"""Guillotine league FAAB bidding strategy.

Integrates with GuillotineScorer to provide survival-aware bidding,
budget planning across weeks, and waiver pool forecasting.
"""

from __future__ import annotations

from dataclasses import dataclass

from ff_tools.draft.rankings import RankedPlayer
from ff_tools.guillotine.strategy import GuillotineScorer


@dataclass
class FAABBid:
    """A FAAB bid recommendation for a player."""

    player: RankedPlayer
    survival_score: float = 0.0
    recommended_bid: int = 0  # percentage of remaining budget (0-100)
    max_bid: int = 0
    priority: int = 0  # 1 = highest priority
    reasoning: str = ""
    survival_impact: float = 0.0  # how much this player improves your score


@dataclass
class BudgetPlan:
    """A weekly budget projection."""

    week: int
    remaining_budget: int
    teams_remaining: int
    projected_pool_quality: str  # "elite", "strong", "average", "weak"
    recommended_spend: int  # recommended % to spend this week
    reasoning: str = ""


@dataclass
class WaiverPoolForecast:
    """Forecast of players expected to hit waivers."""

    position: str
    current_available: int
    projected_releases_per_week: float
    weeks_until_glut: int  # weeks until position becomes deep
    recommendation: str  # "bid now", "wait", "stream"


class GuillotineFAABAnalyzer:
    """FAAB analysis for guillotine leagues.

    Combines survival scoring with FAAB strategy to recommend
    bids that maximize your chances of surviving each week.
    """

    def __init__(
        self,
        scorer: GuillotineScorer,
        total_budget: int = 100,
        current_week: int = 1,
        weeks_remaining: int = 14,
        teams_remaining: int = 12,
        user_survival_rank: int = 12,  # 1 = best, 12 = worst
    ) -> None:
        self.scorer = scorer
        self.total_budget = total_budget
        self.current_week = current_week
        self.weeks_remaining = weeks_remaining
        self.teams_remaining = teams_remaining
        self.user_survival_rank = user_survival_rank

    def survival_gap_multiplier(self) -> float:
        """How urgent is your survival situation?

        Returns a multiplier from 0.5 (safe) to 2.0 (desperate).
        Teams near the bottom should bid aggressively.
        """
        if self.teams_remaining <= 0:
            return 1.0
        safety = 1.0 - ((self.user_survival_rank - 1) / max(1, self.teams_remaining - 1))
        return 0.5 + (1.0 - safety) * 1.5

    def budget_multiplier(self, remaining_budget: int) -> float:
        """Adjust bidding based on remaining budget."""
        pct = remaining_budget / self.total_budget if self.total_budget > 0 else 0
        if pct > 0.7:
            return 1.2
        if pct > 0.3:
            return 1.0
        if pct > 0.15:
            return 0.7
        return 0.4

    def endgame_multiplier(self) -> float:
        """Adjust for late-season dynamics."""
        if self.weeks_remaining <= 3:
            return 1.5
        if self.weeks_remaining <= 6:
            return 1.2
        return 1.0

    def recommend_bid(
        self,
        player: RankedPlayer,
        remaining_budget: int,
        user_roster_positions: dict[str, int] | None = None,
    ) -> FAABBid:
        """Recommend a FAAB bid for a player."""
        player_score = self.scorer.score_player(
            player, user_roster_positions=user_roster_positions
        )
        survival_score = player_score.survival_score

        base_pct = min(60, max(5, int(survival_score / 100 * 60)))

        survival_mult = self.survival_gap_multiplier()
        budget_mult = self.budget_multiplier(remaining_budget)
        endgame_mult = self.endgame_multiplier()
        need_mult = player_score.need_bonus

        final_pct = int(base_pct * survival_mult * budget_mult * endgame_mult * need_mult)
        final_pct = max(1, min(final_pct, 85))

        max_pct = min(final_pct + 20, 95)

        reasoning_parts = []
        if survival_mult > 1.2:
            reasoning_parts.append(
                f"ON THE BUBBLE (rank {self.user_survival_rank}/{self.teams_remaining})"
            )
        elif survival_mult < 0.7:
            reasoning_parts.append(
                f"Safe (rank {self.user_survival_rank}/{self.teams_remaining})"
            )

        if need_mult >= 1.5:
            reasoning_parts.append("CRITICAL need")
        elif need_mult >= 1.15:
            reasoning_parts.append("Depth need")

        if endgame_mult > 1.2:
            reasoning_parts.append(f"Late season ({self.weeks_remaining} wks left)")

        if budget_mult > 1.1:
            reasoning_parts.append("Healthy budget")
        elif budget_mult < 0.8:
            reasoning_parts.append("Low budget - be selective")

        priority_score = survival_score * need_mult
        priority = 1 if priority_score > 50 else 2 if priority_score > 25 else 3

        return FAABBid(
            player=player,
            survival_score=survival_score,
            recommended_bid=final_pct,
            max_bid=max_pct,
            priority=priority,
            reasoning=" | ".join(reasoning_parts) if reasoning_parts else "Standard bid",
            survival_impact=survival_score * 0.1,
        )

    def recommend_all_bids(
        self,
        available_players: list[RankedPlayer],
        remaining_budget: int,
        count: int = 10,
        user_roster_positions: dict[str, int] | None = None,
    ) -> list[FAABBid]:
        """Recommend FAAB bids for top available players."""
        bids = []
        for player in available_players[:count]:
            bid = self.recommend_bid(player, remaining_budget, user_roster_positions)
            bids.append(bid)
        return sorted(bids, key=lambda b: (b.priority, -b.survival_score))

    def plan_budget(
        self,
        remaining_budget: int,
        current_week: int | None = None,
        weeks_remaining: int | None = None,
    ) -> list[BudgetPlan]:
        """Create a weekly budget plan for the rest of the season."""
        week = current_week or self.current_week
        weeks = weeks_remaining or self.weeks_remaining
        plans = []

        for w in range(week, week + weeks):
            weeks_left = (week + weeks) - w

            if weeks_left > 8:
                quality = "weak"
                spend_pct = 10
            elif weeks_left > 5:
                quality = "average"
                spend_pct = 15
            elif weeks_left > 3:
                quality = "strong"
                spend_pct = 20
            else:
                quality = "elite"
                spend_pct = 30

            if self.user_survival_rank > self.teams_remaining * 0.7:
                spend_pct = int(spend_pct * 1.5)
                reasoning = "Aggressive - need to survive"
            elif self.user_survival_rank <= self.teams_remaining * 0.3:
                spend_pct = int(spend_pct * 0.7)
                reasoning = "Conservative - save for endgame"
            else:
                reasoning = "Standard spending pace"

            plans.append(
                BudgetPlan(
                    week=w,
                    remaining_budget=remaining_budget,
                    teams_remaining=self.teams_remaining - (w - week),
                    projected_pool_quality=quality,
                    recommended_spend=min(spend_pct, 40),
                    reasoning=reasoning,
                )
            )

        return plans

    def forecast_pool(self, weeks_ahead: int = 6) -> list[WaiverPoolForecast]:
        """Forecast waiver pool growth by position."""
        projection = self.scorer.get_waiver_projection(weeks_ahead)
        forecasts = []

        for pos, data in projection.items():
            released_per_week = data.get("released_per_week", 0)
            current = data.get("pool_size", 0)

            if released_per_week > 1.5:
                weeks_until_glut = max(1, int(3 / released_per_week))
                recommendation = "wait"
            elif released_per_week > 0.5:
                weeks_until_glut = max(2, int(6 / released_per_week))
                recommendation = "bid now if needed"
            else:
                weeks_until_glut = 99
                recommendation = "bid now - scarce"

            forecasts.append(
                WaiverPoolForecast(
                    position=pos,
                    current_available=current,
                    projected_releases_per_week=released_per_week,
                    weeks_until_glut=weeks_until_glut,
                    recommendation=recommendation,
                )
            )

        return sorted(forecasts, key=lambda f: f.weeks_until_glut)

    def print_analysis(
        self,
        available_players: list[RankedPlayer],
        remaining_budget: int,
        user_roster_positions: dict[str, int] | None = None,
    ) -> None:
        """Print full FAAB analysis."""
        print("\n" + "=" * 70)
        print("  GUILLOTINE FAAB ANALYSIS")
        print(f"  Week {self.current_week} | {self.teams_remaining} teams left | "
              f"Budget: {remaining_budget}/{self.total_budget}")
        print("=" * 70)

        mult = self.survival_gap_multiplier()
        rank_str = f"{self.user_survival_rank}/{self.teams_remaining}"
        if mult > 1.2:
            print(f"\n  WARNING: You're on the bubble (rank {rank_str})")
            print("  Bid aggressively on impact players!")
        elif mult < 0.7:
            print(f"\n  You're safe (rank {rank_str})")
            print("  Be selective - save budget for endgame.")
        else:
            print(f"\n  Middle of the pack (rank {self.user_survival_rank}/{self.teams_remaining})")

        bids = self.recommend_all_bids(
            available_players, remaining_budget, count=10,
            user_roster_positions=user_roster_positions
        )

        print("\n  TOP FAAB TARGETS:")
        print("  " + "-" * 66)
        for bid in bids:
            priority_label = {1: "HIGH", 2: "MED", 3: "LOW"}.get(bid.priority, "LOW")
            print(
                f"    [{priority_label:>3}] {bid.player.display_name:<22s} "
                f"{bid.player.position:<4s} {bid.player.team:<4s} "
                f"| Bid: {bid.recommended_bid:>2d}% (max {bid.max_bid}%) "
                f"| Score: {bid.survival_score:.0f}"
            )
            if bid.reasoning:
                print(f"          {bid.reasoning}")

        plans = self.plan_budget(remaining_budget)
        print("\n  BUDGET PLAN:")
        print("  " + "-" * 66)
        for plan in plans[:8]:
            bar_len = plan.recommended_spend // 2
            bar = "#" * bar_len + "-" * (20 - bar_len)
            print(
                f"    Week {plan.week:>2d}: {plan.remaining_budget:>3d} budget | "
                f"{plan.projected_pool_quality:>7s} pool | "
                f"Spend {plan.recommended_spend:>2d}% [{bar}]"
            )

        forecasts = self.forecast_pool()
        print("\n  WAIVER POOL FORECAST:")
        print("  " + "-" * 66)
        for fc in forecasts:
            if fc.position in ("QB", "RB", "WR", "TE"):
                print(
                    f"    {fc.position:<4s}: {fc.current_available:>2d} available | "
                    f"+{fc.projected_releases_per_week:.1f}/wk | "
                    f"{fc.recommendation}"
                )

        print("\n" + "=" * 70)

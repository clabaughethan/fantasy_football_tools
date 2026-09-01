"""Guillotine-aware draft board with survival-focused recommendations."""

from __future__ import annotations

from dataclasses import dataclass, field

from ff_tools.draft.board import DraftBoard
from ff_tools.draft.rankings import RankedPlayer
from ff_tools.guillotine.strategy import GuillotineScorer, PlayerScore

_TRACKED_POSITIONS = ("QB", "RB", "WR", "TE", "K", "DEF")


@dataclass
class PositionalRun:
    """Tracks a potential positional run in the draft."""

    position: str
    count: int  # how many taken in the window
    window_size: int  # how many picks in the window
    severity: str  # "mild", "moderate", "severe"
    supply_remaining: int = 0  # how many at this position still available
    teams_still_need: int = 0  # how many teams still need a starter here


@dataclass
class GuillotineDraftBoard(DraftBoard):
    """Draft board with guillotine-specific strategy logic.

    Extends the base DraftBoard with:
    - Survival-score-based recommendations (not just raw BPA)
    - Positional run detection
    - Waiver pool projections
    - Floor/ceiling analysis
    """

    scorer: GuillotineScorer | None = None
    user_roster_positions: dict[str, int] = field(default_factory=dict)

    def __post_init__(self) -> None:
        super().__post_init__()
        if self.scorer is None:
            # Size the scarcity model off the actual board rather than assuming
            # a pool, so scarcity moves as the position drains.
            #
            # `total_rounds` is the roster size for this purpose: it is exactly
            # how many players each team drafts, which is what bounds the
            # startable pool. Leaving it at the default 16 put the cut past the
            # end of a 500-player board in a deep league, which is the same as
            # not capping at all.
            self.scorer = GuillotineScorer.from_rankings(
                self.ranked_players,
                total_teams=self.total_teams,
                roster_size=self.total_rounds,
            )

    @property
    def taken_at_position(self) -> dict[str, int]:
        """Drafted counts by position, derived from the pick log."""
        return self.get_taken_at_position()

    def _roster_positions(self) -> dict[str, int]:
        """The user's position counts, explicit override taking precedence."""
        if self.user_roster_positions:
            return self.user_roster_positions
        return self.get_roster_positions()

    # ── Recommendations ─────────────────────────────────────────────

    def get_scored_recommendations(self, count: int = 5) -> list[PlayerScore]:
        """Get scored recommendations with the full survival breakdown.

        Accounts for:
        - Positional scarcity relative to league-wide starter demand
        - Roster need (critical vs depth vs luxury)
        - Floor bonus (consistency over ceiling)
        - Positional value multipliers
        """
        if not self.scorer:
            return []

        scored = self.scorer.score_players(
            ranked_players=self.ranked_players,
            user_roster_positions=self._roster_positions(),
            taken_at_position=self.taken_at_position,
            is_taken=self.is_taken,
        )
        return scored[:count] if count else scored

    def recommend_pick(self, count: int = 5) -> list[RankedPlayer]:
        """Recommend picks ranked by guillotine survival score."""
        if not self.scorer:
            return self.get_best_available(count)
        return [s.ranked_player for s in self.get_scored_recommendations(count)]

    # ── Draft-flow signals ──────────────────────────────────────────

    def detect_positional_runs(self, window: int = 5) -> list[PositionalRun]:
        """Detect if a position is being run on in recent picks.

        A 'run' is several players at one position going in quick succession,
        which is a warning that the position may be depleted before your turn
        comes back around.
        """
        if len(self.picks) < window:
            return []

        recent = self.picks[-window:]
        pos_counts: dict[str, int] = {}
        for pick in recent:
            pos = self.position_of(pick)
            pos_counts[pos] = pos_counts.get(pos, 0) + 1

        supply = self.get_position_supply()
        runs = []
        for pos, count in pos_counts.items():
            if count >= 3:
                severity = "severe" if count >= 4 else "moderate"
            elif count >= 2 and window >= 4:
                severity = "mild"
            else:
                continue

            info = supply.get(pos, {})
            runs.append(
                PositionalRun(
                    position=pos,
                    count=count,
                    window_size=window,
                    severity=severity,
                    supply_remaining=info.get("startable_remaining", 0),
                    teams_still_need=info.get("teams_without", 0),
                )
            )

        severity_order = {"severe": 0, "moderate": 1, "mild": 2}
        runs.sort(key=lambda r: (severity_order.get(r.severity, 9), -r.count))
        return runs

    def get_floor_analysis(self, count: int = 5) -> list[dict]:
        """Floor/ceiling labels for the top recommended players.

        Uses the same survival ordering as `recommend_pick` so the labels
        describe the players actually being suggested.
        """
        if self.scorer:
            candidates = [s.ranked_player for s in self.get_scored_recommendations(count)]
        else:
            candidates = self.get_best_available(count)

        analysis = []
        for rp in candidates:
            rank = rp.rank
            if rank <= 24:
                floor_label, ceiling_label = "ELITE", "CEILING"
            elif rank <= 48:
                floor_label, ceiling_label = "HIGH", "HIGH"
            elif rank <= 96:
                floor_label, ceiling_label = "SOLID", "MODERATE"
            elif rank <= 150:
                floor_label, ceiling_label = "RISKY", "BOOM/BUST"
            else:
                floor_label, ceiling_label = "LOW", "LOTTERY"

            analysis.append(
                {
                    "player": rp,
                    "floor": floor_label,
                    "ceiling": ceiling_label,
                }
            )
        return analysis

    # ── Supply and depth ────────────────────────────────────────────

    def _available_by_position(self) -> dict[str, list[RankedPlayer]]:
        """Group the remaining player pool by position in one pass."""
        grouped: dict[str, list[RankedPlayer]] = {}
        for rp in self.ranked_players:
            if self.is_taken(rp):
                continue
            grouped.setdefault(rp.position or "UNK", []).append(rp)
        return grouped

    def _starters_by_team(self, position: str) -> dict[int, int]:
        """Count each team's drafted players at a position."""
        counts: dict[int, int] = {}
        for pick in self.picks:
            if self.position_of(pick) != position:
                continue
            counts[pick.roster_id] = counts.get(pick.roster_id, 0) + 1
        return counts

    def get_positional_depth(self) -> dict[str, dict]:
        """Remaining players by tier at each position.

        Critical in deep leagues where the gap between WR1 and WR4 is enormous.
        """
        grouped = self._available_by_position()
        starters = self.scorer.starters if self.scorer else {}

        depth = {}
        for pos in _TRACKED_POSITIONS:
            players = grouped.get(pos, [])
            top = sum(1 for rp in players if rp.tier and rp.tier <= 2)
            mid = sum(1 for rp in players if rp.tier and 2 < rp.tier <= 4)
            low = len(players) - top - mid

            starters_needed = starters.get(pos, 1) * self.total_teams
            depth[pos] = {
                "total_remaining": len(players),
                "top_tier": top,
                "mid_tier": mid,
                "low_tier": low,
                "starters_needed": starters_needed,
                "shortage": max(0, starters_needed - len(players)),
            }
        return depth

    def get_position_supply(self) -> dict[str, dict]:
        """Supply vs demand at each position.

        The key draft-strategy metric: how many startable players remain
        relative to how many teams still need one.
        """
        grouped = self._available_by_position()
        taken_counts = self.taken_at_position
        starters = self.scorer.starters if self.scorer else {}

        supply = {}
        for pos in _TRACKED_POSITIONS:
            players = grouped.get(pos, [])
            startable = sum(1 for rp in players if rp.tier and rp.tier <= 2)

            required = starters.get(pos, 1)
            per_team = self._starters_by_team(pos)
            teams_with = sum(1 for count in per_team.values() if count >= required)
            # Teams yet to draft here are assumed to still need the position.
            teams_without = max(0, self.total_teams - teams_with)

            supply[pos] = {
                "total_remaining": len(players),
                "startable_remaining": startable,
                "taken": taken_counts.get(pos, 0),
                "teams_with": teams_with,
                "teams_without": teams_without,
                "starters_needed_per_team": required,
            }
        return supply

    def get_waiver_projection(self, weeks_remaining: int = 14) -> dict[str, dict]:
        """Project waiver pool growth over the remaining weeks."""
        if not self.scorer:
            return {}
        return self.scorer.get_waiver_projection(weeks_remaining=weeks_remaining)

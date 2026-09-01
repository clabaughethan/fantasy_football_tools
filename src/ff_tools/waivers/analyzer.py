"""Core waiver wire analysis - FA rankings, drop recommendations, FAAB bids."""

from __future__ import annotations

from dataclasses import dataclass, replace

from ff_tools.draft.rankings import RankedPlayer
from ff_tools.models.player import Player
from ff_tools.utils.names import match_key

# How many ranks better a free agent must be before dropping someone for them.
DEFAULT_UPGRADE_THRESHOLD = 10

# Minimum bodies to keep at each position so a "drop" never leaves the lineup
# unfillable. Dropping your only quarterback is not an upgrade.
MIN_ROSTERED: dict[str, int] = {
    "QB": 1,
    "RB": 2,
    "WR": 2,
    "TE": 1,
    "K": 1,
    "DEF": 1,
}


@dataclass
class FAABRecommendation:
    """FAAB bid recommendation for a player."""

    player: RankedPlayer
    recommended_bid: int  # percentage of remaining budget (0-100)
    max_bid: int
    reasoning: str = ""


@dataclass
class DropRecommendation:
    """Recommendation to drop a player for a waiver pickup."""

    drop_player: Player
    pickup_player: RankedPlayer
    reasoning: str = ""
    upgrade_rank: int = 0  # how many ranks better the pickup is


@dataclass
class WaiverTarget:
    """A recommended waiver wire pickup."""

    player: RankedPlayer
    faab: FAABRecommendation | None = None
    trending_adds: int = 0
    trending_drops: int = 0
    reasoning: str = ""


class WaiverAnalyzer:
    """Analyze waiver wire options for a league.

    Works with both Sleeper and ESPN by accepting pre-fetched data.
    """

    def __init__(self, total_budget: int = 100) -> None:
        self.total_budget = total_budget

    def rank_free_agents(
        self,
        available_players: list[Player],
        rankings: list[RankedPlayer],
    ) -> list[RankedPlayer]:
        """Rank available free agents against consensus rankings.

        Returns available players sorted by their rank in the consensus list.
        Matching goes through the player ID first and the normalized name key
        second, because the roster/free-agent feed and the rankings feed are
        usually different providers with unrelated ID spaces.

        A match keeps the *platform's* player object and borrows only the
        consensus rank. Substituting the rankings provider's player would throw
        away the ID the caller needs to actually place the claim - and which
        trending-add counts are keyed by.
        """
        by_id, by_key = _index_rankings(rankings)

        unranked_rank = _unranked_rank(rankings)
        ranked_available: list[RankedPlayer] = []
        for player in available_players:
            found = _lookup(player, by_id, by_key)
            if found is not None:
                ranked_available.append(replace(found, player=player))
            else:
                # Unranked players (just-promoted, newly signed) sort to the back
                # rather than being dropped from the board entirely.
                ranked_available.append(
                    RankedPlayer(
                        rank=unranked_rank,
                        player=player,
                        tier=6,
                    )
                )

        return sorted(ranked_available, key=lambda rp: rp.rank)

    def find_drop_candidates(
        self,
        roster_players: list[Player],
        available_players: list[RankedPlayer],
        rankings: list[RankedPlayer] | None = None,
        upgrade_threshold: int = DEFAULT_UPGRADE_THRESHOLD,
        min_rostered: dict[str, int] | None = None,
    ) -> list[DropRecommendation]:
        """Compare roster players to available FAs and suggest upgrades.

        `rankings` is the full consensus list used to rank the *roster*. It
        matters: a rostered player is by definition not a free agent, so looking
        their rank up in `available_players` can only ever miss, which is what
        made this return nothing at all. Falling back to `available_players`
        keeps the old call signature working, just without roster ranks.

        A drop is only proposed when the position keeps enough bodies to fill
        the lineup afterwards. Drops and pickups are paired off within each
        position - worst rostered against best available, then second worst
        against second best - so a free agent is never offered twice.
        """
        pool = rankings if rankings is not None else available_players
        by_id, by_key = _index_rankings(pool)
        limits = min_rostered if min_rostered is not None else MIN_ROSTERED

        # Unranked roster players are the most droppable, not the least, so give
        # them a rank past the end of the board instead of skipping them.
        unranked_rank = _unranked_rank(pool)

        # Rank every roster player and group by position.
        roster_by_pos: dict[str, list[tuple[int, bool, Player]]] = {}
        for player in roster_players:
            pos = player.position or "UNK"
            found = _lookup(player, by_id, by_key)
            rank = found.rank if found is not None else unranked_rank
            roster_by_pos.setdefault(pos, []).append(
                (rank, found is not None, player)
            )

        available_by_pos: dict[str, list[RankedPlayer]] = {}
        for rp in sorted(available_players, key=lambda r: r.rank):
            available_by_pos.setdefault(rp.position or "UNK", []).append(rp)

        recommendations = []
        for pos, held in roster_by_pos.items():
            candidates = available_by_pos.get(pos)
            if not candidates:
                continue

            # How many can be dropped before the lineup can no longer be filled.
            droppable = len(held) - limits.get(pos, 0)
            if droppable <= 0:
                continue

            # Worst rostered first, best available first.
            worst_first = sorted(held, key=lambda item: -item[0])[:droppable]

            # Shortest wins: whichever side runs out first ends the pairing.
            for (rank, was_ranked, player), pickup in zip(
                worst_first, candidates, strict=False
            ):
                if pickup.rank >= rank - upgrade_threshold:
                    # Sorted both ways, so nothing further down can qualify.
                    break
                held_label = f"#{rank}" if was_ranked else "unranked"
                recommendations.append(
                    DropRecommendation(
                        drop_player=player,
                        pickup_player=pickup,
                        reasoning=(
                            f"{pickup.display_name} is ranked #{pickup.rank} vs "
                            f"{player.full_name} at {held_label}"
                        ),
                        upgrade_rank=rank - pickup.rank,
                    )
                )

        return sorted(recommendations, key=lambda r: -r.upgrade_rank)

    def recommend_faab(
        self,
        player: RankedPlayer,
        remaining_budget: int,
        is_starved_at_position: bool = False,
    ) -> FAABRecommendation:
        """Recommend a FAAB bid for a player.

        Based on player tier, remaining budget, and positional need. Bids are a
        percentage of the total budget, so league size does not enter into it -
        this used to take a `league_size` argument and ignore it, which is worse
        than not offering the knob.
        """
        rank = player.rank
        tier = player.tier

        # Base bid by tier
        if tier == 1:
            base_pct = 60
        elif tier == 2:
            base_pct = 35
        elif tier == 3:
            base_pct = 20
        elif tier == 4:
            base_pct = 10
        else:
            base_pct = 5

        # Adjust for positional scarcity
        if is_starved_at_position:
            base_pct = min(base_pct + 15, 80)

        # Adjust for remaining budget
        budget_pct_of_total = remaining_budget / self.total_budget
        if budget_pct_of_total < 0.3:
            # Low budget: be more conservative
            base_pct = int(base_pct * 0.7)
        elif budget_pct_of_total > 0.7:
            # High budget: can be more aggressive
            base_pct = int(base_pct * 1.2)

        # Cap at reasonable limits
        recommended = max(1, min(base_pct, 80))
        max_bid = min(recommended + 15, 100)

        # Build reasoning
        reasoning = f"Tier {tier} player (rank #{rank})"
        if is_starved_at_position:
            reasoning += ", high positional need"
        if budget_pct_of_total < 0.3:
            reasoning += ", limited budget remaining"

        return FAABRecommendation(
            player=player,
            recommended_bid=recommended,
            max_bid=max_bid,
            reasoning=reasoning,
        )

    def build_target_list(
        self,
        available_players: list[Player],
        rankings: list[RankedPlayer],
        remaining_budget: int = 100,
        positions_needed: list[str] | None = None,
        trending_adds: dict[str, int] | None = None,
        trending_drops: dict[str, int] | None = None,
    ) -> list[WaiverTarget]:
        """Build a prioritized list of waiver targets.

        Combines rankings, trending data, and FAAB recommendations.
        """
        ranked = self.rank_free_agents(available_players, rankings)

        # Build trending lookup
        adds = trending_adds or {}
        drops = trending_drops or {}

        targets = []
        for rp in ranked[:30]:  # Top 30 available
            trending_a = adds.get(rp.player.player_id, 0)
            trending_d = drops.get(rp.player.player_id, 0)

            # Determine if position is needed
            need = False
            if positions_needed:
                need = rp.position in positions_needed

            faab = self.recommend_faab(
                rp, remaining_budget, is_starved_at_position=need
            )

            # Build reasoning
            reasoning_parts = [f"Rank #{rp.rank}"]
            if trending_a > 50:
                reasoning_parts.append(f"+{trending_a} adds (trending)")
            if need:
                reasoning_parts.append(f"Fills {rp.position} need")

            targets.append(
                WaiverTarget(
                    player=rp,
                    faab=faab,
                    trending_adds=trending_a,
                    trending_drops=trending_d,
                    reasoning=" | ".join(reasoning_parts),
                )
            )

        # Sort by a composite score: rank + trending boost
        def _target_score(t: WaiverTarget) -> float:
            score = t.player.rank
            # Trending adds reduce effective rank (more desirable)
            score -= t.trending_adds * 0.1
            # Positional need boost
            if t.faab and "Fills" in (t.faab.reasoning or ""):
                score -= 5
            return score

        return sorted(targets, key=_target_score)


def _unranked_rank(rankings: list[RankedPlayer]) -> int:
    """A rank sentinel guaranteed to sort behind everyone on the board.

    Taken from the worst rank present rather than the list length: a rankings
    list is not always a dense 1..N: a positional or filtered list can hold 40
    players whose ranks run into the hundreds, and `len + 1` would then rate an
    unknown player ahead of most of the board.
    """
    return max((rp.rank for rp in rankings), default=0) + 1


def _index_rankings(
    rankings: list[RankedPlayer],
) -> tuple[dict[str, RankedPlayer], dict[str, RankedPlayer]]:
    """Build ID and name-key lookups over a rankings list, best rank winning.

    Sorted first so a duplicate resolves to the better rank whatever order the
    provider returned - `setdefault` alone would only do that for a list that
    already happened to arrive in rank order.
    """
    by_id: dict[str, RankedPlayer] = {}
    by_key: dict[str, RankedPlayer] = {}
    for rp in sorted(rankings, key=lambda r: r.rank):
        pid = rp.player.player_id
        if pid:
            by_id.setdefault(pid, rp)
        key = rp.match_key
        if key:
            by_key.setdefault(key, rp)
    return by_id, by_key


def _lookup(
    player: Player,
    by_id: dict[str, RankedPlayer],
    by_key: dict[str, RankedPlayer],
) -> RankedPlayer | None:
    """Find a player in the rankings by ID, then by normalized name."""
    if player.player_id:
        found = by_id.get(player.player_id)
        if found is not None:
            return found
    key = match_key(player.full_name, player.position, player.team)
    if key:
        return by_key.get(key)
    return None

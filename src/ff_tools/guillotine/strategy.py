"""Core guillotine league strategy engine.

Computes guillotine-specific player valuations that prioritize survival
(avoiding the lowest weekly score) over ceiling (chasing the highest score).

Scoring is rank-based, not projection-based. The survival score is:
    score = rank_score * floor * position_value * scarcity * need

Where rank_score = 1000 / rank (so rank 1 = 1000, rank 10 = 100, etc.)
"""

from __future__ import annotations

from dataclasses import dataclass

from ff_tools.draft.rankings import RankedPlayer
from ff_tools.models.player import Player

# Positional value multipliers for deep guillotine leagues.
#
# These are hand-tuned priors, NOT computed from data. The reasoning is that a
# position's value tracks how much worse the replacement-level starter is than
# the best one, discounted by how easily the position can be streamed off
# waivers:
#   QB:  0.65x - very streamable, the 32nd QB is still a usable starter
#   RB:  1.04x - moderate scarcity, comparable to WR depth
#   WR:  1.05x - deep position, the reference point
#   TE:  1.00x - scarcity is measured independently by estimate_position_scarcity
#   K:   0.50x - pure stream, high variance
#   DEF: 0.50x - pure stream, matchup dependent
#
# TE's prior was 1.25x to reflect scarcity, but scarcity is now measured
# independently at ~1.25 for a 32-team league. Combining the two double-counted
# the signal. Setting the prior to 1.0 lets scarcity do the work alone.
#
# The three streaming discounts are a *shallow-league* baseline, not a constant:
# `GuillotineScorer._position_value` rescales them against the league's real
# waiver depth via `streaming_discount`, because "streamable" stops being true
# once a league is deep enough to draft the whole startable pool at a position.
#
# `ff_tools.guillotine.analysis.compute_positional_values` derives the same
# multipliers from live projections; pass its output to
# `GuillotineScorer(position_multipliers=...)` to use measured values instead.
POSITION_MULTIPLIER: dict[str, float] = {
    "QB": 0.65,
    "RB": 1.04,
    "WR": 1.05,
    "TE": 1.00,
    "K": 0.50,
    "DEF": 0.50,
}

# Roster need multipliers
NEED_CRITICAL = 1.50  # Below the required number of starters
NEED_DEPTH = 1.15  # Exactly enough starters, no backup
NEED_LUXURY = 1.00  # Starters plus depth already rostered

# Fallback starting lineup, used only when the league's real settings are
# unavailable. Prefer `GuillotineScorer(starters=draft.roster_slots)`.
DEFAULT_STARTERS: dict[str, int] = {
    "QB": 1,
    "RB": 2,
    "WR": 2,
    "TE": 1,
    "FLEX": 1,  # RB/WR/TE
    "K": 1,
    "DEF": 1,
}

# Positions a FLEX slot can be filled from.
FLEX_ELIGIBLE = ("RB", "WR", "TE")

# Positions cheap enough to replace off waivers week to week.
STREAMABLE = frozenset({"QB", "K", "DEF"})

# Approximate size of the *draft-relevant* player pool by position - roughly a
# 300-player consensus board. This is deliberately not the whole NFL: comparing
# 1,366 rostered NFL wide receivers against 64 starter slots makes every
# position look like a surplus and flattens the scarcity signal to a constant.
# `GuillotineScorer.from_rankings` replaces these with the real pool counts.
DEFAULT_POOL_SIZES: dict[str, int] = {
    "QB": 32,
    "RB": 75,
    "WR": 105,
    "TE": 35,
    "K": 20,
    "DEF": 25,
}


@dataclass
class PlayerScore:
    """Guillotine-adjusted score for a player."""

    ranked_player: RankedPlayer
    rank_score: float
    floor_bonus: float
    position_value: float
    need_bonus: float
    scarcity_bonus: float
    survival_score: float

    @property
    def player(self) -> Player:
        return self.ranked_player.player

    @property
    def position(self) -> str:
        return self.ranked_player.position

    @property
    def display_name(self) -> str:
        return self.ranked_player.display_name


def rank_to_score(rank: int) -> float:
    """Convert rank to a score for survival calculation.

    Uses inverse square root: rank 1 = 1000, rank 10 = 316, rank 100 = 100.
    This is less steep than the previous 1/rank curve (which gave rank 27 only
    3.7% of the top value). The sqrt decay better matches published draft-pick
    value charts where the dropoff from pick 1 to pick 10 is meaningful but
    not catastrophic. The multipliers (floor, scarcity, need) do the real work.
    """
    if rank <= 0:
        return 0.0
    return 1000.0 / (rank ** 0.5)


def floor_bonus_from_rank(rank: int) -> float:
    """Estimate floor bonus from rank position.

    Higher-ranked players (top tiers) tend to be more consistent.
    Mid-tier players have more variance. Late-tier players are volatile.

    Returns a multiplier between 0.85 and 1.15.
    """
    if rank <= 12:
        return 1.15  # Elite - very consistent
    if rank <= 24:
        return 1.10  # Strong starter - reliable
    if rank <= 48:
        return 1.05  # Solid starter - mostly consistent
    if rank <= 72:
        return 1.00  # Average starter
    if rank <= 120:
        return 0.95  # Flex/bench - some variance
    if rank <= 200:
        return 0.90  # Depth - volatile
    return 0.85  # Deep bench - very volatile


def positional_demand(
    starters: dict[str, int] | None = None,
    total_teams: int = 32,
) -> dict[str, float]:
    """League-wide starter demand per position, including flex slots.

    FLEX slots are spread across the flex-eligible positions in proportion to
    their dedicated starter counts, so a league starting RB2/WR2/TE1 with two
    flex spots attributes 0.8 of that flex demand to RB, 0.8 to WR and 0.4 to
    TE. Fractional demand is fine - it feeds a ratio, not a roster.
    """
    slots = dict(starters or DEFAULT_STARTERS)
    flex_count = slots.pop("FLEX", 0)
    # A superflex is usually filled with a quarterback, so its demand is
    # attributed there rather than spread across the flex-eligible positions.
    superflex_count = slots.pop("SUPER_FLEX", 0)
    # Bench and IDP slots create no demand on the offensive starter pool.
    slots.pop("BN", None)
    slots.pop("IDP_FLEX", None)

    base_flex_total = sum(slots.get(pos, 0) for pos in FLEX_ELIGIBLE)

    demand: dict[str, float] = {}
    for pos, count in slots.items():
        demand[pos] = float(count) * total_teams

    if superflex_count:
        demand["QB"] = demand.get("QB", 0.0) + superflex_count * total_teams

    if flex_count and base_flex_total:
        for pos in FLEX_ELIGIBLE:
            share = slots.get(pos, 0) / base_flex_total
            demand[pos] = demand.get(pos, 0.0) + flex_count * share * total_teams
    elif flex_count:
        # No dedicated flex-eligible starters; split the flex slots evenly.
        for pos in FLEX_ELIGIBLE:
            demand[pos] = demand.get(pos, 0.0) + (
                flex_count / len(FLEX_ELIGIBLE) * total_teams
            )

    return demand


def startable_depth(
    ranked_players: list[RankedPlayer],
    total_teams: int,
    roster_size: int,
) -> dict[str, int]:
    """Count players by position within the part of the board that gets drafted.

    A rankings board is much longer than any league drafts. Counting all of it
    as "supply" is what made the scarcity signal inert: a 500-player board holds
    49 quarterbacks, so supply appeared to beat a 32-team league's demand of 32
    even though the 49th quarterback is a career backup nobody starts. Every
    position then landed in the surplus bucket and the shortage buckets - where
    this model's actual judgement lives - never fired.

    The cut is `total_teams * roster_size`: the number of players who come off
    the board at all. This is a proxy for startability, not a measurement of it,
    but it is a league-derived one, so it tightens as a league gets deeper
    instead of needing a hand-tuned constant per league size.
    """
    if total_teams <= 0 or roster_size <= 0:
        return {}
    cut = total_teams * roster_size
    counts: dict[str, int] = {}
    # By rank rather than list order: a board is not guaranteed to arrive
    # sorted, and taking the first N of an unsorted list would sample randomly.
    for rp in sorted(ranked_players, key=lambda r: r.rank)[:cut]:
        if rp.position:
            counts[rp.position] = counts.get(rp.position, 0) + 1
    return counts


def estimate_position_scarcity(
    position: str,
    taken_count: int,
    total_at_position: int,
    teams_remaining: int,
    starters_needed: float | None = None,
    startable_total: int | None = None,
) -> float:
    """Scarcity multiplier from remaining supply vs league-wide starter demand.

    `starters_needed` is the per-team starter requirement at this position; when
    omitted it falls back to `DEFAULT_STARTERS`. Callers that know the league's
    real lineup should pass flex-aware demand from `positional_demand`.

    `startable_total` caps the supply that counts toward the ratio at the
    startable part of the pool (see `startable_depth`). Without it, the deep
    tail of a long board masks a genuine shortage. It is applied as a separate
    clamp rather than by shrinking `total_at_position`, because `taken_count` is
    scoped to the whole board - subtracting it from a shrunken total would mix
    two populations and understate what is left.

    Returns a multiplier between 0.85 and 1.5.
    """
    remaining = total_at_position - taken_count
    if startable_total is not None:
        remaining = min(remaining, startable_total - taken_count)
    if remaining <= 0:
        return 1.5  # Position is run out

    per_team = (
        starters_needed
        if starters_needed is not None
        else DEFAULT_STARTERS.get(position, 1)
    )
    total_starters_needed = teams_remaining * per_team

    if total_starters_needed <= 0:
        return 1.0
    ratio = remaining / total_starters_needed

    # Supply-to-demand ratio, bucketed. ratio 1.0 means supply exactly meets
    # the league's starter demand.
    if ratio < 0.3:
        return 1.5  # Extreme shortage (position nearly exhausted)
    if ratio < 0.5:
        return 1.4  # Severe shortage
    if ratio < 0.75:
        return 1.25  # Moderate shortage
    if ratio < 1.0:
        return 1.10 + (1.0 - ratio) * 0.4  # Slight shortage (1.10 to 1.22)
    if ratio < 1.5:
        return 1.0  # Adequate supply
    if ratio < 2.0:
        return 0.95  # Surplus
    return 0.85  # Large surplus


def streaming_discount(
    base_multiplier: float,
    startable_remaining: int,
    league_demand: float,
    total_teams: int,
) -> float:
    """Scale a streamable position's discount by whether streaming is possible.

    `POSITION_MULTIPLIER` discounts QB, K and DEF because they can be replaced
    off waivers week to week. That is a claim about the waiver wire, not about
    the position, and it silently stops being true in a deep league: 32 teams
    starting one quarterback each draft 32 of roughly 35 startable ones, leaving
    a waiver pool of three. Applying a streaming discount there marks a position
    down for a flexibility the league does not offer.

    So the discount is interpolated against spare startable supply per team:
    one spare per team means streaming works and the full discount applies;
    no spares means no discount at all.
    """
    if total_teams <= 0:
        return base_multiplier
    spare_per_team = max(0.0, startable_remaining - league_demand) / total_teams
    reach = min(1.0, spare_per_team)
    return base_multiplier + (1.0 - base_multiplier) * (1.0 - reach)


def compute_need_bonus(
    position: str,
    user_roster_positions: dict[str, int],
    starters: dict[str, int] | None = None,
) -> float:
    """Compute roster need multiplier.

    user_roster_positions: dict mapping position -> count of players on roster.
    e.g. {"QB": 1, "RB": 3, "WR": 4, "TE": 1, "K": 1, "DEF": 1}
    """
    slots = starters or DEFAULT_STARTERS
    count = user_roster_positions.get(position, 0)
    required = slots.get(position, 1)

    if count < required:
        return NEED_CRITICAL
    if count <= required:
        return NEED_DEPTH
    return NEED_LUXURY


class GuillotineScorer:
    """Compute guillotine-adjusted survival scores for players.

    The survival score determines how likely a player helps you avoid
    being the lowest scorer in a given week. This prioritizes high-floor,
    consistent players over boom/bust options.
    """

    def __init__(
        self,
        total_teams: int = 32,
        position_totals: dict[str, int] | None = None,
        roster_size: int = 16,
        starters: dict[str, int] | None = None,
        position_multipliers: dict[str, float] | None = None,
        startable_totals: dict[str, int] | None = None,
    ) -> None:
        self.total_teams = total_teams
        self.roster_size = roster_size
        self.starters = dict(starters) if starters else dict(DEFAULT_STARTERS)
        self.position_multipliers = dict(position_multipliers or POSITION_MULTIPLIER)
        # Size of the draft-relevant pool by position.
        self.position_totals = dict(position_totals or DEFAULT_POOL_SIZES)
        # The startable slice of that pool. Empty when the caller did not build
        # from a board, in which case scarcity and the streaming discount both
        # fall back to their unclamped behaviour rather than guessing a depth.
        self.startable_totals = dict(startable_totals or {})
        self.demand = positional_demand(self.starters, self.total_teams)

    @classmethod
    def from_rankings(
        cls,
        ranked_players: list[RankedPlayer],
        total_teams: int = 32,
        roster_size: int = 16,
        starters: dict[str, int] | None = None,
        position_multipliers: dict[str, float] | None = None,
    ) -> GuillotineScorer:
        """Build a scorer whose pool sizes come from the actual rankings list.

        Scarcity is only meaningful relative to the players genuinely in play,
        so the pool is measured from the board rather than assumed.
        """
        totals: dict[str, int] = {}
        for rp in ranked_players:
            pos = rp.position
            if pos:
                totals[pos] = totals.get(pos, 0) + 1
        return cls(
            total_teams=total_teams,
            position_totals=totals or None,
            roster_size=roster_size,
            starters=starters,
            position_multipliers=position_multipliers,
            startable_totals=startable_depth(
                ranked_players, total_teams, roster_size
            ),
        )

    def _per_team_demand(self, position: str) -> float:
        """Per-team starter demand at a position, flex included."""
        if self.total_teams <= 0:
            return float(self.starters.get(position, 1))
        return self.demand.get(position, 0.0) / self.total_teams

    def _startable_total(self, position: str) -> int | None:
        """The startable pool depth at a position, or None when unknown."""
        return self.startable_totals.get(position)

    def _position_value(self, position: str) -> float:
        """Positional multiplier, with any streaming discount checked for reality.

        RB/WR/TE keep their hand-tuned priors. The streamable positions get
        their discount rescaled to the league's actual waiver depth, so the
        same constant behaves correctly in a 12-team and a 32-team league.
        """
        base = self.position_multipliers.get(position, 1.0)
        if position not in STREAMABLE or base >= 1.0:
            return base
        depth = self._startable_total(position)
        if depth is None:
            return base
        return streaming_discount(
            base, depth, self.demand.get(position, 0.0), self.total_teams
        )

    def score_player(
        self,
        ranked: RankedPlayer,
        taken_at_position: dict[str, int] | None = None,
        user_roster_positions: dict[str, int] | None = None,
    ) -> PlayerScore:
        """Compute the guillotine survival score for a single player."""
        pos = ranked.position
        taken = (taken_at_position or {}).get(pos, 0)

        base = rank_to_score(ranked.rank)
        floor = floor_bonus_from_rank(ranked.rank)
        pos_mult = self._position_value(pos)
        scarcity = estimate_position_scarcity(
            pos,
            taken,
            self.position_totals.get(pos, 0),
            self.total_teams,
            starters_needed=self._per_team_demand(pos),
            startable_total=self._startable_total(pos),
        )
        need = compute_need_bonus(
            pos, user_roster_positions or {}, starters=self.starters
        )

        return PlayerScore(
            ranked_player=ranked,
            rank_score=base,
            floor_bonus=floor,
            position_value=pos_mult,
            need_bonus=need,
            scarcity_bonus=scarcity,
            survival_score=base * floor * pos_mult * scarcity * need,
        )

    def score_players(
        self,
        ranked_players: list[RankedPlayer],
        taken_player_ids: set[str] | None = None,
        user_roster_positions: dict[str, int] | None = None,
        taken_at_position: dict[str, int] | None = None,
        is_taken=None,
    ) -> list[PlayerScore]:
        """Score all available players and return them sorted by survival score.

        Availability is decided by `is_taken` when given (the board passes its
        name-aware check), otherwise by membership in `taken_player_ids`.
        """
        taken_ids = taken_player_ids or set()
        results = []

        for ranked in ranked_players:
            if is_taken is not None:
                if is_taken(ranked):
                    continue
            elif ranked.player.player_id in taken_ids:
                continue

            results.append(
                self.score_player(
                    ranked,
                    taken_at_position=taken_at_position,
                    user_roster_positions=user_roster_positions,
                )
            )

        results.sort(key=lambda s: s.survival_score, reverse=True)
        return results

    def get_waiver_projection(self, weeks_remaining: int = 14) -> dict[str, dict]:
        """Project how many players at each position will hit waivers.

        In a guillotine league one team is eliminated per week and its entire
        roster is released, so the waiver pool grows by roughly one roster per
        week. Releases are attributed to positions in proportion to the league's
        starter demand, which is the best available proxy for how teams actually
        build rosters.
        """
        total_demand = sum(self.demand.values())
        projections: dict[str, dict] = {}

        for pos, total in self.position_totals.items():
            share = (self.demand.get(pos, 0.0) / total_demand) if total_demand else 0.0
            per_chop = self.roster_size * share
            total_released = int(round(per_chop * max(0, weeks_remaining)))

            projections[pos] = {
                "pool_size": total,
                "released_per_week": round(per_chop, 2),
                "projected_released": total_released,
                "available_later": total + total_released,
                "streamable": pos in STREAMABLE,
            }
        return projections

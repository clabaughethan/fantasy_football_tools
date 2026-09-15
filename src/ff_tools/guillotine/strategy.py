"""Core guillotine league strategy engine.

Computes guillotine-specific player valuations that prioritize survival
(avoiding the lowest weekly score) over ceiling (chasing the highest score).

Scoring is rank-based, not projection-based. The survival score is:
    score = rank_score * floor * position_value * scarcity * need * availability

Where rank_score = 1000 / rank (so rank 1 = 1000, rank 10 = 100, etc.)
"""

from __future__ import annotations

from dataclasses import dataclass

from ff_tools.draft.rankings import RankedPlayer
from ff_tools.guillotine.injury import InjuryAssessment
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

# Probability a player with each designation is on the field, keyed on the
# uppercased value Sleeper publishes in `injury_status`.
#
# These are probabilities with a real referent, not value multipliers - which is
# what the previous version of this table conflated. Questionable players
# historically suit up around three times in four; Doubtful is roughly the
# inverse. Keeping this a probability is what lets the value question (how much
# does missing him actually cost?) be answered separately, from the league's own
# replacement depth, instead of being folded into the same hand-tuned number.
INJURY_PLAY_PROBABILITY: dict[str, float] = {
    "QUESTIONABLE": 0.75,
    "COV": 0.60,
    "DOUBTFUL": 0.28,
    "OUT": 0.02,
    "NA": 0.05,  # not active / non-football injury
    "PUP": 0.05,
    "SUS": 0.02,  # suspended
    "SUSPENDED": 0.02,
    "IR": 0.02,
    "DNR": 0.02,  # did not report
}

# Designations that mean "healthy" rather than "no data". ESPN publishes
# "ACTIVE" where Sleeper publishes null.
_HEALTHY_DESIGNATIONS = frozenset({"", "ACTIVE", "PROBABLE", "HEALTHY"})

# Designations describing a player expected back shortly, for whom a waiver
# pickup is a genuine one-week patch. "Out" belongs here: on Sleeper it means out
# for this game, not gone for the season. The rest describe a stretch absence,
# which is a different proposition - it ties up a roster spot rather than costing
# a week - and that is what separates an Out player from one on IR.
_NEAR_TERM_DESIGNATIONS = frozenset({"QUESTIONABLE", "DOUBTFUL", "COV", "OUT"})

# Roster standings that override the game-day designation. A player on IR is
# unavailable whether or not this week's injury report mentions him.
_UNAVAILABLE_ROSTER_STATUS: dict[str, str] = {
    "INJURED RESERVE": "IR",
    "PHYSICALLY UNABLE TO PERFORM": "PUP",
    "NON FOOTBALL INJURY": "NA",
    "SUSPENDED": "SUS",
}

# How close to full a player is expected to be *if* he takes the field, by body
# part. This is the question the designation cannot answer and the one that
# matters for a player who is active but limited.
#
# Matched as substrings against the uppercased body part, because Sleeper
# qualifies the joint: "Knee - ACL", "Knee - ACL + MCL", "Knee - PCL".
# Structural is checked first so "Knee - ACL" reads as a repaired ligament rather
# than as generic knee soreness.
SEVERE_INJURY_PARTS = ("ACL", "PCL", "ACHILLES", "LISFRANC", "SPINE")
LINGERING_INJURY_PARTS = (
    "MCL", "KNEE", "HAMSTRING", "GROIN", "CALF", "QUADRICEPS", "THIGH", "FOOT",
    "ANKLE", "HEEL", "OBLIQUE", "ABDOMEN", "BACK", "NECK", "HIP",
    "PECTORAL", "BICEPS", "LEG", "LOWER BODY",
)
# Things that resolve in days and leave no limitation behind. A concussion
# belongs here rather than with the soft-tissue injuries: protocol makes it a
# binary on whether he plays, but a cleared player is not a limited one.
MINOR_INJURY_PARTS = (
    "ILLNESS", "REST", "PERSONAL", "THUMB", "HAND", "FINGER", "CONCUSSION",
    "HEAD",
)

_SEVERE_EFFECTIVENESS = 0.80
_LINGERING_EFFECTIVENESS = 0.90
_UNKNOWN_EFFECTIVENESS = 0.95
_MINOR_EFFECTIVENESS = 0.98

# Replacement quality assumed when there is no board to measure it from. Halfway
# is a deliberate non-answer: without the board, positional depth is unknown.
DEFAULT_REPLACEMENT_RATIO = 0.5


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
    # 1.0 for a player with no injury designation; below 1.0 when one is
    # published. Defaulted so callers constructing a score by hand keep working.
    availability: float = 1.0
    # What a replacement would be worth if this player missed a game, in [0, 1].
    # Reported alongside availability because it is what makes the penalty large
    # or small, and a bare multiplier hides that reasoning.
    replacement_ratio: float = DEFAULT_REPLACEMENT_RATIO
    # Set when a supplied judgement drove the availability rather than the
    # designation heuristic.
    assessment: InjuryAssessment | None = None

    @property
    def player(self) -> Player:
        return self.ranked_player.player

    @property
    def position(self) -> str:
        return self.ranked_player.position

    @property
    def display_name(self) -> str:
        return self.ranked_player.display_name

    @property
    def injury_label(self) -> str:
        """Short human-readable injury note, empty when the player is clean."""
        status = self.player.injury_status
        if not status or status.strip().upper() in _HEALTHY_DESIGNATIONS:
            # An assessment can flag a player Sleeper has not, which is half the
            # reason for having one.
            return "assessed" if self.assessment is not None else ""
        part = self.player.injury_body_part
        if part and part.strip().upper() not in {"UNDISCLOSED", "NONE"}:
            return f"{status} ({part})"
        return status


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


def body_part_effectiveness(body_part: str) -> float:
    """How close to full a player is expected to be if he plays, by body part."""
    part = (body_part or "").strip().upper()
    if not part:
        return 1.0
    # Structural first: "Knee - ACL" is a repaired ligament, not knee soreness.
    if any(token in part for token in SEVERE_INJURY_PARTS):
        return _SEVERE_EFFECTIVENESS
    if any(token in part for token in MINOR_INJURY_PARTS):
        return _MINOR_EFFECTIVENESS
    if any(token in part for token in LINGERING_INJURY_PARTS):
        return _LINGERING_EFFECTIVENESS
    # "Undisclosed" lands here, and it is the single most common value Sleeper
    # publishes. A small haircut acknowledges that something is wrong without
    # inventing a diagnosis; this is exactly the case an assessment improves on.
    return _UNKNOWN_EFFECTIVENESS


def resolve_designation(injury_status: str, roster_status: str = "") -> str:
    """The operative injury designation, or "" for a player with nothing against him.

    Sleeper's two availability fields move independently - a Questionable player
    is still status "Active", and a player parked on IR may carry no game-day
    designation at all - so the worse of the two reads wins.
    """
    designation = (injury_status or "").strip().upper()
    if designation in _HEALTHY_DESIGNATIONS:
        designation = ""
    standing = _UNAVAILABLE_ROSTER_STATUS.get((roster_status or "").strip().upper())
    candidates = [d for d in (designation, standing) if d]
    if not candidates:
        return ""
    return min(candidates, key=lambda d: INJURY_PLAY_PROBABILITY.get(d, 1.0))


def injury_availability(
    injury_status: str,
    body_part: str = "",
    roster_status: str = "",
    replacement_ratio: float = DEFAULT_REPLACEMENT_RATIO,
    carryable: float = 0.0,
    assessment: InjuryAssessment | None = None,
) -> float:
    """Expected value of a roster slot holding this player, as a fraction of his own.

    Three quantities, kept separate because they answer different questions:

        p - probability he is on the field, from the designation
        e - how close to full he is if he plays, from the body part
        r - what a replacement gives you instead, from the league's own depth

    A missed game does not score zero; you start somebody else. So the cost of an
    injury is the *gap to the replacement*, and how big that gap is depends
    entirely on the league. A Questionable quarterback in a twelve-team league
    costs almost nothing because the waiver wire holds a comparable starter; the
    same designation on a running back in a 32-team guillotine league is severe,
    because there is no comparable back left. The previous version of this
    function charged both the same fixed penalty, which contradicted the rest of
    this engine - every other multiplier here is derived from the league.

        value = p * max(e, r) + (1 - p) * r

    `max(e, r)` because a hobbled player does not have to be started: if the
    replacement is better than he is at 70%, you play the replacement. That also
    keeps the result monotonic in `p`, which a plain `p*e + (1-p)*r` is not.

    `carryable` scales the replacement credit for a player who is out for a
    stretch rather than a week, and is derived from bench depth. A season-ending
    designation is not a one-week hole to patch - it ties up a roster spot - so
    with one bench spot the pick is dead weight and gets no replacement credit.
    That distinction is forced by the arithmetic rather than chosen: crediting a
    season-ending injury with a waiver replacement would value a torn ACL at
    whatever the waiver wire is worth, which you would have had anyway.

    `assessment` overrides `p` and `e` where a judgement has been supplied, which
    is the point of `ff_tools.guillotine.injury`: the designation vocabulary is
    two short strings, and "Questionable / Undisclosed" describes both a rest day
    and a knee that will not be right for a month.

    Returns a multiplier in [0, 1.0]; exactly 1.0 for a player with nothing
    published against him and no assessment against him.
    """
    designation = resolve_designation(injury_status, roster_status)
    if designation and designation not in INJURY_PLAY_PROBABILITY:
        # A designation nobody recognises. Flagging it beats silently trusting it,
        # but the size of the penalty is not something to invent, so an unreadable
        # string is left alone rather than guessed at.
        if assessment is None:
            return 1.0
        designation = ""

    prob = INJURY_PLAY_PROBABILITY.get(designation, 1.0) if designation else 1.0
    near_term = designation in _NEAR_TERM_DESIGNATIONS
    if not designation:
        eff = 1.0
    elif not near_term:
        # Out for a stretch: "how limited is he when he plays" is not the
        # operative question, and the odds of him playing at all are already small.
        eff = 1.0
    elif body_part.strip():
        eff = body_part_effectiveness(body_part)
    else:
        # Flagged with no body part given. Something is wrong but the diagnosis is
        # withheld, which is the same standing as one we cannot parse.
        eff = _UNKNOWN_EFFECTIVENESS

    if assessment is not None:
        if assessment.play_probability is not None:
            prob = assessment.play_probability
            # An assessment is a considered read on this player, so it also
            # settles whether he is a one-week question or a longer absence.
            # Anything better than a coin flip is a player expected back.
            near_term = prob >= 0.5
        if assessment.effectiveness is not None:
            eff = assessment.effectiveness

    if prob >= 1.0 and eff >= 1.0:
        return 1.0

    ratio = min(1.0, max(0.0, replacement_ratio))
    if not near_term:
        # Out for a stretch: the replacement only helps to the extent you can
        # afford to hold the injured player while waiting.
        ratio *= min(1.0, max(0.0, carryable))

    return min(1.0, prob * max(eff, ratio) + (1.0 - prob) * ratio)


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
    # If position not in starters, it's not required (default to 0)
    required = slots.get(position, 0)

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
        use_injury_status: bool = True,
        position_ranks: dict[str, list[int]] | None = None,
        injury_assessments: dict[str, InjuryAssessment] | None = None,
    ) -> None:
        self.total_teams = total_teams
        self.roster_size = roster_size
        # Off means score players as if healthy. Worth having as a switch because
        # the injury penalty is only as good as the data behind it: a board with
        # no injury fields merged in scores identically either way, but a stale
        # or partial merge would quietly mark players down for nothing.
        self.use_injury_status = use_injury_status
        # Board ranks per position, ascending. Used to find what would actually
        # replace an injured player, which is what makes the injury penalty
        # league-dependent rather than a constant.
        self.position_ranks = dict(position_ranks or {})
        # Agent- or human-supplied judgements, keyed by match key. These beat the
        # designation heuristic wherever they exist.
        self.injury_assessments = dict(injury_assessments or {})
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
        use_injury_status: bool = True,
        injury_assessments: dict[str, InjuryAssessment] | None = None,
    ) -> GuillotineScorer:
        """Build a scorer whose pool sizes come from the actual rankings list.

        Scarcity is only meaningful relative to the players genuinely in play,
        so the pool is measured from the board rather than assumed.
        """
        totals: dict[str, int] = {}
        position_ranks: dict[str, list[int]] = {}
        for rp in ranked_players:
            pos = rp.position
            if pos:
                totals[pos] = totals.get(pos, 0) + 1
                if rp.rank > 0:
                    position_ranks.setdefault(pos, []).append(rp.rank)
        # Ascending, so index N is the (N+1)th best player at the position.
        for ranks in position_ranks.values():
            ranks.sort()
        return cls(
            total_teams=total_teams,
            position_totals=totals or None,
            roster_size=roster_size,
            starters=starters,
            position_multipliers=position_multipliers,
            startable_totals=startable_depth(
                ranked_players, total_teams, roster_size
            ),
            use_injury_status=use_injury_status,
            position_ranks=position_ranks,
            injury_assessments=injury_assessments,
        )

    def _per_team_demand(self, position: str) -> float:
        """Per-team starter demand at a position, flex included."""
        if self.total_teams <= 0:
            return float(self.starters.get(position, 1))
        return self.demand.get(position, 0.0) / self.total_teams

    def _startable_total(self, position: str) -> int | None:
        """The startable pool depth at a position, or None when unknown."""
        return self.startable_totals.get(position)

    @property
    def bench_spots(self) -> int:
        """Bench slots per team.

        Read from the lineup settings when they name a bench, since that is the
        league's own answer, and otherwise inferred from what the roster holds
        beyond its starters.
        """
        named = self.starters.get("BN", 0)
        if named:
            return int(named)
        starting = sum(v for k, v in self.starters.items() if k != "BN")
        return max(0, self.roster_size - starting)

    def _carryable(self) -> float:
        """How freely this league can hold an injured player while he recovers.

        A guillotine roster with one bench spot cannot stash anybody: that spot is
        the only cover for a bye or a late scratch, so a long-term injury is a
        wasted pick rather than an investment. Deeper benches can absorb one.
        """
        return max(0.0, min(1.0, (self.bench_spots - 1) / 2.0))

    def _replacement_index(self, position: str) -> int:
        """How many players at a position come off the board across the league.

        The player just past that line is what is actually available to replace an
        injured starter, so this is where the replacement's quality is read from.
        Starter demand accounts for most of it; bench picks are attributed to
        positions in proportion to that demand.
        """
        demand = self.demand.get(position, 0.0)
        total_demand = sum(self.demand.values())
        share = (demand / total_demand) if total_demand else 0.0
        bench_total = self.total_teams * self.bench_spots
        return int(demand + bench_total * share)

    def replacement_ratio(self, ranked: RankedPlayer) -> float:
        """What a replacement is worth relative to this player, in [0, 1].

        Answers "how much do I lose if he does not play?" - which is the question
        an injury penalty needs and the one a flat multiplier cannot ask. Two
        things drive it: how deep the position is in this league, and how good the
        player is. Losing the best running back in a 32-team league is dire
        because the next available back is nowhere near him; losing a mid-range
        quarterback in a shallow league costs almost nothing.
        """
        ranks = self.position_ranks.get(ranked.position)
        own = rank_to_score(ranked.rank)
        if not ranks or own <= 0:
            return DEFAULT_REPLACEMENT_RATIO

        index = self._replacement_index(ranked.position)
        if index < len(ranks):
            replacement_rank = ranks[index]
        else:
            # The replacement is past the end of the board, i.e. worse than
            # anything ranked. Extrapolate rather than clamping to the last known
            # player, which would overstate what is left.
            replacement_rank = ranks[-1] + (index - len(ranks) + 1)
        return min(1.0, rank_to_score(replacement_rank) / own)

    def _assessment_for(self, ranked: RankedPlayer) -> InjuryAssessment | None:
        """Any supplied judgement about this player."""
        if not self.injury_assessments:
            return None
        return self.injury_assessments.get(ranked.match_key)

    def availability_of(self, ranked: RankedPlayer) -> float:
        """Availability multiplier for one player, 1.0 when nothing is against him."""
        if not self.use_injury_status:
            return 1.0
        return injury_availability(
            ranked.player.injury_status,
            ranked.player.injury_body_part,
            ranked.player.status,
            replacement_ratio=self.replacement_ratio(ranked),
            carryable=self._carryable(),
            assessment=self._assessment_for(ranked),
        )

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
        avail = self.availability_of(ranked)
        ratio = self.replacement_ratio(ranked)

        return PlayerScore(
            ranked_player=ranked,
            rank_score=base,
            floor_bonus=floor,
            position_value=pos_mult,
            need_bonus=need,
            scarcity_bonus=scarcity,
            availability=avail,
            replacement_ratio=ratio,
            assessment=self._assessment_for(ranked),
            survival_score=base * floor * pos_mult * scarcity * need * avail,
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

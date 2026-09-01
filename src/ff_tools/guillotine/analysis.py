"""Data-driven guillotine league positional analysis.

Pulls real projection data and computes actual positional values,
scarcity curves, and floor metrics for a 32-team guillotine format.

Run standalone to see findings, or import to get computed values.

The measured multipliers do not agree with the hand-tuned priors in
`ff_tools.guillotine.strategy`, and the disagreement is informative rather than
a bug in either. As of the 2026 ESPN projections, for 32 teams:

    pos   measured   prior
    QB        1.05    0.65
    RB        1.34    1.04
    WR        1.00    1.05
    TE        0.88    1.25
    K         0.57    0.50
    DEF       0.65    0.50

They are answering different questions. VOR here is denominated in *points*: RB
scores high because the gap between a startable RB and RB33 is large in absolute
terms, and TE scores low because tight ends put up small totals either way. The
priors reason about *startability* - the claim behind TE 1.25 is that only about
18 tight ends are usable at all, which points-over-replacement cannot see once
the pool is 32 teams deep.

Neither is authoritative, so `POSITION_MULTIPLIER` remains the default and these
values are opt-in through `build_scorer`.
"""

from __future__ import annotations

from dataclasses import dataclass

from ff_tools.draft.rankings import RankedPlayer, RankingsSource, default_season
from ff_tools.guillotine.strategy import (
    STREAMABLE,
    GuillotineScorer,
    positional_demand,
)

# League sizes the scarcity curves are reported at.
SCARCITY_TEAM_SIZES = (12, 16, 24, 32, 40)

# How much of a streamable position's value over replacement survives the fact
# that you can re-draw its replacement off waivers every week. Drafting a QB
# early only buys the edge over whoever you would have streamed, not the edge
# over the last QB on the board.
STREAM_DISCOUNT = 0.45

# Positions the analysis covers.
ANALYZED_POSITIONS = ("QB", "RB", "WR", "TE", "K", "DEF")


@dataclass
class PositionAnalysis:
    """Computed analysis for a single position."""

    position: str
    total_players: int
    points_top1: float
    points_top12: float
    points_top24: float
    points_top32: float
    points_median: float
    dropoff_1_to_12: float  # % drop from 1st to 12th
    dropoff_12_to_24: float
    dropoff_24_to_32: float
    replacement_value: float  # points at the "replacement level" (32nd player)
    scarcity_score: float  # how steep the dropoff is
    variance_proxy: float  # coefficient of variation of top-32 players


@dataclass
class GuillotineValues:
    """Computed guillotine-specific values from projection data."""

    position_values: dict[str, float]  # multiplier per position
    floor_curve: dict[int, float]  # rank -> floor multiplier
    scarcity_curve: dict[str, list[float]]  # position -> [32-team scarcity ratios]
    raw_analyses: dict[str, PositionAnalysis]


def analyze_position(
    position: str,
    ranked: list[RankedPlayer],
) -> PositionAnalysis:
    """Analyze the points distribution for a single position.

    Computes dropoff curves, replacement value, and variance metrics
    from actual projection data.

    The cutoffs are fixed at ranks 1/12/24/32 - the `points_top32` field means
    the 32nd player, not "the last starter in your league". Nothing here varies
    with league size; `compute_positional_values` is the league-aware step.
    """
    pos_players = [rp for rp in ranked if rp.position == position]
    pos_players.sort(key=lambda rp: rp.rank)

    if not pos_players:
        return PositionAnalysis(
            position=position,
            total_players=0,
            points_top1=0, points_top12=0, points_top24=0,
            points_top32=0, points_median=0,
            dropoff_1_to_12=0, dropoff_12_to_24=0, dropoff_24_to_32=0,
            replacement_value=0, scarcity_score=0, variance_proxy=0,
        )

    points = [rp.projected_points for rp in pos_players]

    def safe_get(idx: int) -> float:
        return points[idx] if idx < len(points) else 0.0

    p1 = safe_get(0)
    p12 = safe_get(11)
    p24 = safe_get(23)
    p32 = safe_get(31)
    p_median = points[len(points) // 2] if points else 0

    # Dropoff percentages (negative = decline)
    d1_12 = ((p12 - p1) / p1 * 100) if p1 > 0 else 0
    d12_24 = ((p24 - p12) / p12 * 100) if p12 > 0 else 0
    d24_32 = ((p32 - p24) / p24 * 100) if p24 > 0 else 0

    # Scarcity: how much does the 32nd player score vs the 1st?
    # Lower = more scarce
    scarcity = (p32 / p1) if p1 > 0 else 0

    # Variance proxy: coefficient of variation of top-32 players
    top32_points = points[:32] if len(points) >= 32 else points
    if top32_points and len(top32_points) > 1:
        mean = sum(top32_points) / len(top32_points)
        variance = sum((x - mean) ** 2 for x in top32_points) / len(top32_points)
        std = variance ** 0.5
        cv = (std / mean) if mean > 0 else 0
    else:
        cv = 0

    return PositionAnalysis(
        position=position,
        total_players=len(pos_players),
        points_top1=p1,
        points_top12=p12,
        points_top24=p24,
        points_top32=p32,
        points_median=p_median,
        dropoff_1_to_12=d1_12,
        dropoff_12_to_24=d12_24,
        dropoff_24_to_32=d24_32,
        replacement_value=p32,
        scarcity_score=scarcity,
        variance_proxy=cv,
    )


def compute_positional_values(
    analyses: dict[str, PositionAnalysis],
    ranked: list[RankedPlayer] | None = None,
    teams: int = 32,
    starters: dict[str, int] | None = None,
    stream_discount: float = STREAM_DISCOUNT,
) -> dict[str, float]:
    """Compute guillotine positional value multipliers from projection data.

    Uses value over replacement (VOR): how many more points a startable player
    at this position scores than the one you could have had for free, where
    "for free" means the last player who would go in a league this size.

    An earlier version scored positions on the ratio of the 32nd player's
    points to the 1st player's. That measures the *shape* of the curve, not
    value, and it inverts the answer for quarterbacks: QBs all post big raw
    totals, so the top-to-replacement ratio looks steep even though a streamed
    QB gives back very little. VOR compares each position against its own
    replacement level, which is the quantity that actually decides where a pick
    is best spent.

    Streamable positions are then discounted, because their replacement level
    is redrawn every week off waivers rather than fixed at the draft.

    Feed the result to `GuillotineScorer(position_multipliers=...)` to replace
    the hand-tuned priors in `ff_tools.guillotine.strategy`, or call
    `build_scorer` below to do both steps at once.
    """
    if ranked is None:
        # Without the player list there is no points curve to integrate over,
        # so fall back to neutral rather than guessing.
        return {pos: 1.0 for pos in analyses}

    demand = positional_demand(starters, teams)

    by_pos: dict[str, list[float]] = {}
    for rp in ranked:
        if rp.projected_points > 0 and rp.position:
            by_pos.setdefault(rp.position, []).append(rp.projected_points)
    for points in by_pos.values():
        points.sort(reverse=True)

    vor: dict[str, float] = {}
    for pos in analyses:
        points = by_pos.get(pos, [])
        if not points:
            vor[pos] = 0.0
            continue

        # Replacement level: the player just past the last starting slot the
        # league will fill at this position.
        per_team = demand.get(pos, 0.0) / teams if teams > 0 else 1.0
        cutoff = max(1, int(round(per_team * teams)))
        replacement = points[min(cutoff, len(points) - 1)]

        # Compare the starters a team would actually roster against that floor.
        starter_pool = points[:cutoff]
        avg_starter = sum(starter_pool) / len(starter_pool)
        surplus = max(0.0, avg_starter - replacement)

        if pos in STREAMABLE:
            surplus *= stream_discount
        vor[pos] = surplus

    # Normalize against WR, the conventional reference point.
    ref = vor.get("WR", 0.0)
    if ref <= 0:
        ref = max(vor.values(), default=0.0)
    if ref <= 0:
        return {pos: 1.0 for pos in analyses}

    values = {}
    for pos, surplus in vor.items():
        if analyses[pos].total_players == 0:
            values[pos] = 1.0
            continue
        # Anchor WR at 1.0 and scale the spread around it, then clamp so a
        # thin-sample position cannot dominate the whole board.
        multiplier = 0.5 + 0.5 * (surplus / ref)
        values[pos] = round(max(0.5, min(1.5, multiplier)), 2)

    return values


def compute_floor_curve(ranked: list[RankedPlayer]) -> dict[int, float]:
    """Compute floor multipliers based on actual projection dropoff curves.

    Instead of arbitrary rank-to-multiplier mapping, this looks at how
    projections actually decline within each position and maps that
    to a floor bonus.
    """
    # Group by position and order each group by projected points. Ranking the
    # players themselves (rather than looking a points value up in a list of
    # floats) keeps ties correct: two players projected for the same total used
    # to both receive the first one's position rank.
    by_pos: dict[str, list[RankedPlayer]] = {}
    for rp in ranked:
        if rp.projected_points > 0:
            by_pos.setdefault(rp.position, []).append(rp)

    floor_curve = {}
    for players in by_pos.values():
        players.sort(key=lambda rp: rp.projected_points, reverse=True)
        depth = max(1, len(players) - 1)

        for pos_rank, rp in enumerate(players):
            # Position percentile (0 = best, 1 = worst)
            pos_pct = pos_rank / depth

            # Floor multiplier: top of position = 1.15, bottom = 0.85
            # Linear interpolation based on actual position depth
            floor = 1.15 - (pos_pct * 0.30)
            floor_curve[rp.rank] = round(floor, 2)

    return floor_curve


def compute_scarcity_curves(ranked: list[RankedPlayer]) -> dict[str, list[float]]:
    """Compute scarcity ratios at each league size in `SCARCITY_TEAM_SIZES`.

    For each position, the returned list gives startable players per team at
    every size in that tuple - so the whole curve is reported and the caller's
    own league size does not narrow it.
    """
    by_pos: dict[str, list[RankedPlayer]] = {}
    for rp in ranked:
        by_pos.setdefault(rp.position, []).append(rp)

    curves = {}
    for pos, players in by_pos.items():
        players.sort(key=lambda rp: rp.projected_points, reverse=True)
        points = [rp.projected_points for rp in players if rp.projected_points > 0]

        if not points:
            curves[pos] = []
            continue

        # "Startable" = within 50% of the best projection at the position. This
        # does not vary with league size, so it is computed once.
        top = points[0]
        startable = sum(1 for p in points if p >= top * 0.5)

        # One starter per team at this position, so demand is the team count.
        curves[pos] = [
            round(startable / max(1, t), 2) for t in SCARCITY_TEAM_SIZES
        ]

    return curves


def run_analysis(
    rankings: list[RankedPlayer] | None = None,
    teams: int = 32,
    season: str | int | None = None,
) -> GuillotineValues:
    """Run full guillotine positional analysis from projection data.

    If rankings is None, fetches from ESPN for `season` (defaulting to the
    upcoming season). Every metric here is derived from projected points, so a
    rankings list without projections yields nothing useful - the Sleeper
    fallback has no projections at all and only exists so the call does not
    raise.
    """
    if rankings is None:
        source = RankingsSource()
        target = season if season is not None else default_season()
        try:
            rankings = source.fetch_espn_rankings(season=target, limit=400)
        except Exception:
            print(
                "  WARNING: ESPN projections unavailable; falling back to the "
                "Sleeper pool, which carries no projections. Positional values "
                "will be meaningless."
            )
            rankings = source.fetch_sleeper_rankings(limit=400)

    if not any(rp.projected_points > 0 for rp in rankings):
        print(
            "  WARNING: no projected points in the rankings list. Every metric "
            "below is computed from projections and will read as zero."
        )

    # Analyze each position
    analyses = {}
    for pos in ANALYZED_POSITIONS:
        analyses[pos] = analyze_position(pos, rankings)

    # Compute values
    position_values = compute_positional_values(analyses, rankings, teams)
    floor_curve = compute_floor_curve(rankings)
    scarcity_curves = compute_scarcity_curves(rankings)

    return GuillotineValues(
        position_values=position_values,
        floor_curve=floor_curve,
        scarcity_curve=scarcity_curves,
        raw_analyses=analyses,
    )


def build_scorer(
    values: GuillotineValues,
    ranked_players: list[RankedPlayer],
    total_teams: int = 32,
    roster_size: int = 16,
    starters: dict[str, int] | None = None,
) -> GuillotineScorer:
    """Build a scorer that uses the measured positional values.

    Without this, the analysis output was purely informational - the scorer went
    on using the hand-tuned priors in `ff_tools.guillotine.strategy` no matter
    what the projections said.
    """
    return GuillotineScorer.from_rankings(
        ranked_players,
        total_teams=total_teams,
        roster_size=roster_size,
        starters=starters,
        position_multipliers=values.position_values,
    )


def print_analysis(values: GuillotineValues) -> None:
    """Pretty-print the analysis results."""
    print("\n" + "=" * 70)
    print("  GUILLOTINE LEAGUE POSITIONAL ANALYSIS (32-Team)")
    print("=" * 70)

    print("\n  POSITIONAL VALUE CURVES")
    print("  (Points by rank within position, from projection data)")
    print("-" * 70)

    for pos in ["QB", "RB", "WR", "TE"]:
        a = values.raw_analyses.get(pos)
        if not a or a.total_players == 0:
            continue
        print(f"\n  {pos} ({a.total_players} players in pool)")
        print(f"    #1:  {a.points_top1:6.1f} pts")
        print(f"    #12: {a.points_top12:6.1f} pts  ({a.dropoff_1_to_12:+.1f}% from #1)")
        print(f"    #24: {a.points_top24:6.1f} pts  ({a.dropoff_12_to_24:+.1f}% from #12)")
        if pos != "QB":
            print(f"    #32: {a.points_top32:6.1f} pts  ({a.dropoff_24_to_32:+.1f}% from #24)")
        print(f"    Median: {a.points_median:6.1f} pts")
        print(f"    Scarcity (32nd/1st): {a.scarcity_score:.2f}")
        print(f"    Variance (CV): {a.variance_proxy:.3f}")

    print("\n\n  COMPUTED POSITIONAL MULTIPLIERS")
    print("  (Pass to GuillotineScorer(position_multipliers=...) to use these)")
    print("-" * 70)
    for pos in ANALYZED_POSITIONS:
        val = values.position_values.get(pos, 1.0)
        bar = "#" * int(val * 20)
        print(f"    {pos}: {val:.2f}x  {bar}")

    print("\n\n  SCARCITY BY LEAGUE SIZE")
    print("  (Ratio of startable players to teams needed)")
    print("  < 1.0 = shortage, > 1.0 = surplus")
    print("-" * 70)
    header = "    Pos   " + "  ".join(f"{t:>3d}t" for t in SCARCITY_TEAM_SIZES)
    print(header)
    for pos in ["QB", "RB", "WR", "TE"]:
        ratios = values.scarcity_curve.get(pos, [])
        if not ratios:
            continue
        row = f"    {pos:<5s} " + "  ".join(f"{r:5.2f}" for r in ratios)
        print(row)

    print("\n\n  KEY FINDINGS")
    print("-" * 70)
    # Auto-detect findings
    analyses = values.raw_analyses
    if "TE" in analyses and "WR" in analyses:
        te = analyses["TE"]
        wr = analyses["WR"]
        if te.scarcity_score < wr.scarcity_score:
            print("    * TE is more scarce than WR in 32-team format")
    if "QB" in analyses:
        qb = analyses["QB"]
        if qb.scarcity_score > 0.4:
            print("    * QB is deep enough to stream in guillotine format")
    if "RB" in analyses and "WR" in analyses:
        rb = analyses["RB"]
        wr = analyses["WR"]
        if rb.dropoff_1_to_12 < wr.dropoff_1_to_12:
            print("    * RB top tier is more concentrated than WR top tier")

    print("\n" + "=" * 70)


if __name__ == "__main__":
    values = run_analysis()
    print_analysis(values)

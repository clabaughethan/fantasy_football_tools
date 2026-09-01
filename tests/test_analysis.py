"""Tests for the projection-driven guillotine analysis."""

from ff_tools.draft.rankings import RankedPlayer
from ff_tools.guillotine.analysis import (
    STREAM_DISCOUNT,
    analyze_position,
    build_scorer,
    compute_floor_curve,
    compute_positional_values,
    compute_scarcity_curves,
    run_analysis,
)
from ff_tools.models.player import Player


def _pool(pos: str, points: list[float], start_rank: int = 1) -> list[RankedPlayer]:
    """A position's worth of players, projections descending."""
    return [
        RankedPlayer(
            rank=start_rank + i,
            player=Player(
                player_id=f"{pos}{i}", full_name=f"{pos} {i}", position=pos
            ),
            projected_points=pts,
        )
        for i, pts in enumerate(points)
    ]


def _linear(top: float, bottom: float, count: int) -> list[float]:
    step = (top - bottom) / max(1, count - 1)
    return [round(top - step * i, 1) for i in range(count)]


def _board() -> list[RankedPlayer]:
    """A synthetic board: WRs deep and flat, RBs steep, QBs high but flat."""
    ranked: list[RankedPlayer] = []
    rank = 1
    for pos, points in (
        ("RB", _linear(320, 60, 80)),
        ("WR", _linear(300, 120, 100)),
        ("QB", _linear(400, 330, 32)),
        ("TE", _linear(220, 80, 30)),
        ("K", _linear(150, 110, 20)),
        ("DEF", _linear(160, 100, 25)),
    ):
        ranked.extend(_pool(pos, points, start_rank=rank))
        rank += len(points)
    return ranked


# ── analyze_position ────────────────────────────────────────────────


def test_analyze_position_reads_the_curve():
    ranked = _pool("RB", _linear(300, 100, 40))
    a = analyze_position("RB", ranked)

    assert a.total_players == 40
    assert a.points_top1 == 300.0
    assert a.dropoff_1_to_12 < 0  # projections decline
    assert 0 < a.scarcity_score < 1
    assert a.replacement_value == a.points_top32


def test_analyze_position_missing_position_is_all_zeros():
    a = analyze_position("TE", _pool("RB", [200.0, 100.0]))
    assert a.total_players == 0
    assert a.points_top1 == 0
    assert a.scarcity_score == 0


def test_analyze_position_shallow_pool_does_not_index_past_the_end():
    a = analyze_position("K", _pool("K", [150.0, 140.0, 130.0]))
    assert a.total_players == 3
    assert a.points_top32 == 0.0
    assert a.dropoff_24_to_32 == 0


# ── compute_positional_values ───────────────────────────────────────


def test_positional_values_anchor_wr_near_one():
    ranked = _board()
    analyses = {
        pos: analyze_position(pos, ranked)
        for pos in ("QB", "RB", "WR", "TE", "K", "DEF")
    }
    values = compute_positional_values(analyses, ranked, teams=32)
    assert values["WR"] == 1.0


def test_positional_values_do_not_overrate_streamable_quarterbacks():
    """The bug this replaced: a flat-but-high QB curve scored as scarce.

    QBs here post the biggest raw totals on the board and the tightest spread,
    which is exactly the shape that should *not* earn a premium.
    """
    ranked = _board()
    analyses = {
        pos: analyze_position(pos, ranked)
        for pos in ("QB", "RB", "WR", "TE", "K", "DEF")
    }
    values = compute_positional_values(analyses, ranked, teams=32)

    assert values["QB"] < values["RB"]
    assert values["QB"] <= values["WR"]


def test_stream_discount_only_moves_streamable_positions():
    ranked = _board()
    analyses = {
        pos: analyze_position(pos, ranked)
        for pos in ("QB", "RB", "WR", "TE", "K", "DEF")
    }
    full = compute_positional_values(analyses, ranked, teams=32, stream_discount=1.0)
    discounted = compute_positional_values(
        analyses, ranked, teams=32, stream_discount=STREAM_DISCOUNT
    )
    assert discounted["QB"] < full["QB"]
    assert discounted["RB"] == full["RB"]


def test_positional_values_stay_in_range():
    ranked = _board()
    analyses = {
        pos: analyze_position(pos, ranked)
        for pos in ("QB", "RB", "WR", "TE", "K", "DEF")
    }
    values = compute_positional_values(analyses, ranked, teams=32)
    assert all(0.5 <= v <= 1.5 for v in values.values())


def test_positional_values_go_neutral_without_a_points_curve():
    """No projections means no basis for a multiplier, so say nothing."""
    analyses = {"RB": analyze_position("RB", [])}
    assert compute_positional_values(analyses) == {"RB": 1.0}
    assert compute_positional_values(analyses, [], teams=32) == {"RB": 1.0}


def test_positional_values_respect_the_league_lineup():
    """A superflex league starts two QBs per team, which lifts QB value.

    Needs a QB pool deeper than the league's demand: with only 32 QBs on the
    board a 32-team superflex league has already run the position dry, so
    replacement level is pinned at the last QB and both lineups price the
    position identically.
    """
    ranked = _pool("QB", _linear(400, 180, 70)) + _pool(
        "WR", _linear(300, 120, 100), start_rank=71
    )
    analyses = {pos: analyze_position(pos, ranked) for pos in ("QB", "WR")}

    single = compute_positional_values(
        analyses, ranked, teams=32, starters={"QB": 1, "WR": 2}
    )
    superflex = compute_positional_values(
        analyses, ranked, teams=32, starters={"QB": 1, "SUPER_FLEX": 1, "WR": 2}
    )
    assert superflex["QB"] > single["QB"]


def test_positional_values_handle_demand_exceeding_the_pool():
    """A position the league cannot fill must not blow up or spike to the cap."""
    ranked = _pool("QB", _linear(400, 330, 20)) + _pool(
        "WR", _linear(300, 120, 100), start_rank=21
    )
    analyses = {pos: analyze_position(pos, ranked) for pos in ("QB", "WR")}
    values = compute_positional_values(
        analyses, ranked, teams=32, starters={"QB": 1, "SUPER_FLEX": 1, "WR": 2}
    )
    assert 0.5 <= values["QB"] <= 1.5


# ── compute_floor_curve ─────────────────────────────────────────────


def test_floor_curve_declines_within_a_position():
    ranked = _pool("RB", _linear(300, 100, 20))
    curve = compute_floor_curve(ranked)
    assert curve[1] == 1.15
    assert curve[20] == 0.85
    assert curve[1] > curve[10] > curve[20]


def test_floor_curve_gives_tied_players_distinct_ranks():
    """Two players on the same projection used to share the first one's rank."""
    ranked = _pool("WR", [200.0, 200.0, 200.0, 100.0])
    curve = compute_floor_curve(ranked)
    assert set(curve) == {1, 2, 3, 4}
    assert curve[1] > curve[4]


def test_floor_curve_skips_players_without_projections():
    ranked = _pool("RB", [300.0, 0.0, 100.0])
    curve = compute_floor_curve(ranked)
    assert 2 not in curve
    assert set(curve) == {1, 3}


# ── compute_scarcity_curves ─────────────────────────────────────────


def test_scarcity_curve_falls_as_the_league_grows():
    ranked = _pool("RB", _linear(300, 100, 60))
    curve = compute_scarcity_curves(ranked)["RB"]
    assert curve == sorted(curve, reverse=True)
    assert all(r > 0 for r in curve)


def test_scarcity_curve_empty_for_a_position_without_projections():
    assert compute_scarcity_curves(_pool("K", [0.0, 0.0]))["K"] == []


# ── run_analysis / build_scorer ─────────────────────────────────────


def test_run_analysis_on_a_supplied_board_makes_no_network_call():
    values = run_analysis(_board(), teams=32)
    assert set(values.position_values) == {"QB", "RB", "WR", "TE", "K", "DEF"}
    assert values.raw_analyses["RB"].total_players == 80
    assert values.floor_curve
    assert values.scarcity_curve["WR"]


def test_build_scorer_actually_uses_the_measured_values():
    """Without this the analysis was informational only - the scorer ignored it."""
    ranked = _board()
    values = run_analysis(ranked, teams=32)
    scorer = build_scorer(values, ranked, total_teams=32, roster_size=16)

    assert scorer.position_multipliers == values.position_values
    # Pool sizes come from the board rather than the module defaults.
    assert scorer.position_totals["RB"] == 80


def test_run_analysis_warns_when_there_are_no_projections(capsys):
    ranked = _pool("RB", [0.0, 0.0, 0.0])
    run_analysis(ranked, teams=12)
    assert "no projected points" in capsys.readouterr().out

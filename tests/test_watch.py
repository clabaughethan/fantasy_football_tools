"""Tests for the guillotine elimination watch."""

from ff_tools.draft.rankings import RankedPlayer
from ff_tools.guillotine.watch import build_watch, is_ruled_out, plan_cap_for_rank
from ff_tools.models.league import Matchup, Roster
from ff_tools.models.player import Player


def _player(
    pid: str,
    name: str,
    pos: str = "RB",
    team: str = "ARI",
    injury_status: str = "",
    status: str = "Active",
) -> Player:
    return Player(
        player_id=pid,
        full_name=name,
        position=pos,
        team=team,
        injury_status=injury_status,
        status=status,
    )


def _ranked(rank: int, name: str, pos: str = "RB", team: str = "ARI") -> RankedPlayer:
    return RankedPlayer(
        rank=rank,
        player=Player(player_id=f"r{rank}", full_name=name, position=pos, team=team),
        projected_points=200.0 - rank,
    )


def _matchup(roster_id: int, points: float, starters, starter_pts) -> Matchup:
    return Matchup(
        matchup_id=1,
        roster_id=roster_id,
        starters=list(starters),
        starters_points=list(starter_pts),
        points=points,
    )


def test_empty_matchups_returns_empty():
    assert build_watch([], [], {}, {}, None) == []


def test_sorts_by_points_and_marks_likely_out():
    matchups = [
        _matchup(1, 100.0, ["a"], [100.0]),
        _matchup(2, 40.0, ["b"], [40.0]),
        _matchup(3, 60.0, ["c"], [60.0]),
    ]
    players = {
        "a": _player("a", "Safe Guy"),
        "b": _player("b", "Doomed Guy"),
        "c": _player("c", "Mid Guy"),
    }
    rosters = [
        Roster(roster_id=1, players=["a"], starters=["a"]),
        Roster(roster_id=2, players=["b"], starters=["b"]),
        Roster(roster_id=3, players=["c"], starters=["c"]),
    ]
    watch = build_watch(matchups, rosters, {}, players, None, bottom_n=3)
    assert [t.roster_id for t in watch] == [2, 3, 1]
    # Safety line is 2nd-lowest (60.0)
    assert watch[0].margin_to_safety == 40.0 - 60.0
    assert watch[1].margin_to_safety == 0.0
    assert watch[2].margin_to_safety == 40.0
    assert watch[0].likely_out is True
    assert watch[1].likely_out is False


def test_multi_cut_safety_line_and_flags():
    matchups = [
        _matchup(1, 100.0, ["a"], [100.0]),
        _matchup(2, 40.0, ["b"], [40.0]),
        _matchup(3, 60.0, ["c"], [60.0]),
        _matchup(4, 80.0, ["d"], [80.0]),
    ]
    players = {
        "a": _player("a", "Safe Guy"),
        "b": _player("b", "Doomed Guy"),
        "c": _player("c", "Mid Guy"),
        "d": _player("d", "Nearly Safe Guy"),
    }
    rosters = [Roster(roster_id=i, players=[p], starters=[p]) for i, p in
               [(1, "a"), (2, "b"), (3, "c"), (4, "d")]]
    watch = build_watch(matchups, rosters, {}, players, None, bottom_n=4, cuts=2)
    assert [t.roster_id for t in watch] == [2, 3, 4, 1]
    # Safety line is the 3rd-lowest (80.0)
    assert watch[0].margin_to_safety == 40.0 - 80.0
    assert watch[1].margin_to_safety == 60.0 - 80.0
    assert watch[2].margin_to_safety == 0.0
    assert watch[0].likely_out is True
    assert watch[1].likely_out is True
    assert watch[2].likely_out is False


def test_zero_point_starter_flagged_pending():
    matchups = [_matchup(1, 10.0, ["a", "b"], [10.0, 0.0])]
    players = {"a": _player("a", "Played Guy"), "b": _player("b", "Monday Guy")}
    rosters = [Roster(roster_id=1, players=["a", "b"], starters=["a", "b"])]
    watch = build_watch(matchups, rosters, {}, players, None, bottom_n=1)
    assert watch[0].starters[0].pending is False
    assert watch[0].starters[1].pending is True


def test_assets_match_across_suffixes_and_sort_by_rank():
    # Sleeper roster says "James Cook", rankings say "James Cook III".
    matchups = [_matchup(1, 42.0, ["p1"], [42.0])]
    players = {
        "p1": _player("p1", "James Cook"),
        "p2": _player("p2", "Brock Bowers", pos="TE", team="LV"),
    }
    rosters = [Roster(roster_id=1, players=["p1", "p2"], starters=["p1"])]
    rankings = [
        _ranked(5, "Brock Bowers", pos="TE", team="LV"),
        _ranked(30, "James Cook III"),
    ]
    watch = build_watch(matchups, rosters, {}, players, rankings, bottom_n=1)
    assets = watch[0].top_assets
    assert [a.name for a in assets] == ["Brock Bowers", "James Cook"]
    assert [a.rank for a in assets] == [5, 30]
    assert assets[0].is_starter is False
    assert assets[1].is_starter is True


def test_plan_cap_tiers():
    assert "ELITE" in plan_cap_for_rank(5)
    assert "35" in plan_cap_for_rank(20)
    assert "25" in plan_cap_for_rank(50)
    assert "15" in plan_cap_for_rank(120)
    assert "8" in plan_cap_for_rank(250)
    assert "5" in plan_cap_for_rank(None)


def test_is_ruled_out():
    assert is_ruled_out("Out") is True
    assert is_ruled_out("IR") is True
    assert is_ruled_out("PUP") is True
    assert is_ruled_out("", "Inactive") is True
    assert is_ruled_out("Questionable") is False
    assert is_ruled_out("") is False


def test_ruled_out_zero_is_not_pending():
    matchups = [_matchup(1, 10.0, ["a", "b"], [10.0, 0.0])]
    players = {
        "a": _player("a", "Played Guy"),
        "b": _player("b", "IR Guy", injury_status="IR", status="Inactive"),
    }
    rosters = [Roster(roster_id=1, players=["a", "b"], starters=["a", "b"])]
    watch = build_watch(matchups, rosters, {}, players, None, bottom_n=1)
    assert watch[0].starters[1].pending is False
    assert watch[0].starters[1].injury_status == "IR"


def test_ruled_out_asset_gets_skip_cap():
    matchups = [_matchup(1, 42.0, ["p1"], [42.0])]
    players = {
        "p1": _player("p1", "James Cook"),
        "p2": _player("p2", "Brock Bowers", pos="TE", team="LV",
                      injury_status="Out"),
    }
    rosters = [Roster(roster_id=1, players=["p1", "p2"], starters=["p1"])]
    rankings = [
        _ranked(5, "Brock Bowers", pos="TE", team="LV"),
        _ranked(30, "James Cook III"),
    ]
    watch = build_watch(matchups, rosters, {}, players, rankings, bottom_n=1)
    bowers = watch[0].top_assets[0]
    assert bowers.rank == 5
    assert "skip" in bowers.plan_cap

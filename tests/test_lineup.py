"""Tests for reading lineup shape off a league and turning it into demand."""

from ff_tools.espn.client import _roster_positions
from ff_tools.guillotine.strategy import positional_demand
from ff_tools.models.league import League

# A standard 9-starter, 7-bench Sleeper lineup.
_STANDARD = [
    "QB", "RB", "RB", "WR", "WR", "TE", "FLEX", "K", "DEF",
    "BN", "BN", "BN", "BN", "BN", "BN", "BN",
]


# ── League ──────────────────────────────────────────────────────────


def test_league_roster_size_counts_the_bench():
    league = League(league_id="1", roster_positions=_STANDARD)
    assert league.roster_size == 16
    assert sum(league.starting_slots.values()) == 9


def test_league_starting_slots():
    league = League(league_id="1", roster_positions=_STANDARD)
    assert league.starting_slots == {
        "QB": 1, "RB": 2, "WR": 2, "TE": 1, "FLEX": 1, "K": 1, "DEF": 1,
    }


def test_league_without_settings_reports_nothing_rather_than_a_default():
    league = League(league_id="1")
    assert league.roster_size == 0
    assert league.starting_slots == {}


def test_league_from_sleeper_reads_roster_positions():
    league = League.from_sleeper({
        "league_id": "123",
        "name": "Test",
        "total_rosters": 12,
        "roster_positions": _STANDARD,
    })
    assert league.roster_size == 16
    assert league.starting_slots["RB"] == 2


# ── ESPN lineupSlotCounts ───────────────────────────────────────────


def test_espn_roster_positions_expands_the_slot_count_map():
    """ESPN sends `{slotId: count}`, not a list of slot objects."""
    settings = {
        "rosterSettings": {
            "lineupSlotCounts": {
                "0": 1,    # QB
                "2": 2,    # RB
                "4": 2,    # WR
                "6": 1,    # TE
                "16": 1,   # DEF
                "17": 1,   # K
                "20": 7,   # BN
                "21": 1,   # IR
                "23": 1,   # FLEX
                "1": 0,    # unused TQB
            }
        }
    }
    positions = _roster_positions(settings)

    assert positions.count("QB") == 1
    assert positions.count("RB") == 2
    assert positions.count("WR") == 2
    assert positions.count("FLEX") == 1
    assert positions.count("BN") == 7
    # Zero-count slots contribute nothing.
    assert len(positions) == 17

    league = League(league_id="1", roster_positions=positions)
    assert league.starting_slots == {
        "QB": 1, "RB": 2, "WR": 2, "TE": 1, "FLEX": 1, "K": 1, "DEF": 1,
    }


def test_espn_roster_positions_tolerates_junk():
    assert _roster_positions({}) == []
    assert _roster_positions({"rosterSettings": None}) == []
    assert _roster_positions(
        {"rosterSettings": {"lineupSlotCounts": {"999": 3, "0": "x"}}}
    ) == []


# ── positional_demand ───────────────────────────────────────────────


def test_positional_demand_spreads_flex_by_dedicated_starters():
    """RB2/WR2/TE1 with 2 flex spots: 0.8 to RB, 0.8 to WR, 0.4 to TE per team."""
    demand = positional_demand(
        {"QB": 1, "RB": 2, "WR": 2, "TE": 1, "FLEX": 2, "K": 1, "DEF": 1},
        total_teams=10,
    )
    assert demand["QB"] == 10.0  # 1 per team, no flex share
    assert demand["RB"] == 20.0 + 8.0
    assert demand["WR"] == 20.0 + 8.0
    assert demand["TE"] == 10.0 + 4.0
    assert demand["K"] == 10.0


def test_positional_demand_attributes_superflex_to_qb():
    demand = positional_demand({"QB": 1, "SUPER_FLEX": 1, "RB": 2}, total_teams=12)
    assert demand["QB"] == 24.0
    assert demand["RB"] == 24.0


def test_positional_demand_ignores_bench_and_idp():
    demand = positional_demand({"QB": 1, "BN": 7, "IDP_FLEX": 3}, total_teams=10)
    assert demand == {"QB": 10.0}


def test_positional_demand_splits_flex_evenly_with_no_base_starters():
    """A flex-only lineup has no proportions to follow, so it splits evenly."""
    demand = positional_demand({"QB": 1, "FLEX": 3}, total_teams=10)
    assert demand["RB"] == demand["WR"] == demand["TE"] == 10.0


def test_positional_demand_defaults_when_lineup_is_unknown():
    demand = positional_demand(None, total_teams=32)
    assert demand["QB"] == 32.0
    assert demand["RB"] > 64.0  # 2 dedicated plus a flex share


def test_positional_demand_does_not_mutate_the_caller_lineup():
    starters = {"QB": 1, "RB": 2, "FLEX": 1}
    positional_demand(starters, total_teams=10)
    assert starters == {"QB": 1, "RB": 2, "FLEX": 1}

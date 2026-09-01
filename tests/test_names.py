"""Tests for cross-source player identity keys and label normalization."""

import pytest

from ff_tools.utils.names import (
    match_key,
    normalize_lineup_slot,
    normalize_name,
    normalize_position,
    normalize_team,
    starting_slot_counts,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Ja'Marr Chase", "jamarr chase"),
        ("Marvin Harrison Jr.", "marvin harrison"),
        ("D.J. Moore", "dj moore"),
        ("Kenneth Walker III", "kenneth walker"),
        ("Amon-Ra St. Brown", "amon ra st brown"),
        ("  Josh   Allen  ", "josh allen"),
        ("", ""),
        (None, ""),
    ],
)
def test_normalize_name(raw, expected):
    assert normalize_name(raw) == expected


def test_normalize_name_strips_accents():
    assert normalize_name("Kadarius Tonè") == "kadarius tone"


def test_normalize_name_keeps_two_token_suffix():
    """A suffix is only dropped when a real name survives underneath it."""
    assert normalize_name("Player Jr.") == "player jr"


def test_normalize_name_agrees_across_sources():
    """The whole point: differently-formatted spellings collapse together."""
    assert normalize_name("Marvin Harrison Jr.") == normalize_name("Marvin Harrison")
    assert normalize_name("Ja'Marr Chase") == normalize_name("JaMarr Chase")


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("JAC", "JAX"),
        ("WSH", "WAS"),
        ("jax", "JAX"),
        ("OAK", "LV"),
        ("SD", "LAC"),
        ("KC", "KC"),
        ("", ""),
        (None, ""),
    ],
)
def test_normalize_team(raw, expected):
    assert normalize_team(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("DST", "DEF"),
        ("D/ST", "DEF"),
        ("dst", "DEF"),
        ("PK", "K"),
        ("RB", "RB"),
        ("", ""),
        (None, ""),
    ],
)
def test_normalize_position(raw, expected):
    assert normalize_position(raw) == expected


def test_match_key_person():
    assert match_key("Jahmyr Gibbs", "RB", "DET") == "jahmyr gibbs"


def test_match_key_defense_collapses_naming_variants():
    """Defenses are named wildly differently, so they key on the team."""
    keys = {
        match_key("PHI", "DEF", "PHI"),
        match_key("Philadelphia Eagles", "DST", None),
        match_key("Eagles D/ST", "D/ST", None),
        match_key("Philadelphia Eagles", "DEF", "PHI"),
    }
    assert keys == {"def:PHI"}


def test_match_key_defense_team_alias():
    assert match_key("Washington Commanders", "DEF", "WSH") == "def:WAS"


def test_match_key_defense_without_resolvable_team():
    key = match_key("Some Unknown Team", "DEF", None)
    assert key.startswith("def:")


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("QB", "QB"),
        ("WRRB_FLEX", "FLEX"),
        ("REC_FLEX", "FLEX"),
        ("SUPER_FLEX", "SUPER_FLEX"),
        ("BE", "BN"),
        ("BN", "BN"),
        ("IR", ""),
        ("TAXI", ""),
        ("NONSENSE", ""),
        (None, ""),
    ],
)
def test_normalize_lineup_slot(raw, expected):
    assert normalize_lineup_slot(raw) == expected


def test_starting_slot_counts_excludes_bench():
    positions = [
        "QB", "RB", "RB", "WR", "WR", "TE", "FLEX", "K", "DEF",
        "BN", "BN", "BN", "BN", "BN", "BN", "IR",
    ]
    assert starting_slot_counts(positions) == {
        "QB": 1, "RB": 2, "WR": 2, "TE": 1, "FLEX": 1, "K": 1, "DEF": 1,
    }


def test_starting_slot_counts_sums_flex_variants():
    """Distinct flex types all draw from RB/WR/TE, so they add together."""
    assert starting_slot_counts(["WRRB_FLEX", "REC_FLEX", "FLEX"]) == {"FLEX": 3}


def test_starting_slot_counts_empty():
    assert starting_slot_counts([]) == {}
    assert starting_slot_counts(None) == {}

"""Tests for the rankings fallback chain and season defaulting.

No network access: every source is monkeypatched.
"""

import datetime

import pytest

from ff_tools.draft import rankings as rankings_mod
from ff_tools.draft.rankings import RankedPlayer, RankingsSource, default_season
from ff_tools.models.player import Player


def _one(source: str) -> list[RankedPlayer]:
    return [
        RankedPlayer(
            rank=1,
            player=Player(player_id="1", full_name="A Player", position="RB"),
            source=source,
        )
    ]


def _boom(*_args, **_kwargs):
    raise RuntimeError("source unavailable")


# ── default_season ──────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("today", "expected"),
    [
        (datetime.date(2026, 1, 15), 2025),   # before projections publish
        (datetime.date(2026, 2, 28), 2025),
        (datetime.date(2026, 3, 1), 2026),    # March: the new season is live
        (datetime.date(2026, 9, 1), 2026),
        (datetime.date(2026, 12, 31), 2026),
    ],
)
def test_default_season(monkeypatch, today, expected):
    class _Date(datetime.date):
        @classmethod
        def today(cls):
            return today

    monkeypatch.setattr(rankings_mod, "date", _Date)
    assert default_season() == expected


def test_espn_rankings_default_season_is_not_a_literal_year(monkeypatch):
    """A hardcoded season silently starts requesting the past."""
    seen = {}

    def _capture(url, **_kwargs):
        seen["url"] = url
        raise RuntimeError("stop here")

    src = RankingsSource()
    monkeypatch.setattr(src.session, "get", _capture)
    with pytest.raises(RuntimeError):
        src.fetch_espn_rankings()

    assert f"/seasons/{default_season()}/" in seen["url"]


# ── fetch_best_available ────────────────────────────────────────────


def test_prefers_fantasypros(monkeypatch, capsys):
    src = RankingsSource()
    monkeypatch.setattr(src, "fetch_fantasypros_rankings", lambda **_: _one("fp"))
    monkeypatch.setattr(src, "fetch_espn_rankings", _boom)
    monkeypatch.setattr(src, "fetch_sleeper_rankings", _boom)

    ranked = src.fetch_best_available()
    assert ranked[0].source == "fp"
    assert "FantasyPros" in capsys.readouterr().out


def test_falls_back_to_espn(monkeypatch, capsys):
    src = RankingsSource()
    monkeypatch.setattr(src, "fetch_fantasypros_rankings", _boom)
    monkeypatch.setattr(src, "fetch_espn_rankings", lambda **_: _one("espn"))
    monkeypatch.setattr(src, "fetch_sleeper_rankings", _boom)

    assert src.fetch_best_available()[0].source == "espn"
    assert "ESPN" in capsys.readouterr().out


def test_falls_back_to_sleeper_and_says_so(monkeypatch, capsys):
    """The Sleeper pool has no ranks or projections, so reaching it is a warning."""
    src = RankingsSource()
    monkeypatch.setattr(src, "fetch_fantasypros_rankings", _boom)
    monkeypatch.setattr(src, "fetch_espn_rankings", _boom)
    monkeypatch.setattr(src, "fetch_sleeper_rankings", lambda **_: _one("sleeper"))

    assert src.fetch_best_available()[0].source == "sleeper"
    out = capsys.readouterr().out
    assert "WARNING" in out
    assert "no consensus rank" in out


def test_an_empty_result_is_treated_as_a_failure(monkeypatch):
    """A source that answers with nothing is no better than one that raises."""
    src = RankingsSource()
    monkeypatch.setattr(src, "fetch_fantasypros_rankings", lambda **_: [])
    monkeypatch.setattr(src, "fetch_espn_rankings", lambda **_: _one("espn"))
    monkeypatch.setattr(src, "fetch_sleeper_rankings", _boom)

    assert src.fetch_best_available(verbose=False)[0].source == "espn"


def test_season_is_passed_through_to_espn(monkeypatch):
    seen = {}

    def _espn(season=None, limit=0):
        seen["season"] = season
        return _one("espn")

    src = RankingsSource()
    monkeypatch.setattr(src, "fetch_fantasypros_rankings", _boom)
    monkeypatch.setattr(src, "fetch_espn_rankings", _espn)
    src.fetch_best_available(season=2024, verbose=False)
    assert seen["season"] == 2024


def test_verbose_false_stays_quiet(monkeypatch, capsys):
    src = RankingsSource()
    monkeypatch.setattr(src, "fetch_fantasypros_rankings", lambda **_: _one("fp"))
    src.fetch_best_available(verbose=False)
    assert capsys.readouterr().out == ""


# ── FantasyFootballCalculator ADP fallback ──────────────────────────


class _Resp:
    def __init__(self, status: int, payload: dict | None = None) -> None:
        self.status_code = status
        self._payload = payload or {}

    def json(self) -> dict:
        return self._payload

    def raise_for_status(self) -> None:
        raise RuntimeError(f"HTTP {self.status_code}")


_ADP_PAYLOAD = {
    "players": [
        {"player_id": "1", "name": "Deep Pick", "position": "WR",
         "team": "PHI", "adp": 45.0, "times_drafted": 10},
    ]
}


def _adp_session(supported: set[int], seen: list[int]):
    """Stub FFC: 200 only for team counts it publishes, 400 otherwise."""

    def _get(url, params=None, **_kwargs):
        teams = (params or {}).get("teams")
        seen.append(teams)
        if teams in supported:
            return _Resp(200, _ADP_PAYLOAD)
        return _Resp(400)

    return _get


def test_adp_falls_back_to_the_largest_published_board(monkeypatch):
    """FFC publishes 8/10/12/14 only, so a 32-team league must borrow one."""
    seen: list[int] = []
    src = RankingsSource()
    monkeypatch.setattr(src.session, "get", _adp_session({8, 10, 12, 14}, seen))

    ranked = src.fetch_fantasycalculator_adp(teams=32)
    assert seen[:2] == [32, 14]  # asked for its own size first
    assert ranked[0].source == "fantasyfootballcalculator-14t"


def test_adp_round_is_relative_to_the_league_not_the_source_board(monkeypatch):
    """Pick 45 is round 4 of a 14-team draft but round 2 of a 32-team one.

    The ordinal transfers between formats; the round it lands in does not.
    """
    src = RankingsSource()
    monkeypatch.setattr(src.session, "get", _adp_session({8, 10, 12, 14}, []))

    borrowed = src.fetch_fantasycalculator_adp(teams=32)[0]
    assert borrowed.adp == 45.0  # the ordinal is untouched
    assert borrowed.adp_round == 2  # ...but the round is this league's

    native = src.fetch_fantasycalculator_adp(teams=14)[0]
    assert native.adp_round == 4


def test_adp_no_fallback_needed_when_the_size_is_published(monkeypatch):
    seen: list[int] = []
    src = RankingsSource()
    monkeypatch.setattr(src.session, "get", _adp_session({8, 10, 12, 14}, seen))

    assert src.fetch_fantasycalculator_adp(teams=12)[0].source == (
        "fantasyfootballcalculator-12t"
    )
    assert seen == [12]  # no wasted requests


def test_adp_raises_when_no_board_answers(monkeypatch):
    src = RankingsSource()
    monkeypatch.setattr(src.session, "get", _adp_session(set(), []))
    with pytest.raises(RuntimeError):
        src.fetch_fantasycalculator_adp(teams=32)


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("fantasyfootballcalculator-14t", 14),
        ("fantasyfootballcalculator-8t", 8),
        ("fantasyfootballcalculator", None),  # no size recorded
        ("fantasypros", None),
        ("", None),
        ("my-own-ranks.csv", None),  # a user CSV carries no provenance
    ],
)
def test_adp_reference_teams_reads_the_source_label(source, expected):
    assert RankingsSource.adp_reference_teams(source) == expected


# --- Injury merge ---------------------------------------------------------


def _ranked(rank: int, name: str, pos: str, team: str = "LV") -> RankedPlayer:
    return RankedPlayer(
        rank=rank,
        player=Player(player_id=str(rank), full_name=name, position=pos, team=team),
        source="fantasypros",
    )


def _pool_player(
    name: str,
    pos: str,
    injury_status: str = "",
    body_part: str = "",
    status: str = "Active",
    team: str = "LV",
    active: bool = True,
) -> Player:
    return Player(
        player_id=name,
        full_name=name,
        position=pos,
        team=team,
        status=status,
        injury_status=injury_status,
        injury_body_part=body_part,
        active=active,
    )


def test_injury_merge_copies_designations_onto_the_board():
    src = RankingsSource()
    board = [_ranked(1, "Ashton Jeanty", "RB")]
    flagged = src.merge_injury_status(
        board, [_pool_player("Ashton Jeanty", "RB", "Questionable", "Knee")]
    )
    assert flagged == 1
    assert board[0].injury_status == "Questionable"
    assert board[0].injury_body_part == "Knee"


def test_injury_merge_leaves_a_healthy_player_alone():
    src = RankingsSource()
    board = [_ranked(1, "Ashton Jeanty", "RB")]
    flagged = src.merge_injury_status(board, [_pool_player("Ashton Jeanty", "RB")])
    assert flagged == 0
    assert board[0].injury_status == ""


def test_injury_merge_reports_zero_when_nothing_matched():
    """A failed match and a clean league must be distinguishable by the caller."""
    src = RankingsSource()
    board = [_ranked(1, "Ashton Jeanty", "RB")]
    assert src.merge_injury_status(board, []) == 0
    assert board[0].injury_status == ""


def test_injury_merge_survives_name_formatting_differences():
    """FantasyPros writes the suffix; Sleeper often does not."""
    src = RankingsSource()
    board = [_ranked(1, "Marvin Harrison Jr.", "WR")]
    src.merge_injury_status(
        board, [_pool_player("Marvin Harrison", "WR", "Questionable", "Foot")]
    )
    assert board[0].injury_status == "Questionable"


def test_injury_merge_carries_roster_standing_too():
    """IR shows up in `status` even when the game-day designation is empty."""
    src = RankingsSource()
    board = [_ranked(1, "Hurt Guy", "RB")]
    src.merge_injury_status(
        board, [_pool_player("Hurt Guy", "RB", status="Injured Reserve")]
    )
    assert board[0].player.status == "Injured Reserve"


def test_injury_merge_prefers_the_position_that_matches():
    """A name-only key collides across positions; the position breaks the tie."""
    src = RankingsSource()
    board = [_ranked(1, "Mike Williams", "WR")]
    src.merge_injury_status(
        board,
        [
            _pool_player("Mike Williams", "TE", "Out", "Achilles"),
            _pool_player("Mike Williams", "WR"),
        ],
    )
    assert board[0].injury_status == ""


def test_injury_merge_prefers_an_active_player_over_a_fringe_namesake():
    """Attaching a practice-squad player's injury to a starter is the bad case."""
    src = RankingsSource()
    board = [_ranked(1, "Chris Olave", "WR")]
    src.merge_injury_status(
        board,
        [
            _pool_player(
                "Chris Olave", "WR", "IR", "Knee - ACL", team="", active=False
            ),
            _pool_player("Chris Olave", "WR"),
        ],
    )
    assert board[0].injury_status == ""


def test_injury_merge_handles_a_pool_entry_with_no_usable_name():
    src = RankingsSource()
    board = [_ranked(1, "Ashton Jeanty", "RB")]
    flagged = src.merge_injury_status(
        board,
        [_pool_player("", "RB", "Out"), _pool_player("Ashton Jeanty", "RB", "Out")],
    )
    assert flagged == 1


def test_sleeper_player_parsing_reads_the_injury_fields():
    """The regression this merge depends on: from_sleeper used to drop them."""
    player = Player.from_sleeper(
        {
            "player_id": "1",
            "full_name": "Ashton Jeanty",
            "position": "RB",
            "team": "LV",
            "status": "Active",
            "injury_status": "Questionable",
            "injury_body_part": "Knee",
        }
    )
    assert player.injury_status == "Questionable"
    assert player.injury_body_part == "Knee"
    # Roster standing stays separate: a Questionable player is still "Active".
    assert player.status == "Active"


def test_sleeper_player_parsing_coalesces_explicit_nulls():
    """Sleeper sends the keys with null values rather than omitting them."""
    player = Player.from_sleeper(
        {
            "player_id": "1",
            "full_name": "Healthy Guy",
            "position": "RB",
            "injury_status": None,
            "injury_body_part": None,
        }
    )
    assert player.injury_status == ""
    assert player.injury_body_part == ""

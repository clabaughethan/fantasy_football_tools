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

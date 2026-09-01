"""Tests for Sleeper client - using mock responses."""

from unittest.mock import MagicMock, patch

from ff_tools.models.draft import DraftPick
from ff_tools.models.league import League, Roster
from ff_tools.models.player import Player
from ff_tools.sleeper import SleeperClient


def test_player_from_sleeper():
    data = {
        "player_id": "4046",
        "full_name": "Patrick Mahomes",
        "first_name": "Patrick",
        "last_name": "Mahomes",
        "position": "QB",
        "team": "KC",
        "age": 28,
        "active": True,
    }
    p = Player.from_sleeper(data)
    assert p.player_id == "4046"
    assert p.full_name == "Patrick Mahomes"
    assert p.position == "QB"
    assert p.team == "KC"


def test_league_from_sleeper():
    data = {
        "league_id": "12345",
        "name": "Test League",
        "status": "in_season",
        "season": "2025",
        "total_rosters": 10,
        "draft_id": "67890",
    }
    lg = League.from_sleeper(data)
    assert lg.league_id == "12345"
    assert lg.name == "Test League"
    assert lg.total_rosters == 10


def test_roster_from_sleeper():
    data = {
        "roster_id": 1,
        "owner_id": "user123",
        "players": ["4046", "4035"],
        "starters": ["4046"],
        "wins": 5,
        "losses": 3,
        "fpts": 1200.5,
    }
    r = Roster.from_sleeper(data)
    assert r.roster_id == 1
    assert len(r.players) == 2
    assert r.wins == 5
    assert r.fpts == 1200.5


def test_draft_pick_from_sleeper():
    data = {
        "draft_id": "draft1",
        "pick_no": 1,
        "round": 1,
        "roster_id": 1,
        "player_id": "4046",
        "picked_by": "user1",
    }
    p = DraftPick.from_sleeper(data)
    assert p.pick_no == 1
    assert p.player_id == "4046"


@patch("ff_tools.sleeper.client.requests.Session")
def test_client_get_user(mock_session_cls):
    mock_session = MagicMock()
    mock_response = MagicMock()
    mock_response.json.return_value = {"user_id": "123", "display_name": "testuser"}
    mock_session.get.return_value = mock_response
    mock_session_cls.return_value = mock_session

    client = SleeperClient()
    user = client.get_user("testuser")
    assert user["user_id"] == "123"
    mock_session.get.assert_called_once()

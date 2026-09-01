"""Tests for ESPN client and player lookup."""

from ff_tools.espn import ESPNClient
from ff_tools.models.player import Player
from ff_tools.utils.player_lookup import PlayerLookup


def test_player_from_espn():
    # players_wl format (flat)
    data = {
        "id": 3134977,
        "fullName": "Josh Allen",
        "firstName": "Josh",
        "lastName": "Allen",
        "defaultPositionId": 1,  # QB
        "proTeamId": 2,  # BUF
        "age": 28,
    }
    p = Player.from_espn(data)
    assert p.player_id == "3134977"
    assert p.full_name == "Josh Allen"
    assert p.position == "QB"
    assert p.team == "BUF"
    assert p.espn_id == 3134977


def test_player_from_espn_kona():
    # kona_player_info format (nested)
    data = {
        "player": {
            "id": 4429795,
            "fullName": "Jahmyr Gibbs",
            "firstName": "Jahmyr",
            "lastName": "Gibbs",
            "defaultPositionId": 2,  # RB
            "proTeamId": 8,  # DET
            "ownership": {
                "percentOwned": 99.93,
                "auctionValueAverage": 70.41,
                "averageDraftPosition": 1.34,
            },
        }
    }
    p = Player.from_espn(data)
    assert p.player_id == "4429795"
    assert p.full_name == "Jahmyr Gibbs"
    assert p.position == "RB"
    assert p.team == "DET"
    assert p.ownership_pct == 99.93
    assert p.adp == 1.34
    # An auction dollar value is a market price, not a points projection.
    assert p.auction_value == 70.41
    # No stat block in this payload, so there is no projection to report.
    assert p.projected_points == 0.0


def test_player_from_espn_season_projection():
    """Season projections come from statSourceId=1 / statSplitTypeId=0."""
    data = {
        "player": {
            "id": 4429795,
            "fullName": "Jahmyr Gibbs",
            "defaultPositionId": 2,
            "proTeamId": 8,
            "stats": [
                # Weekly projection - must be ignored.
                {
                    "statSourceId": 1,
                    "statSplitTypeId": 1,
                    "scoringPeriodId": 13,
                    "seasonId": 2026,
                    "appliedTotal": 17.9,
                },
                # Actual season total - must be ignored.
                {
                    "statSourceId": 0,
                    "statSplitTypeId": 0,
                    "scoringPeriodId": 0,
                    "seasonId": 2025,
                    "appliedTotal": 289.9,
                },
                # Prior-season projection.
                {
                    "statSourceId": 1,
                    "statSplitTypeId": 0,
                    "scoringPeriodId": 0,
                    "seasonId": 2025,
                    "appliedTotal": 256.9,
                },
                # Upcoming-season projection - the one we want.
                {
                    "statSourceId": 1,
                    "statSplitTypeId": 0,
                    "scoringPeriodId": 0,
                    "seasonId": 2026,
                    "appliedTotal": 297.1,
                },
            ],
        }
    }
    # Explicit season wins.
    assert Player.from_espn(data, season=2025).projected_points == 256.9
    assert Player.from_espn(data, season=2026).projected_points == 297.1
    # Without a season, the latest projected season wins.
    assert Player.from_espn(data).projected_points == 297.1


def test_draft_pick_from_espn():
    data = {
        "overallPickNumber": 1,
        "roundId": 1,
        "player": {
            "id": 3134977,
            "fullName": "Josh Allen",
            "position": {"abbreviation": "QB"},
            "proTeam": {"abbreviation": "BUF"},
        },
    }
    p = Player.from_espn(data["player"])
    assert p.full_name == "Josh Allen"


def test_player_lookup():
    lookup = PlayerLookup()

    sleeper_players = {
        "4046": Player(
            player_id="4046",
            full_name="Patrick Mahomes",
            position="QB",
            team="KC",
        )
    }
    espn_players = [
        Player(
            player_id="3139477",
            full_name="Patrick Mahomes",
            position="QB",
            team="KC",
            espn_id=3139477,
        )
    ]

    lookup.add_sleeper_players(sleeper_players)
    lookup.add_espn_players(espn_players)

    # Forward lookups
    assert lookup.get_sleeper_player("Patrick Mahomes") is not None
    assert lookup.get_espn_player("Patrick Mahomes") is not None

    # Cross-platform
    espn_id = lookup.get_espn_id_from_sleeper("4046")
    assert espn_id == 3139477

    sleeper_id = lookup.get_sleeper_id_from_espn(3139477)
    assert sleeper_id == "4046"


def test_client_no_auth():
    client = ESPNClient()
    assert client.cookies == {}


def test_client_with_auth():
    client = ESPNClient(espn_s2="test_s2", swid="{test_swid}")
    assert client.cookies["espn_s2"] == "test_s2"
    assert client.cookies["SWID"] == "{test_swid}"

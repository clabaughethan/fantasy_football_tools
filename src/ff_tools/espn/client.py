"""ESPN Fantasy Football API client - unofficial/undocumented.

Public leagues: no auth needed.
Private leagues: pass espn_s2 and SWID cookies.
"""

from __future__ import annotations

import json

import requests

from ff_tools.models.draft import DraftPick
from ff_tools.models.league import League, Roster
from ff_tools.models.player import ESPN_LINEUP_SLOTS, Player

BASE_URL = "https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl"

# Lineup slot IDs at or above this are bench, IR and other inactive spots.
_FIRST_INACTIVE_SLOT_ID = 20

# ESPN's canned scoring formats, used by the `leaguedefaults` player endpoint.
_SCORING_IDS = {"standard": 0, "half_ppr": 1, "ppr": 2}


def _roster_positions(settings: dict) -> list[str]:
    """Expand ESPN's lineupSlotCounts into a flat roster_positions list.

    ESPN reports the lineup as a `{slotId: count}` map, not the array of slot
    objects an earlier version of this client expected - which is why
    `roster_positions` always came back empty. Repeating each label `count`
    times matches Sleeper's representation so downstream code can share it.
    """
    counts = (settings.get("rosterSettings") or {}).get("lineupSlotCounts") or {}
    positions: list[str] = []
    for raw_slot, raw_count in sorted(counts.items(), key=lambda kv: int(kv[0])):
        try:
            slot_id = int(raw_slot)
            count = int(raw_count)
        except (TypeError, ValueError):
            continue
        if count <= 0:
            continue
        label = ESPN_LINEUP_SLOTS.get(slot_id)
        if not label:
            continue
        positions.extend([label] * count)
    return positions


class ESPNClient:
    """Client for the ESPN Fantasy Football API.

    This is an unofficial, undocumented API. Endpoints may change without notice.

    For private leagues, obtain cookies from your browser:
    1. Log into espn.com/fantasy
    2. DevTools -> Application -> Cookies -> espn.com
    3. Copy `espn_s2` and `SWID` values
    """

    def __init__(
        self,
        espn_s2: str | None = None,
        swid: str | None = None,
        timeout: int = 10,
    ) -> None:
        self.session = requests.Session()
        self.timeout = timeout
        self.cookies = {}
        if espn_s2:
            self.cookies["espn_s2"] = espn_s2
        if swid:
            self.cookies["SWID"] = swid

    def _get(self, url: str, headers: dict | None = None, **kwargs) -> dict | list:
        """Make a GET request to the ESPN API."""
        resp = self.session.get(
            url,
            cookies=self.cookies,
            headers=headers or {},
            timeout=self.timeout,
            **kwargs,
        )
        resp.raise_for_status()
        return resp.json()

    def _league_url(self, league_id: str, season: str | int) -> str:
        """Build league endpoint URL for current era (2018+)."""
        return (
            f"{BASE_URL}/seasons/{season}/segments/0/leagues/{league_id}"
        )

    def _league_url_history(self, league_id: str, season: str | int) -> str:
        """Build league endpoint URL for historical seasons (pre-2018)."""
        return (
            f"{BASE_URL}/leagueHistory/{league_id}?seasonId={season}"
        )

    def _get_league(
        self, league_id: str, season: str | int, views: list[str], filters: dict | None = None
    ) -> dict:
        """Fetch league data with specified views."""
        season_int = int(season)
        if season_int >= 2018:
            url = self._league_url(league_id, season)
        else:
            url = self._league_url_history(league_id, season)

        params = [("view", v) for v in views]
        headers = {}
        if filters:
            headers["X-Fantasy-Filter"] = json.dumps(filters)

        return self._get(url, params=params, headers=headers)

    # ── League endpoints ────────────────────────────────────────────

    def get_league(self, league_id: str, season: str | int) -> League:
        """Get basic league info."""
        data = self._get_league(league_id, season, ["mSettings"])
        settings = data.get("settings") or {}
        return League(
            league_id=league_id,
            name=settings.get("name", ""),
            status="active",
            season=str(season),
            total_rosters=settings.get("size", 0),
            scoring_settings=settings.get("scoringSettings", {}),
            roster_positions=_roster_positions(settings),
            espn_id=int(league_id) if league_id.isdigit() else None,
        )

    def get_teams(self, league_id: str, season: str | int) -> list[dict]:
        """Get all teams in a league."""
        data = self._get_league(league_id, season, ["mTeam"])
        return data.get("teams", [])

    def get_rosters(self, league_id: str, season: str | int) -> list[Roster]:
        """Get all rosters in a league."""
        data = self._get_league(league_id, season, ["mRoster"])
        rosters = []
        for team in data.get("teams", []):
            roster_data = team.get("roster", {})
            rosters.append(
                Roster(
                    roster_id=team.get("id", 0),
                    owner_id=str(team.get("primaryOwner", "")),
                    players=[str(p.get("playerId", "")) for p in roster_data.get("entries", [])],
                    starters=[
                        str(e.get("playerId", ""))
                        for e in roster_data.get("entries", [])
                        if e.get("lineupSlotId", 0) < _FIRST_INACTIVE_SLOT_ID
                    ],
                    display_name=team.get("abbrev", ""),
                    team_name=team.get("name", ""),
                )
            )
        return rosters

    def get_standings(self, league_id: str, season: str | int) -> list[dict]:
        """Get standings with W/L/PF/PA."""
        data = self._get_league(league_id, season, ["mStandings"])
        return data.get("standings", {}).get("entries", [])

    # ── Draft endpoints ─────────────────────────────────────────────

    def get_draft(self, league_id: str, season: str | int) -> list[DraftPick]:
        """Get the full draft board."""
        data = self._get_league(league_id, season, ["mDraftDetail"])
        picks = []
        for pick in data.get("draftDetail", {}).get("picks", []):
            picks.append(DraftPick.from_espn(pick))
        return picks

    # ── Matchup endpoints ───────────────────────────────────────────

    def get_matchups(self, league_id: str, season: str | int, scoring_period: int) -> list[dict]:
        """Get matchups for a specific scoring period (week)."""
        filter_obj = {
            "schedule": {
                "filterMatchupPeriodIds": {"value": [scoring_period]}
            }
        }
        data = self._get_league(league_id, season, ["mMatchup"], filter_obj)
        return data.get("schedule", [])

    def get_live_scoring(self, league_id: str, season: str | int, scoring_period: int) -> dict:
        """Get live scoring for a week."""
        filter_obj = {
            "liveScoring": {
                "filterScoringPeriodIds": {"value": [scoring_period]}
            }
        }
        return self._get_league(league_id, season, ["mLiveScoring"], filter_obj)

    # ── Player endpoints ────────────────────────────────────────────

    def get_players(
        self,
        season: str | int,
        scoring_period: int | None = None,
        limit: int = 500,
    ) -> list[Player]:
        """Get available players for a season.

        Requires X-Fantasy-Filter header to return more than 50 results.
        """
        url = f"{BASE_URL}/seasons/{season}/players"
        params = {"view": "players_wl"}
        if scoring_period is not None:
            params["scoringPeriodId"] = scoring_period

        filter_obj = {
            "players": {"limit": limit},
            "filterActive": {"value": True},
        }
        headers = {"X-Fantasy-Filter": json.dumps(filter_obj)}

        data = self._get(url, params=params, headers=headers)
        players = []
        # players_wl returns a list directly; kona_player_info returns a dict
        player_list = data if isinstance(data, list) else data.get("players", [])
        for pdata in player_list:
            players.append(Player.from_espn(pdata))
        return players

    def get_player_info(
        self, season: str | int, scoring: str = "half_ppr"
    ) -> list[Player]:
        """Get detailed player info with projections and ownership.

        This is the `leaguedefaults` endpoint, so results reflect one of ESPN's
        canned scoring formats rather than any particular league's settings. It
        previously took a `league_id` and ignored it, which invited the
        assumption that the numbers were league-specific.
        """
        ppr_id = _SCORING_IDS.get(scoring)
        if ppr_id is None:
            raise ValueError(
                f"scoring must be one of {sorted(_SCORING_IDS)}, got {scoring!r}"
            )
        url = f"{BASE_URL}/seasons/{season}/segments/0/leaguedefaults/{ppr_id}"
        params = {"view": "kona_player_info"}
        filter_obj = {
            "players": {
                "limit": 2000,
                "sortPercOwned": {"sortPriority": 4, "sortAsc": False},
            }
        }
        headers = {"X-Fantasy-Filter": json.dumps(filter_obj)}

        data = self._get(url, params=params, headers=headers)
        players = []
        player_list = data if isinstance(data, list) else data.get("players", [])
        for pdata in player_list:
            players.append(Player.from_espn(pdata))
        return players

    def get_pro_team_schedules(self, season: str | int) -> dict:
        """Get NFL team schedules (useful for bye weeks)."""
        url = f"{BASE_URL}/seasons/{season}"
        params = {"view": "proTeamSchedules_wl"}
        return self._get(url, params=params)

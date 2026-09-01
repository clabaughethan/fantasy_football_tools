"""Sleeper API client - read-only, no auth required."""

from __future__ import annotations

import time

import requests

from ff_tools.models.draft import Draft, DraftPick
from ff_tools.models.league import League, Matchup, Roster
from ff_tools.models.player import Player

BASE_URL = "https://api.sleeper.app/v1"


class SleeperClient:
    """Client for the Sleeper fantasy football API.

    All endpoints are read-only and require no authentication.
    Rate limit: ~90 requests/minute.
    """

    def __init__(self, timeout: int = 10) -> None:
        self.session = requests.Session()
        self.timeout = timeout
        self._players_cache: dict[str, Player] | None = None
        self._players_cache_time: float = 0

    def _get(self, path: str, **kwargs) -> dict | list:
        """Make a GET request to the Sleeper API."""
        resp = self.session.get(f"{BASE_URL}{path}", timeout=self.timeout, **kwargs)
        resp.raise_for_status()
        return resp.json()

    # ── User endpoints ──────────────────────────────────────────────

    def get_user(self, username: str) -> dict:
        """Resolve a username to a user object."""
        return self._get(f"/user/{username}")

    def get_user_leagues(self, user_id: str, season: str | int) -> list[League]:
        """Get all leagues for a user."""
        data = self._get(f"/user/{user_id}/leagues/nfl/{season}")
        return [League.from_sleeper(lg) for lg in data]

    def get_user_drafts(self, user_id: str, season: str | int) -> list[Draft]:
        """Get all drafts for a user."""
        data = self._get(f"/user/{user_id}/drafts/nfl/{season}")
        return [Draft.from_sleeper(d) for d in data]

    # ── League endpoints ────────────────────────────────────────────

    def get_league(self, league_id: str) -> League:
        """Get league details."""
        return League.from_sleeper(self._get(f"/league/{league_id}"))

    def get_league_rosters(self, league_id: str) -> list[Roster]:
        """Get all rosters in a league."""
        data = self._get(f"/league/{league_id}/rosters")
        return [Roster.from_sleeper(r) for r in data]

    def get_league_users(self, league_id: str) -> list[dict]:
        """Get all users in a league (display names, team names)."""
        return self._get(f"/league/{league_id}/users")

    def get_matchups(self, league_id: str, week: int) -> list[Matchup]:
        """Get all matchups for a given week."""
        data = self._get(f"/league/{league_id}/matchups/{week}")
        return [Matchup.from_sleeper(m) for m in data]

    def get_transactions(self, league_id: str, round_num: int) -> list[dict]:
        """Get all transactions (adds, drops, waivers, trades) for a round."""
        return self._get(f"/league/{league_id}/transactions/{round_num}")

    def get_traded_picks(self, league_id: str) -> list[dict]:
        """Get all traded draft picks in a league."""
        return self._get(f"/league/{league_id}/traded_picks")

    def get_playoff_bracket(self, league_id: str, bracket: str = "winners") -> list[dict]:
        """Get playoff bracket. bracket is 'winners' or 'losers'."""
        key = "winners_bracket" if bracket == "winners" else "losers_bracket"
        return self._get(f"/league/{league_id}/{key}")

    # ── Draft endpoints ─────────────────────────────────────────────

    def get_draft(self, draft_id: str) -> Draft:
        """Get draft details."""
        return Draft.from_sleeper(self._get(f"/draft/{draft_id}"))

    def get_draft_picks(self, draft_id: str) -> list[DraftPick]:
        """Get all picks in a draft."""
        data = self._get(f"/draft/{draft_id}/picks")
        return [DraftPick.from_sleeper(p) for p in data]

    def get_draft_traded_picks(self, draft_id: str) -> list[dict]:
        """Get all traded picks in a draft."""
        return self._get(f"/draft/{draft_id}/traded_picks")

    # ── Player endpoints ────────────────────────────────────────────

    def get_players(
        self,
        position: str | None = None,
        active: bool | None = None,
        cache: bool = True,
    ) -> dict[str, Player]:
        """Get all NFL players.

        The full player list is ~14MB. It's cached in-memory for 24 hours.
        Use position/active filters to reduce response size.
        """
        now = time.time()
        if cache and self._players_cache and (now - self._players_cache_time) < 86400:
            return self._players_cache

        params = {}
        if position:
            params["position"] = position
        if active is not None:
            params["active"] = str(active).lower()

        resp = self.session.get(
            f"{BASE_URL}/players/nfl", params=params, timeout=self.timeout
        )
        resp.raise_for_status()
        raw = resp.json()

        players = {}
        for pid, pdata in raw.items():
            pdata["player_id"] = pid
            players[pid] = Player.from_sleeper(pdata)

        if cache:
            self._players_cache = players
            self._players_cache_time = now

        return players

    def get_trending_players(
        self, trend: str = "add", lookback_hours: int = 24, limit: int = 25
    ) -> list[dict]:
        """Get trending players (adds or drops) in the last N hours."""
        return self._get(
            f"/players/nfl/trending/{trend}",
            params={"lookback_hours": lookback_hours, "limit": limit},
        )

    # ── State endpoints ─────────────────────────────────────────────

    def get_nfl_state(self) -> dict:
        """Get current NFL state (season, week, scoring status)."""
        return self._get("/state/nfl")

"""Cross-platform player ID resolution.

Sleeper and ESPN use different player IDs. This module provides lookup
by player name to map between the two systems.
"""

from __future__ import annotations

from ff_tools.models.player import Player


class PlayerLookup:
    """Map players between Sleeper and ESPN by name.

    Since both platforms use proprietary IDs, we index by normalized
    player name to provide cross-platform lookup.
    """

    def __init__(self) -> None:
        self._by_name: dict[str, dict[str, Player]] = {}  # name -> {platform: Player}
        self._sleeper: dict[str, Player] = {}
        self._espn: dict[int, Player] = {}

    @staticmethod
    def _normalize(name: str) -> str:
        """Normalize a player name for lookup."""
        return name.strip().lower()

    def add_sleeper_players(self, players: dict[str, Player]) -> None:
        """Index Sleeper players."""
        for pid, player in players.items():
            self._sleeper[pid] = player
            key = self._normalize(player.full_name)
            if key not in self._by_name:
                self._by_name[key] = {}
            self._by_name[key]["sleeper"] = player

    def add_espn_players(self, players: list[Player]) -> None:
        """Index ESPN players."""
        for player in players:
            if player.espn_id:
                self._espn[player.espn_id] = player
            key = self._normalize(player.full_name)
            if key not in self._by_name:
                self._by_name[key] = {}
            self._by_name[key]["espn"] = player

    def get_sleeper_player(self, name: str) -> Player | None:
        """Look up a Sleeper player by name."""
        entry = self._by_name.get(self._normalize(name), {})
        return entry.get("sleeper")

    def get_espn_player(self, name: str) -> Player | None:
        """Look up an ESPN player by name."""
        entry = self._by_name.get(self._normalize(name), {})
        return entry.get("espn")

    def get_sleeper_id_from_espn(self, espn_id: int) -> str | None:
        """Get Sleeper player ID from ESPN player ID."""
        espn_player = self._espn.get(espn_id)
        if not espn_player:
            return None
        sleeper_player = self.get_sleeper_player(espn_player.full_name)
        return sleeper_player.player_id if sleeper_player else None

    def get_espn_id_from_sleeper(self, sleeper_id: str) -> int | None:
        """Get ESPN player ID from Sleeper player ID."""
        sleeper_player = self._sleeper.get(sleeper_id)
        if not sleeper_player:
            return None
        espn_player = self.get_espn_player(sleeper_player.full_name)
        return espn_player.espn_id if espn_player else None

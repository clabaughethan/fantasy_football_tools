"""Trending player analysis from Sleeper."""

from __future__ import annotations

from dataclasses import dataclass

from ff_tools.sleeper.client import SleeperClient


@dataclass
class TrendingPlayer:
    """A player trending on Sleeper with add/drop counts."""

    player_id: str
    count: int
    trend: str  # "add" or "drop"
    player_name: str = ""
    position: str = ""
    team: str = ""


class TrendingAnalyzer:
    """Analyze trending players on Sleeper.

    Fetches platform-wide add/drop activity to identify
    players generating buzz.
    """

    def __init__(self, client: SleeperClient | None = None) -> None:
        self.client = client or SleeperClient()

    def get_trending(
        self,
        trend: str = "add",
        lookback_hours: int = 168,  # 7 days
        limit: int = 50,
    ) -> list[TrendingPlayer]:
        """Get trending players from Sleeper."""
        raw = self.client.get_trending_players(trend, lookback_hours, limit)

        # Fetch player data to resolve names
        players = self.client.get_players(cache=True)

        results = []
        for t in raw:
            pid = t.get("player_id", "")
            player = players.get(pid)
            results.append(
                TrendingPlayer(
                    player_id=pid,
                    count=t.get("count", 0),
                    trend=trend,
                    player_name=player.full_name if player else pid,
                    position=player.position if player else "",
                    team=player.team if player else "",
                )
            )
        return results

    def get_hot_adds(self, days: int = 7, limit: int = 20) -> list[TrendingPlayer]:
        """Get the most-added players in the last N days."""
        return self.get_trending("add", lookback_hours=days * 24, limit=limit)

    def get_hot_drops(self, days: int = 7, limit: int = 20) -> list[TrendingPlayer]:
        """Get the most-dropped players in the last N days."""
        return self.get_trending("drop", lookback_hours=days * 24, limit=limit)

    def get_net_trending(self, days: int = 7, limit: int = 30) -> list[dict]:
        """Get net trending (adds minus drops) for each player.

        Players with high net adds are being picked up more than dropped.
        """
        adds = self.get_trending("add", days * 24, limit)
        drops = self.get_trending("drop", days * 24, limit)

        add_map = {t.player_id: t.count for t in adds}
        drop_map = {t.player_id: t.count for t in drops}

        # Combine
        all_ids = set(add_map.keys()) | set(drop_map.keys())
        net = []
        for pid in all_ids:
            net_count = add_map.get(pid, 0) - drop_map.get(pid, 0)
            if net_count > 0:
                player = None
                for t in adds:
                    if t.player_id == pid:
                        player = t
                        break
                if not player:
                    for t in drops:
                        if t.player_id == pid:
                            player = t
                            break

                net.append({
                    "player_id": pid,
                    "player_name": player.player_name if player else pid,
                    "position": player.position if player else "",
                    "team": player.team if player else "",
                    "adds": add_map.get(pid, 0),
                    "drops": drop_map.get(pid, 0),
                    "net": net_count,
                })

        return sorted(net, key=lambda x: -x["net"])[:limit]

    def print_trending_board(self, days: int = 7) -> None:
        """Print a formatted trending board."""
        print("\n" + "=" * 60)
        print(f"  TRENDING PLAYERS (last {days} days)")
        print("=" * 60)

        net = self.get_net_trending(days)

        print("\n  TOP ADDS (net adds):")
        for i, p in enumerate(net[:10], 1):
            print(
                f"    {i:2d}. {p['player_name']:<25s} {p['position']:<4s} "
                f"{p['team']:<4s} +{p['adds']}/{p['drops']} "
                f"(net: +{p['net']})"
            )

        print("\n" + "=" * 60)

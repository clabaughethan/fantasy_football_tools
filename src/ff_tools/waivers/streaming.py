"""Streaming recommendations for QB, DEF, and K positions."""

from __future__ import annotations

from dataclasses import dataclass

from ff_tools.draft.rankings import RankedPlayer


@dataclass
class StreamingOption:
    """A streaming recommendation for a specific position and week."""

    player: RankedPlayer
    week: int
    projected_points: float = 0.0
    opponent: str = ""
    home_away: str = ""  # "home" or "away"
    reasoning: str = ""


class StreamingAnalyzer:
    """Analyze streaming options for QB, DEF, and K.

    Uses projected points from rankings (which factor in matchups)
    to recommend the best short-term pickup.
    """

    def get_streaming_options(
        self,
        available_players: list[RankedPlayer],
        position: str,
        weeks_ahead: int = 1,
    ) -> dict[int, list[StreamingOption]]:
        """Get streaming options for a position, grouped by week.

        Args:
            available_players: All available ranked players
            position: QB, DEF, or K
            weeks_ahead: How many weeks to project ahead

        Returns:
            Dict of week number -> list of streaming options
        """
        # Filter to position
        position_players = [
            rp for rp in available_players if rp.position == position
        ]

        # Sort by projected points (which already factor in matchups)
        position_players.sort(
            key=lambda rp: rp.projected_points, reverse=True
        )

        options: dict[int, list[StreamingOption]] = {}
        for week in range(1, weeks_ahead + 1):
            week_options = []
            for rp in position_players[:5]:  # Top 5 options
                week_options.append(
                    StreamingOption(
                        player=rp,
                        week=week,
                        projected_points=rp.projected_points,
                        reasoning=(
                            f"Ranked #{rp.rank} | "
                            f"{rp.projected_points:.1f} projected pts"
                        ),
                    )
                )
            options[week] = week_options

        return options

    def get_best_stream(
        self,
        available_players: list[RankedPlayer],
        position: str,
    ) -> StreamingOption | None:
        """Get the single best streaming option for a position."""
        options = self.get_streaming_options(available_players, position, weeks_ahead=1)
        week_options = options.get(1, [])
        return week_options[0] if week_options else None

    def get_all_streams(
        self,
        available_players: list[RankedPlayer],
    ) -> dict[str, StreamingOption | None]:
        """Get the best streaming option at each streamable position."""
        result = {}
        for pos in ["QB", "DEF", "K"]:
            result[pos] = self.get_best_stream(available_players, pos)
        return result

    def print_streaming_board(
        self,
        available_players: list[RankedPlayer],
    ) -> None:
        """Print a formatted streaming recommendations board."""
        print("\n" + "=" * 60)
        print("  STREAMING RECOMMENDATIONS")
        print("=" * 60)

        streams = self.get_all_streams(available_players)

        for pos in ["QB", "DEF", "K"]:
            option = streams.get(pos)
            print(f"\n  {pos}:")
            if option:
                rp = option.player
                print(
                    f"    {rp.display_name:<25s} {rp.team:<4s} "
                    f"| {option.projected_points:.1f} proj pts "
                    f"| Rank #{rp.rank}"
                )
            else:
                print("    No options available")

        print("\n" + "=" * 60)

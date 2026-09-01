"""CLI entry point for the guillotine draft assistant.

Usage:
    python -m ff_tools.guillotine --league-id 12345 --username yourname
    python -m ff_tools.guillotine --league-id 12345 --username yourname --rankings my_rankings.csv
    python -m ff_tools.guillotine --league-id 12345 --username yourname --roster-size 20
"""

from __future__ import annotations

import argparse
import sys

from ff_tools.guillotine.assistant import GuillotineDraftAssistant


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Live draft assistant for guillotine fantasy football leagues"
    )
    parser.add_argument(
        "--league-id", required=True, help="Sleeper league ID"
    )
    parser.add_argument(
        "--username", required=True, help="Your Sleeper username"
    )
    parser.add_argument(
        "--rankings",
        help="Path to CSV rankings file (rank,name,position,team,projected_points)",
    )
    parser.add_argument(
        "--poll-interval",
        type=float,
        default=3.0,
        help="Seconds between API polls (default: 3)",
    )
    # Both default to 0, meaning "read it from the league". Passing a real
    # default here would override the league's own settings every run.
    parser.add_argument(
        "--roster-size",
        type=int,
        default=0,
        help="Roster spots per team (default: read from the league)",
    )
    parser.add_argument(
        "--rounds",
        type=int,
        default=0,
        help="Total draft rounds (default: read from the draft settings)",
    )
    parser.add_argument(
        "--espn-s2", help="ESPN espn_s2 cookie for private league rankings"
    )
    parser.add_argument(
        "--swid", help="ESPN SWID cookie for private league rankings"
    )

    args = parser.parse_args()

    assistant = GuillotineDraftAssistant(
        league_id=args.league_id,
        username=args.username,
        poll_interval=args.poll_interval,
        roster_size=args.roster_size,
        espn_s2=args.espn_s2,
        swid=args.swid,
    )

    try:
        print(f"Setting up guillotine draft assistant for league {args.league_id}...")
        assistant.setup(rankings_csv=args.rankings, total_rounds=args.rounds)
        print(f"Connected! Tracking draft for user: {assistant.user_display_name}")
        # Report what was resolved, not what was asked for - the interesting
        # case is the one where these came from the league rather than the flags.
        rounds = assistant.board.total_rounds if assistant.board else args.rounds
        print(
            f"League size: {assistant.total_teams} teams | "
            f"Roster: {assistant.roster_size} spots | Rounds: {rounds}"
        )
        print(f"Polling every {args.poll_interval}s. Press Ctrl+C to stop.\n")
        assistant.run()
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()

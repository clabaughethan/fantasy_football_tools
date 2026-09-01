"""CLI entry point for the draft assistant.

Usage:
    python -m ff_tools.draft --league-id 12345 --username yourname
    python -m ff_tools.draft --league-id 12345 --username yourname --rankings my_rankings.csv
    python -m ff_tools.draft --league-id 12345 --username yourname --poll-interval 5
"""

from __future__ import annotations

import argparse
import sys

from ff_tools.draft.assistant import DraftAssistant


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Live draft assistant for Sleeper fantasy football leagues"
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
    parser.add_argument(
        "--espn-s2", help="ESPN espn_s2 cookie for private league rankings"
    )
    parser.add_argument(
        "--swid", help="ESPN SWID cookie for private league rankings"
    )

    args = parser.parse_args()

    assistant = DraftAssistant(
        league_id=args.league_id,
        username=args.username,
        poll_interval=args.poll_interval,
        espn_s2=args.espn_s2,
        swid=args.swid,
    )

    try:
        print(f"Setting up draft assistant for league {args.league_id}...")
        assistant.setup(rankings_csv=args.rankings)
        print(f"Connected! Tracking draft for user: {assistant.user_display_name}")
        print(f"Polling every {args.poll_interval}s. Press Ctrl+C to stop.\n")
        assistant.run()
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()

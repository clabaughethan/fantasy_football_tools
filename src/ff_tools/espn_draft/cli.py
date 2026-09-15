"""CLI entry point for the ESPN draft assistant.

Usage:
    python -m ff_tools.espn_draft --league-id 12345 --season 2025 --team-id 1
    python -m ff_tools.espn_draft --league-id 12345 --season 2025 --list-teams
"""

from __future__ import annotations

import argparse
import sys

from ff_tools.espn_draft.assistant import ESPNDraftAssistant


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Live draft assistant for ESPN fantasy football leagues"
    )
    parser.add_argument(
        "--league-id", required=True, help="ESPN league ID"
    )
    parser.add_argument(
        "--season", required=True, help="Season year (e.g. 2025)"
    )
    parser.add_argument(
        "--team-id", type=int, help="Your ESPN team ID (run with --list-teams to find it)"
    )
    parser.add_argument(
        "--list-teams", action="store_true", help="List all teams in the league and exit"
    )
    parser.add_argument(
        "--rankings",
        help="Path to CSV rankings file (rank,name,position,team,projected_points)",
    )
    parser.add_argument(
        "--poll-interval",
        type=float,
        default=15.0,
        help="Seconds between API polls (default: 15)",
    )
    parser.add_argument(
        "--espn-s2", help="ESPN espn_s2 cookie (required for private leagues)"
    )
    parser.add_argument(
        "--swid", help="ESPN SWID cookie (required for private leagues)"
    )
    parser.add_argument(
        "--rec-count",
        type=int,
        default=10,
        help="Number of overall recommendations to show on your turn (default: 10)",
    )
    parser.add_argument(
        "--preview",
        type=int,
        default=3,
        help="Detailed preview when N picks away; otherwise always shows lookahead forecast to your next pick (default: 3, 0=status only)",
    )

    args = parser.parse_args()

    # List teams mode
    if args.list_teams:
        _list_teams(args.league_id, args.season, args.espn_s2, args.swid)
        return

    if not args.team_id:
        print(
            "Error: --team-id is required (use --list-teams to find your team ID)",
            file=sys.stderr,
        )
        sys.exit(1)

    assistant = ESPNDraftAssistant(
        league_id=args.league_id,
        season=args.season,
        espn_s2=args.espn_s2,
        swid=args.swid,
        poll_interval=args.poll_interval,
        team_id=args.team_id,
        rec_count=args.rec_count,
        preview=args.preview,
    )

    try:
        print(f"Setting up ESPN draft assistant for league {args.league_id}...")
        assistant.setup(rankings_csv=args.rankings)
        print(f"Connected! Tracking draft for team: {assistant.user_team_name}")
        print(f"Polling every {args.poll_interval}s. Press Ctrl+C to stop.\n")
        assistant.run()
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        print("\nStopped.")


def _list_teams(
    league_id: str, season: str | int, espn_s2: str | None, swid: str | None
) -> None:
    """List all teams in the league."""
    from ff_tools.espn.client import ESPNClient

    client = ESPNClient(espn_s2=espn_s2, swid=swid)
    data = client._get_league(league_id, season, ["mTeam"])
    teams = data.get("teams", [])

    print(f"\nTeams in league {league_id} ({season}):")
    print("-" * 50)
    for team in sorted(teams, key=lambda t: t.get("id", 0)):
        tid = team.get("id", "?")
        abbrev = team.get("abbrev", "")
        name = team.get("name", "")
        print(f"  Team ID {tid:>3}: {abbrev:>4s} - {name}")
    print()
    print("Use the Team ID with --team-id to identify yourself.")


if __name__ == "__main__":
    main()

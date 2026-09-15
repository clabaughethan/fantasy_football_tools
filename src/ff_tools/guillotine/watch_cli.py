"""CLI entry point for the guillotine elimination watch.

Usage:
    python -m ff_tools.guillotine.watch_cli --league-id 12345 --week 1
    python -m ff_tools.guillotine.watch_cli --league-id 12345 --week 1 --bottom 6
    python -m ff_tools.guillotine.watch_cli --league-id 12345 --week 1 --username myname
"""

from __future__ import annotations

import argparse
import sys

from dotenv import load_dotenv

load_dotenv()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Monday-night elimination watch for guillotine leagues"
    )
    parser.add_argument("--league-id", required=True, help="Sleeper league ID")
    parser.add_argument("--week", type=int, required=True, help="Current week")
    parser.add_argument(
        "--bottom", type=int, default=8, help="At-risk teams to show (default: 8)"
    )
    parser.add_argument(
        "--cuts", type=int, default=1, help="Teams chopped this week (default: 1)"
    )
    parser.add_argument("--username", help="Your Sleeper username (marks your team)")
    parser.add_argument("--rankings", help="Path to CSV rankings file")

    args = parser.parse_args()

    from ff_tools.draft.rankings import RankingsSource
    from ff_tools.guillotine.watch import build_watch, print_watch
    from ff_tools.sleeper.client import SleeperClient

    client = SleeperClient()

    league = client.get_league(args.league_id)
    if not league or league.league_id != args.league_id:
        print(f"Error: League {args.league_id} not found", file=sys.stderr)
        sys.exit(1)

    user_roster_id: int | None = None
    if args.username:
        try:
            user = client.get_user(args.username)
            rosters = client.get_league_rosters(args.league_id)
            for r in rosters:
                if r.owner_id == user.get("user_id", ""):
                    user_roster_id = r.roster_id
                    break
        except Exception:
            pass

    matchups = client.get_matchups(args.league_id, args.week)
    rosters = client.get_league_rosters(args.league_id)
    users = {
        u["user_id"]: u.get("display_name", "?")
        for u in client.get_league_users(args.league_id)
    }
    players = client.get_players(cache=True)

    rankings_source = RankingsSource()
    if args.rankings:
        rankings = rankings_source.load_csv(args.rankings)
    else:
        try:
            rankings = rankings_source.fetch_best_available(verbose=False)
        except Exception:
            try:
                rankings = rankings_source.fetch_espn_rankings()
            except Exception:
                rankings = rankings_source.fetch_sleeper_rankings()

    watch = build_watch(
        matchups,
        rosters,
        users,
        players,
        rankings,
        bottom_n=args.bottom,
        user_roster_id=user_roster_id,
        cuts=args.cuts,
    )
    print_watch(watch, args.week, cuts=args.cuts)


if __name__ == "__main__":
    main()

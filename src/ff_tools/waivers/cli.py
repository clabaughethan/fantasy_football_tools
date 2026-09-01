"""CLI entry point for the waiver wire tool.

Usage:
    # Sleeper league
    python -m ff_tools.waivers --platform sleeper --league-id 12345 --username myname

    # ESPN league
    python -m ff_tools.waivers --platform espn --league-id 12345 --season 2025 --team-id 1

    # Trending only (no league needed)
    python -m ff_tools.waivers --trending --days 7
"""

from __future__ import annotations

import argparse
import os
import sys

from dotenv import load_dotenv

from ff_tools.waivers.analyzer import WaiverAnalyzer
from ff_tools.waivers.scrape import WaiverScraper
from ff_tools.waivers.streaming import StreamingAnalyzer
from ff_tools.waivers.trending import TrendingAnalyzer

# Load .env file if present
load_dotenv()

# Positions that can be started in a standard league; everything else in
# Sleeper's pool (offensive linemen, IDP, retired players) is noise here.
_FANTASY_POSITIONS = frozenset({"QB", "RB", "WR", "TE", "K", "DEF"})


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Waiver wire analysis for fantasy football"
    )

    # Platform selection
    parser.add_argument(
        "--platform",
        choices=["sleeper", "espn"],
        help="Platform to use (sleeper or espn)",
    )
    parser.add_argument(
        "--league-id", default=os.getenv("ESPN_LEAGUE_ID"), help="League ID"
    )
    parser.add_argument(
        "--username", help="Sleeper username (required for sleeper platform)"
    )
    parser.add_argument(
        "--team-id", type=int, default=int(os.getenv("ESPN_TEAM_ID", "0")) or None,
        help="ESPN team ID (required for espn platform)"
    )
    # No literal-year default: it would pin the tool to one season forever and
    # make the live-season lookups below unreachable.
    parser.add_argument(
        "--season",
        default=os.getenv("ESPN_SEASON") or None,
        help="Season year (default: the current fantasy season)",
    )
    parser.add_argument(
        "--espn-s2", default=os.getenv("ESPN_S2"), help="ESPN espn_s2 cookie"
    )
    parser.add_argument(
        "--swid", default=os.getenv("ESPN_SWID"), help="ESPN SWID cookie"
    )

    # Options
    parser.add_argument(
        "--rankings", help="Path to CSV rankings file"
    )
    parser.add_argument(
        "--budget", type=int, default=100, help="FAAB budget (default: 100)"
    )
    parser.add_argument(
        "--trending", action="store_true", help="Show trending players (no league needed)"
    )
    parser.add_argument(
        "--days", type=int, default=7, help="Days for trending lookback (default: 7)"
    )
    parser.add_argument(
        "--streaming", action="store_true", help="Show streaming recommendations"
    )
    parser.add_argument(
        "--scrape", action="store_true", help="Attempt to scrape waiver articles"
    )

    args = parser.parse_args()

    # Trending-only mode (no league needed)
    if args.trending:
        trending = TrendingAnalyzer()
        trending.print_trending_board(args.days)
        if args.scrape:
            scraper = WaiverScraper()
            scraper.print_articles()
        return

    # Require league for other modes
    if not args.league_id:
        print("Error: --league-id is required (or use --trending for trending-only mode)",
              file=sys.stderr)
        sys.exit(1)

    if not args.platform:
        print("Error: --platform is required (sleeper or espn)", file=sys.stderr)
        sys.exit(1)

    if args.platform == "sleeper" and not args.username:
        print("Error: --username is required for sleeper platform", file=sys.stderr)
        sys.exit(1)

    if args.platform == "espn" and not args.team_id:
        print("Error: --team-id is required for espn platform", file=sys.stderr)
        sys.exit(1)

    # Run analysis based on platform
    if args.platform == "sleeper":
        _run_sleeper(args)
    else:
        _run_espn(args)


def _run_sleeper(args) -> None:
    """Run waiver analysis for a Sleeper league."""
    from ff_tools.draft.rankings import RankingsSource
    from ff_tools.sleeper.client import SleeperClient

    client = SleeperClient()

    # Resolve user
    user = client.get_user(args.username)
    user_id = user.get("user_id", "")

    # Sleeper reports the live season, so the tool does not go stale each year.
    season = args.season or client.get_nfl_state().get("season") or ""

    # Get league data
    leagues = client.get_user_leagues(user_id, season)
    league = None
    for lg in leagues:
        if lg.league_id == args.league_id:
            league = lg
            break

    if not league:
        # A league ID the user gave directly is still worth trying; they may
        # have passed a season that does not match the league's own.
        try:
            league = client.get_league(args.league_id)
        except Exception:
            print(
                f"Error: League {args.league_id} not found for user "
                f"{args.username} in season {season}",
                file=sys.stderr,
            )
            sys.exit(1)

    # Get rosters to find user's players
    rosters = client.get_league_rosters(args.league_id)
    user_roster = None
    for r in rosters:
        if r.owner_id == user_id:
            user_roster = r
            break

    trending = TrendingAnalyzer(client)
    hot_adds = trending.get_hot_adds(args.days)

    # Load rankings
    rankings_source = RankingsSource()
    if args.rankings:
        rankings = rankings_source.load_csv(args.rankings)
    else:
        rankings = rankings_source.fetch_best_available(season=season or None)

    # Analyze
    analyzer = WaiverAnalyzer(args.budget)

    # Sleeper has no free-agent endpoint, so derive one: every player not on
    # any roster in this league is available. Using trending adds as a proxy
    # (the previous approach) both misses quiet free agents and includes players
    # who are already rostered here.
    players_data = client.get_players(cache=True)
    rostered = {pid for r in rosters for pid in r.players}
    available_players = [
        p
        for pid, p in players_data.items()
        if pid not in rostered and p.active and p.position in _FANTASY_POSITIONS
    ]
    print(f"  {len(available_players)} free agents in {league.name}")

    # Build targets
    targets = analyzer.build_target_list(
        available_players,
        rankings,
        remaining_budget=args.budget,
        trending_adds={t.player_id: t.count for t in hot_adds},
    )

    # Print results
    print("\n" + "=" * 60)
    print(f"  WAIVER WIRE TARGETS - {league.name}")
    print("=" * 60)

    print("\n  TOP PICKUPS:")
    for i, target in enumerate(targets[:10], 1):
        rp = target.player
        bid = target.faab.recommended_bid if target.faab else 0
        print(
            f"    {i:2d}. {rp.display_name:<25s} {rp.position:<4s} "
            f"{rp.team:<4s} | Bid: {bid}% | {target.reasoning}"
        )

    # Drop analysis
    if user_roster:
        roster_players = [
            players_data[pid]
            for pid in user_roster.players
            if pid in players_data
        ]
        # Extract RankedPlayer from WaiverTarget for drop analysis. The full
        # rankings list goes in separately so the roster can be ranked too.
        available_ranked = [t.player for t in targets[:15]]
        drops = analyzer.find_drop_candidates(
            roster_players, available_ranked, rankings=rankings
        )
        if drops:
            print("\n  DROP CANDIDATES:")
            for d in drops[:5]:
                print(
                    f"    DROP {d.drop_player.full_name} "
                    f"-> PICKUP {d.pickup_player.display_name}"
                )
                print(f"           {d.reasoning}")

    # Streaming
    if args.streaming:
        streaming = StreamingAnalyzer()
        # Extract RankedPlayer from WaiverTarget for streaming
        available_ranked = [t.player for t in targets[:30]]
        streaming.print_streaming_board(available_ranked)

    # Trending
    trending.print_trending_board(args.days)

    # Articles
    if args.scrape:
        scraper = WaiverScraper()
        scraper.print_articles()


def _run_espn(args) -> None:
    """Run waiver analysis for an ESPN league."""
    from ff_tools.draft.rankings import RankingsSource, default_season
    from ff_tools.espn.client import ESPNClient

    client = ESPNClient(espn_s2=args.espn_s2, swid=args.swid)

    # ESPN has no "current season" endpoint, so derive it from the calendar.
    season = args.season or default_season()

    # Get the whole player pool
    try:
        pool = client.get_players(season, limit=500)
    except Exception as e:
        print(f"Error fetching players: {e}", file=sys.stderr)
        sys.exit(1)

    # Get rosters, both to find the user's team and to work out who is actually
    # a free agent. The player pool includes rostered players.
    rosters = client.get_rosters(args.league_id, season)
    user_roster = None
    for r in rosters:
        if r.roster_id == args.team_id:
            user_roster = r
            break

    rostered = {pid for r in rosters for pid in r.players}
    available = [p for p in pool if p.player_id not in rostered]
    print(f"  {len(available)} free agents of {len(pool)} players in the pool")

    # Load rankings
    rankings_source = RankingsSource(espn_s2=args.espn_s2, swid=args.swid)
    if args.rankings:
        rankings = rankings_source.load_csv(args.rankings)
    else:
        rankings = rankings_source.fetch_best_available(season=season)

    # Analyze
    analyzer = WaiverAnalyzer(args.budget)
    targets = analyzer.build_target_list(
        available,
        rankings,
        remaining_budget=args.budget,
    )

    # Print results
    print("\n" + "=" * 60)
    print(f"  WAIVER WIRE TARGETS - ESPN League {args.league_id}")
    print("=" * 60)

    print("\n  TOP PICKUPS:")
    for i, target in enumerate(targets[:10], 1):
        rp = target.player
        bid = target.faab.recommended_bid if target.faab else 0
        print(
            f"    {i:2d}. {rp.display_name:<25s} {rp.position:<4s} "
            f"{rp.team:<4s} | Bid: {bid}% | {target.reasoning}"
        )

    # Drop analysis
    if user_roster:
        # Roster players come from the full pool, not the free-agent subset -
        # they were just removed from it.
        roster_players = [
            p for p in pool if p.player_id in set(user_roster.players)
        ]
        available_ranked = [t.player for t in targets[:15]]
        drops = analyzer.find_drop_candidates(
            roster_players, available_ranked, rankings=rankings
        )
        if drops:
            print("\n  DROP CANDIDATES:")
            for d in drops[:5]:
                print(
                    f"    DROP {d.drop_player.full_name} "
                    f"-> PICKUP {d.pickup_player.display_name}"
                )
                print(f"           {d.reasoning}")

    # Streaming
    if args.streaming:
        streaming = StreamingAnalyzer()
        # Extract RankedPlayer from WaiverTarget for streaming
        available_ranked = [t.player for t in targets[:30]]
        streaming.print_streaming_board(available_ranked)


if __name__ == "__main__":
    main()

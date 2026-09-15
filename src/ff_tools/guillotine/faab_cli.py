"""CLI entry point for guillotine FAAB analysis.

Usage:
    python -m ff_tools.guillotine.faab --league-id 12345 --username myname
    python -m ff_tools.guillotine.faab --league-id 12345 --username myname --budget 85 --week 6
"""

from __future__ import annotations

import argparse
import sys

from dotenv import load_dotenv

load_dotenv()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Guillotine league FAAB analysis with survival-aware bidding"
    )
    parser.add_argument("--league-id", required=True, help="Sleeper league ID")
    parser.add_argument("--username", required=True, help="Sleeper username")
    parser.add_argument(
        "--budget", type=int, default=100, help="Starting FAAB budget (default: 100)"
    )
    parser.add_argument(
        "--remaining-budget", type=int, help="Current remaining budget"
    )
    parser.add_argument("--week", type=int, default=1, help="Current week (default: 1)")
    parser.add_argument(
        "--weeks-remaining", type=int, default=14, help="Weeks remaining (default: 14)"
    )
    parser.add_argument("--teams", type=int, default=12, help="Teams remaining")
    parser.add_argument(
        "--rank", type=int, default=12, help="Your survival rank (1=best, 12=worst)"
    )
    parser.add_argument("--rankings", help="Path to CSV rankings file")

    args = parser.parse_args()

    from ff_tools.draft.rankings import RankingsSource
    from ff_tools.guillotine.faab import GuillotineFAABAnalyzer
    from ff_tools.guillotine.strategy import GuillotineScorer
    from ff_tools.sleeper.client import SleeperClient

    client = SleeperClient()

    # Resolve user
    user = client.get_user(args.username)
    user_id = user.get("user_id", "")

    # Get league data - try fetching directly first
    league = client.get_league(args.league_id)

    # If not found or wrong season, search user's leagues
    if not league or league.league_id != args.league_id:
        leagues = client.get_user_leagues(user_id, 2025)
        for lg in leagues:
            if lg.league_id == args.league_id:
                league = lg
                break

    if not league:
        print(f"Error: League {args.league_id} not found for user {args.username}", file=sys.stderr)
        sys.exit(1)

    # Get rosters to find user's roster and survival rank
    rosters = client.get_league_rosters(args.league_id)

    user_roster = None
    for r in rosters:
        if r.owner_id == user_id:
            user_roster = r
            break

    # Calculate survival rank from roster records
    sorted_rosters = sorted(rosters, key=lambda r: (-r.wins, -r.fpts))
    user_rank = args.rank
    if user_roster:
        for i, r in enumerate(sorted_rosters, 1):
            if r.roster_id == user_roster.roster_id:
                user_rank = i
                break

    # Count teams remaining
    # In guillotine, all teams start. Teams are eliminated after scoring lowest.
    # At season start (all 0-0), all teams are still in it.
    teams_remaining = league.total_rosters if league else args.teams
    if rosters and league.status == "in_season":
        # Once games start, count teams with wins (still alive)
        teams_with_wins = len([r for r in rosters if r.wins > 0])
        # If no one has wins yet (week 1), all teams are alive
        if teams_with_wins > 0:
            teams_remaining = teams_with_wins

    # Load rankings
    rankings_source = RankingsSource()
    if args.rankings:
        rankings = rankings_source.load_csv(args.rankings)
    else:
        try:
            rankings = rankings_source.fetch_espn_rankings()
        except Exception:
            rankings = rankings_source.fetch_sleeper_rankings()

    # League's actual starting shape (bench excluded, slot labels normalized).
    starters = league.starting_slots
    print(f"League roster: {starters}")

    # Build scorer with league-specific starters
    scorer = GuillotineScorer.from_rankings(
        rankings,
        total_teams=teams_remaining,
        roster_size=league.total_rosters,
        starters=starters,
    )

    # Build analyzer
    remaining_budget = args.remaining_budget or args.budget
    analyzer = GuillotineFAABAnalyzer(
        scorer=scorer,
        total_budget=args.budget,
        current_week=args.week,
        weeks_remaining=args.weeks_remaining,
        teams_remaining=teams_remaining,
        user_survival_rank=user_rank,
    )

    # Get available players - match by NAME since Sleeper and ESPN use different IDs.
    # Shared normalizer strips suffixes ("James Cook III" vs "James Cook"),
    # punctuation and accents so cross-platform matching holds.
    from ff_tools.utils.names import normalize_name

    players_data = client.get_players(cache=True)

    # Build set of taken player names (from Sleeper rosters)
    taken_names = set()
    for r in rosters:
        for pid in r.players:
            player = players_data.get(pid)
            if player:
                taken_names.add(normalize_name(player.full_name))

    # Filter rankings to only players NOT on any roster
    # Also filter out positions not in this league (e.g., no K/DEF)
    league_positions = set(starters.keys()) - {"FLEX"}  # FLEX covers RB/WR/TE
    league_positions.update(["RB", "WR", "TE"])  # FLEX positions are always valid

    available = [
        rp for rp in rankings
        if normalize_name(rp.player.full_name) not in taken_names
        and rp.position in league_positions
    ]

    # Count user's roster positions
    user_roster_positions = {}
    if user_roster:
        for pid in user_roster.players:
            player = players_data.get(pid)
            if player:
                pos = player.position
                user_roster_positions[pos] = user_roster_positions.get(pos, 0) + 1

    # Run analysis
    analyzer.print_analysis(available, remaining_budget, user_roster_positions)


if __name__ == "__main__":
    main()

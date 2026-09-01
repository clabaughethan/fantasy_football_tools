"""Example: Sleeper API usage - fetch league data, rosters, and draft picks."""

from ff_tools.sleeper import SleeperClient


def main() -> None:
    client = SleeperClient()

    # ── Resolve a user ──────────────────────────────────────────────
    username = "sleeper"  # Change to your username
    user = client.get_user(username)
    user_id = user.get("user_id")
    print(f"User: {user.get('display_name')} (ID: {user_id})")

    # ── Get leagues for current season ──────────────────────────────
    leagues = client.get_user_leagues(user_id, 2025)
    print(f"\nFound {len(leagues)} league(s)")
    for lg in leagues:
        print(f"  - {lg.name} (ID: {lg.league_id}, Status: {lg.status})")

    if not leagues:
        return

    league = leagues[0]
    print(f"\n--- League: {league.name} ---")

    # ── Rosters ─────────────────────────────────────────────────────
    rosters = client.get_league_rosters(league.league_id)
    users = client.get_league_users(league.league_id)
    user_map = {u["user_id"]: u for u in users}

    print("\nStandings:")
    sorted_rosters = sorted(rosters, key=lambda r: (-r.wins, -r.fpts))
    for i, r in enumerate(sorted_rosters, 1):
        user_info = user_map.get(r.owner_id, {})
        team = user_info.get("display_name", f"Team {r.roster_id}")
        print(f"  {i}. {team} ({r.wins}-{r.losses}-{r.ties}) - {r.fpts:.1f} pts")

    # ── Matchups for Week 1 ────────────────────────────────────────
    matchups = client.get_matchups(league.league_id, 1)
    print("\nWeek 1 Matchups:")
    seen = set()
    for m in matchups:
        if m.matchup_id in seen:
            continue
        seen.add(m.matchup_id)
        opp = next(
            (x for x in matchups if x.matchup_id == m.matchup_id and x.roster_id != m.roster_id),
            None,
        )
        t1 = next((r for r in rosters if r.roster_id == m.roster_id), None)
        t2 = next((r for r in rosters if r.roster_id == opp.roster_id), None) if opp else None
        n1 = t1.display_name if t1 else f"Team {m.roster_id}"
        n2 = t2.display_name if t2 else f"Team {opp.roster_id}" if opp else "BYE"
        print(f"  {n1} ({m.points:.1f}) vs {n2} ({opp.points:.1f if opp else 0:.1f})")

    # ── Draft picks ─────────────────────────────────────────────────
    draft_id = league.draft_id
    if draft_id:
        picks = client.get_draft_picks(draft_id)
        print(f"\nDraft Picks (showing first 10 of {len(picks)}):")
        for p in picks[:10]:
            print(f"  R{p.round} P{p.pick_no}: Player {p.player_id}")

    # ── Trending players ───────────────────────────────────────────
    trending = client.get_trending_players("add", limit=5)
    print("\nTrending Adds (last 24h):")
    for t in trending:
        print(f"  Player {t['player_id']}: +{t.get('count', 0)} adds")


if __name__ == "__main__":
    main()

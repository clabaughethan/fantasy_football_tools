"""Example: ESPN API usage - fetch league data with optional auth for private leagues."""

from ff_tools.espn import ESPNClient


def main() -> None:
    # For public leagues, no auth needed
    client = ESPNClient()

    # For private leagues, pass cookies:
    # client = ESPNClient(
    #     espn_s2="your_espn_s2_cookie_value",
    #     swid="{your_swid_cookie_value}",
    # )

    league_id = "123456"  # Change to your league ID
    season = 2025

    # ── League info ─────────────────────────────────────────────────
    league = client.get_league(league_id, season)
    print(f"League: {league.name} ({league.total_rosters} teams)")

    # ── Standings ───────────────────────────────────────────────────
    standings = client.get_standings(league_id, season)
    print("\nStandings:")
    for i, entry in enumerate(standings, 1):
        team = entry.get("team", {})
        record = entry.get("record", {})
        print(
            f"  {i}. {team.get('name', 'Unknown')} "
            f"({record.get('wins', 0)}-{record.get('losses', 0)}) "
            f"- {record.get('pointsFor', 0):.1f} PF"
        )

    # ── Rosters ─────────────────────────────────────────────────────
    rosters = client.get_rosters(league_id, season)
    print(f"\nRosters ({len(rosters)} teams):")
    for r in rosters:
        print(f"  {r.display_name} ({r.team_name}): {len(r.players)} players")

    # ── Draft picks ─────────────────────────────────────────────────
    picks = client.get_draft(league_id, season)
    print(f"\nDraft Picks (first 10 of {len(picks)}):")
    for p in picks[:10]:
        print(f"  R{p.round} P{p.pick_no}: {p.player_name} ({p.position}, {p.team})")

    # ── Players (top available) ─────────────────────────────────────
    players = client.get_players(season, limit=20)
    print(f"\nTop {len(players)} Players:")
    for p in players[:10]:
        print(f"  {p.full_name} - {p.position} ({p.team})")


if __name__ == "__main__":
    main()

"""Example: inspect a guillotine draft board once, without the polling loop.

Replaces the old scratch `temp_test.py`, which had drifted out of sync with the
API (it called `pick.pick`, `pick.metadata` and `board.get_recommendations`,
none of which exist) and hardcoded one specific league.

Usage:
    python examples/guillotine_board.py <league_id> <sleeper_username>
"""

from __future__ import annotations

import sys

from ff_tools.draft import order
from ff_tools.draft.assistant import resolve_draft_slot
from ff_tools.draft.rankings import RankingsSource
from ff_tools.guillotine.board import GuillotineDraftBoard
from ff_tools.guillotine.strategy import GuillotineScorer
from ff_tools.sleeper.client import SleeperClient


def main(league_id: str, username: str) -> None:
    client = SleeperClient()

    league = client.get_league(league_id)
    print(f"League: {league.name}")
    print(f"Teams:  {league.total_rosters}")
    print(f"Lineup: {league.starting_slots}")

    user = client.get_user(username)
    user_id = str(user.get("user_id", ""))
    print(f"User:   {user.get('display_name', '?')} (id: {user_id})")

    rosters = client.get_league_rosters(league_id)
    roster_id = next((r.roster_id for r in rosters if r.owner_id == user_id), 0)
    if not roster_id:
        print(f"'{username}' is not in this league.")
        return
    print(f"Roster: {roster_id}")

    draft = client.get_draft(league.draft_id)
    total_teams = draft.total_teams or league.total_rosters
    slot = resolve_draft_slot(draft, roster_id, user_id)
    print(f"Draft:  {league.draft_id} | {draft.rounds} rounds | {draft.draft_type}")
    print(f"Slot:   {slot} (reversal_round={draft.reversal_round})")

    # ── Rankings ────────────────────────────────────────────────────
    rankings = RankingsSource().fetch_best_available()

    # ── Board ───────────────────────────────────────────────────────
    board = GuillotineDraftBoard(
        draft_id=league.draft_id,
        total_rounds=draft.rounds or 15,
        total_teams=total_teams,
        reversal_round=draft.reversal_round,
        ranked_players=rankings,
        user_roster_id=roster_id,
        scorer=GuillotineScorer.from_rankings(
            rankings,
            total_teams=total_teams,
            roster_size=league.roster_size or 16,
            starters=draft.roster_slots or league.starting_slots or None,
        ),
    )
    board.user_draft_slot = slot
    if slot:
        board.user_picks = order.calculate_pick_numbers(
            slot, total_teams, board.total_rounds, draft.reversal_round
        )

    picks = client.get_draft_picks(league.draft_id)
    board.update_picks(picks)
    print(f"Picks:  {len(board.picks)} made")
    if board.unmatched_picks:
        print(f"        ({len(board.unmatched_picks)} not matched to the rankings)")

    if board.picks:
        print("\nLAST 5 PICKS:")
        for p in board.picks[-5:]:
            mine = " <- you" if board.is_user_pick(p) else ""
            print(
                f"  R{p.round}.{order.pick_in_round(p.pick_no, total_teams):02d} "
                f"#{p.pick_no:03d} {board.player_name(p)} "
                f"({board.position_of(p)}){mine}"
            )

    print(
        f"\nOn the clock: #{board.current_pick_number} "
        f"(R{board.current_round}, slot {board.current_slot})"
    )
    print(f"Your next pick: {board.next_user_pick()}")

    runs = board.detect_positional_runs()
    if runs:
        print("\nPOSITIONAL RUNS:")
        for run in runs:
            print(
                f"  {run.severity.upper()}: {run.count} {run.position}s in the "
                f"last {run.window_size} picks "
                f"({run.supply_remaining} startable left)"
            )

    print("\nTOP 10 RECOMMENDATIONS:")
    for i, ps in enumerate(board.get_scored_recommendations(10), 1):
        rp = ps.ranked_player
        adp = f" | ADP {rp.adp:.0f}" if rp.adp > 0 else ""
        print(
            f"  {i:2d}. [{rp.rank:3d}] {ps.display_name:<25s} "
            f"{ps.position:<4s} {rp.team:<4s} "
            f"(score {ps.survival_score:7.1f}{adp})"
        )


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print(__doc__)
        sys.exit(1)
    main(sys.argv[1], sys.argv[2])

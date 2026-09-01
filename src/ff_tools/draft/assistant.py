"""Live draft assistant - polls Sleeper draft and recommends picks."""

from __future__ import annotations

import time

from ff_tools.draft import order
from ff_tools.draft.board import DraftBoard
from ff_tools.draft.rankings import RankingsSource
from ff_tools.models.draft import Draft, DraftPick
from ff_tools.sleeper.client import SleeperClient


def resolve_draft_slot(draft: Draft, roster_id: int, user_id: str = "") -> int:
    """Find which seat in the draft order belongs to a roster or user.

    A roster ID is not a draft slot. Sleeper assigns the two independently, so
    treating the roster ID as the seat produces pick numbers for whichever team
    happens to be sitting there instead. `slot_to_roster_id` is the mapping;
    `draft_order` (keyed by user ID) is the fallback when it is absent.
    """
    slot = draft.slot_for_roster(roster_id)
    if slot:
        return slot
    if user_id:
        slot = draft.slot_for_user(user_id)
        if slot:
            return slot
    return 0


class DraftAssistant:
    """Live draft assistant for Sleeper leagues.

    Connects to a Sleeper draft, polls for new picks in real-time,
    and recommends the best available player when it's your turn.
    """

    def __init__(
        self,
        league_id: str,
        username: str,
        poll_interval: float = 3.0,
        espn_s2: str | None = None,
        swid: str | None = None,
    ) -> None:
        self.sleeper = SleeperClient()
        self.rankings_source = RankingsSource(espn_s2=espn_s2, swid=swid)
        self.league_id = league_id
        self.username = username
        self.poll_interval = poll_interval
        self.board: DraftBoard | None = None
        self.draft_id: str = ""
        self.user_display_name: str = ""

    def setup(self, rankings_csv: str | None = None) -> None:
        """Initialize the draft assistant.

        1. Resolve user
        2. Find draft in league
        3. Load rankings
        4. Build draft board
        """
        # Resolve user
        user = self.sleeper.get_user(self.username)
        user_id = user.get("user_id", "")
        self.user_display_name = user.get("display_name", self.username)

        # Find user's roster in league
        rosters = self.sleeper.get_league_rosters(self.league_id)

        roster_id = 0
        for r in rosters:
            if r.owner_id == user_id:
                roster_id = r.roster_id
                break

        if roster_id == 0:
            raise ValueError(
                f"User '{self.username}' not found in league {self.league_id}"
            )

        # Get draft info
        league = self.sleeper.get_league(self.league_id)
        self.draft_id = league.draft_id
        if not self.draft_id:
            # Try fetching drafts from the league
            drafts = self.sleeper.get_user_drafts(user_id, league.season)
            if drafts:
                self.draft_id = drafts[0].draft_id

        if not self.draft_id:
            raise ValueError(f"No draft found for league {self.league_id}")

        # Load rankings
        if rankings_csv:
            rankings = self.rankings_source.load_csv(rankings_csv)
        else:
            rankings = self.rankings_source.fetch_best_available()

        # Build board. Draft settings are authoritative for the draft's own
        # shape; the league only knows how many rosters exist.
        draft = self.sleeper.get_draft(self.draft_id)
        total_teams = draft.total_teams or league.total_rosters
        total_rounds = draft.rounds or 15

        self.board = DraftBoard(
            draft_id=self.draft_id,
            total_rounds=total_rounds,
            total_teams=total_teams,
            reversal_round=draft.reversal_round,
            ranked_players=rankings,
            user_roster_id=roster_id,
        )

        slot = resolve_draft_slot(draft, roster_id, user_id)
        self.board.user_draft_slot = slot
        if slot:
            self.board.user_picks = order.calculate_pick_numbers(
                slot, total_teams, total_rounds, draft.reversal_round
            )
        else:
            # Without a seat the pick schedule is unknowable, but roster-ID
            # matching still identifies picks after they happen.
            print(
                "  WARNING: could not determine your draft slot; "
                "upcoming pick numbers will be unavailable."
            )

        # Load existing picks
        existing = self.sleeper.get_draft_picks(self.draft_id)
        new = self.board.update_picks(existing)
        if new:
            print(f"\nLoaded {len(existing)} existing picks")
        self._warn_unmatched()

    def _warn_unmatched(self) -> None:
        """Flag drafted players that could not be tied to the rankings list."""
        board = self.board
        if not board or not board.unmatched_picks:
            return
        count = len(board.unmatched_picks)
        print(
            f"  NOTE: {count} drafted player(s) not found in the rankings list; "
            "they cannot be removed from the available pool."
        )

    def run(self) -> None:
        """Main polling loop."""
        if not self.board:
            raise RuntimeError("Call setup() first")

        self._print_header()

        last_count = len(self.board.picks)
        try:
            while True:
                # Fetch latest picks
                raw_picks = self.sleeper.get_draft_picks(self.draft_id)
                new_picks = self.board.update_picks(raw_picks)

                if new_picks:
                    for pick in new_picks:
                        self._print_new_pick(pick)

                if self.board.is_complete:
                    print("\n*** DRAFT COMPLETE ***")
                    break

                # Show recommendation if it's user's turn
                if self.board.is_user_pick_next:
                    self._print_recommendation()
                elif len(self.board.picks) != last_count:
                    self._print_status()

                last_count = len(self.board.picks)
                time.sleep(self.poll_interval)

        except KeyboardInterrupt:
            print("\n\nDraft assistant stopped.")
            self._print_final_board()

    def _print_header(self) -> None:
        board = self.board
        if not board:
            return
        print("\n" + "=" * 60)
        print("  SLEEPER DRAFT ASSISTANT")
        print(f"  League: {self.league_id}  |  User: {self.user_display_name}")
        if board.user_draft_slot:
            print(f"  Draft slot: {board.user_draft_slot} of {board.total_teams}")
        if board.reversal_round:
            print(f"  Format: snake with round-{board.reversal_round} reversal")
        print(
            f"  Pick: R{board.current_round}P{board.pick_in_round} "
            f"(#{board.current_pick_number} overall)"
        )
        print(f"  Your next pick: #{board.next_user_pick()}")
        print("=" * 60)

    def _print_new_pick(self, pick: DraftPick) -> None:
        board = self.board
        if not board:
            return
        name = board.player_name(pick)
        pos = board.position_of(pick)

        round_num = pick.round or order.round_of_pick(pick.pick_no, board.total_teams)
        pick_in_round = order.pick_in_round(pick.pick_no, board.total_teams)

        marker = " <-- YOUR PICK" if board.is_user_pick(pick) else ""
        print(
            f"  R{round_num:02d} P{pick_in_round:02d} #{pick.pick_no:03d} - "
            f"{name} ({pos}){marker}"
        )

    def _print_recommendation(self) -> None:
        board = self.board
        if not board:
            return

        recs = board.recommend_pick(5)
        scarcity = board.get_positional_scarcity()

        print("\n" + "-" * 60)
        print(
            f"  *** YOUR PICK: R{board.current_round}P{board.pick_in_round} "
            f"(#{board.current_pick_number} overall) ***"
        )
        print("-" * 60)

        # Show positional scarcity
        print("\n  Positional Scarcity:")
        for pos in ["QB", "RB", "WR", "TE"]:
            s = scarcity.get(pos, {})
            remaining = s.get("remaining", 0)
            pct = s.get("pct_taken", 0)
            filled = min(20, int(pct / 5))
            bar = "#" * filled + "-" * (20 - filled)
            print(f"    {pos}: {remaining:3d} left  [{bar}] {pct:.0f}% taken")

        # Show recommendations
        print(f"\n  TOP {len(recs)} RECOMMENDATIONS:")
        for i, rp in enumerate(recs, 1):
            tier_label = f"Tier {rp.tier}" if rp.tier else ""
            details = [d for d in (tier_label,) if d]
            if rp.projected_points:
                details.append(f"{rp.projected_points:.1f} proj pts")
            if rp.adp:
                details.append(f"ADP {rp.adp:.0f}")
            if rp.bye_week:
                details.append(f"bye {rp.bye_week}")
            print(
                f"    {i}. [{rp.rank:3d}] {rp.display_name:<25s} "
                f"{rp.position:<4s} {rp.team:<4s} ({' | '.join(details)})"
            )

        # Pick reasoning
        if recs:
            best = recs[0]
            pos_remaining = scarcity.get(best.position, {}).get("remaining", 0)
            print(f"\n  RECOMMENDATION: {best.display_name} ({best.position})")
            if pos_remaining <= 3:
                print(f"    - Last top-tier {best.position} available!")
            else:
                print(f"    - {pos_remaining} top {best.position}s still on board")
            if best.projected_points:
                print(f"    - {best.projected_points:.1f} projected points")
            # A wide spread among the experts is a risk signal worth seeing.
            if best.rank_std >= 10:
                print(
                    f"    - CAUTION: experts disagree on this player "
                    f"(rank std {best.rank_std:.0f})"
                )

        print("-" * 60)

    def _print_status(self) -> None:
        board = self.board
        if not board:
            return
        picks_until = board.picks_until_user_turn()
        print(
            f"\n  [Pick #{board.current_pick_number} | "
            f"{picks_until} picks until your turn]"
        )

    def _print_final_board(self) -> None:
        board = self.board
        if not board:
            return

        print("\n" + "=" * 60)
        print("  FINAL DRAFT BOARD")
        print("=" * 60)

        rounds = board.get_picks_by_round()
        for round_num, picks in sorted(rounds.items()):
            print(f"\n  Round {round_num}:")
            for pick in sorted(picks, key=lambda p: p.pick_no):
                name = board.player_name(pick)
                marker = " *" if board.is_user_pick(pick) else ""
                print(f"    #{pick.pick_no:03d} {name}{marker}")

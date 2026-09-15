"""Live draft assistant for ESPN fantasy football leagues.

Mirrors the Sleeper draft assistant but uses ESPN's undocumented API.
Private leagues require espn_s2 + SWID cookies.
"""

from __future__ import annotations

import time

from ff_tools.draft import order
from ff_tools.draft.board import DraftBoard
from ff_tools.draft.rankings import RankingsSource
from ff_tools.espn.client import ESPNClient
from ff_tools.models.draft import DraftPick


def slot_from_pick_order(pick_order: list, team_id: int) -> int:
    """Find a team's seat in ESPN's draft order.

    `draftSettings.pickOrder` lists team IDs in seat order, so a team's slot is
    its index plus one. An ESPN team ID is not itself a slot - the two only
    coincide when the commissioner never reordered the draft.
    """
    if not isinstance(pick_order, list):
        return 0
    for index, tid in enumerate(pick_order, 1):
        try:
            if int(tid) == int(team_id):
                return index
        except (TypeError, ValueError):
            continue
    return 0


class ESPNDraftAssistant:
    """Live draft assistant for ESPN leagues.

    Connects to an ESPN fantasy draft, polls for new picks in real-time,
    and recommends the best available player when it's your turn.
    """

    def __init__(
        self,
        league_id: str,
        season: str | int,
        espn_s2: str | None = None,
        swid: str | None = None,
        poll_interval: float = 3.0,
        team_id: int | None = None,
        rec_count: int = 10,
        preview: int = 3,
    ) -> None:
        self.espn = ESPNClient(espn_s2=espn_s2, swid=swid)
        self.rankings_source = RankingsSource(espn_s2=espn_s2, swid=swid)
        self.league_id = league_id
        self.season = season
        self.poll_interval = poll_interval
        self.rec_count = rec_count
        self.preview = preview
        self.board: DraftBoard | None = None
        self.user_team_id: int | None = team_id
        self.user_team_name: str = ""
        self.draft_detail: dict | None = None

    def setup(self, rankings_csv: str | None = None) -> None:
        """Initialize the ESPN draft assistant.

        1. Fetch league info and find user's team
        2. Load draft data
        3. Load rankings
        4. Build draft board
        """
        # Get league info
        league_data = self.espn._get_league(
            self.league_id, self.season, ["mTeam", "mSettings"]
        )

        teams = league_data.get("teams", [])
        settings = league_data.get("settings") or {}
        total_teams = settings.get("size", len(teams))

        # Find user's team (by team_id if provided, otherwise try to auto-detect)
        if self.user_team_id is None:
            # ESPN does not identify which team belongs to the authenticated
            # cookie, so the user has to name it.
            raise ValueError(
                "Could not auto-detect your team. "
                "Please provide --team-id. Available teams:\n"
                + "\n".join(
                    f"  ID {t.get('id')}: {t.get('abbrev', '')} - {t.get('name', '')}"
                    for t in teams
                )
            )

        for team in teams:
            if team.get("id") == self.user_team_id:
                self.user_team_name = team.get("name", team.get("abbrev", ""))
                break

        # Get draft settings
        draft_settings = settings.get("draftSettings") or {}
        draft_rounds = draft_settings.get("rounds") or 15
        draft_type = str(draft_settings.get("type", ""))

        if draft_type and "SNAKE" not in draft_type.upper():
            print(
                f"  WARNING: draft type is {draft_type}; pick-number math here "
                "assumes a snake draft."
            )

        # Load rankings
        if rankings_csv:
            rankings = self.rankings_source.load_csv(rankings_csv)
        else:
            rankings = self.rankings_source.fetch_best_available(season=self.season)

        # Build board
        self.board = DraftBoard(
            draft_id=f"espn-{self.league_id}",
            total_rounds=draft_rounds,
            total_teams=total_teams,
            ranked_players=rankings,
            user_roster_id=self.user_team_id,
        )

        # Resolve the user's seat from the published draft order, falling back
        # to the team ID when ESPN does not expose one.
        slot = slot_from_pick_order(
            draft_settings.get("pickOrder"), self.user_team_id
        )
        if not slot:
            slot = self.user_team_id
            print(
                "  NOTE: no draft order published; assuming team "
                f"{self.user_team_id} drafts from slot {slot}."
            )
        self.board.user_draft_slot = slot
        self.board.user_picks = order.calculate_pick_numbers(
            slot, total_teams, draft_rounds
        )

        # Load existing picks
        existing = self._fetch_picks()
        if existing:
            self.board.update_picks(existing)
            print(f"\nLoaded {len(existing)} existing picks")
        self._warn_unmatched()

    def _warn_unmatched(self) -> None:
        """Flag drafted players that could not be tied to the rankings list."""
        board = self.board
        if not board or not board.unmatched_picks:
            return
        print(
            f"  NOTE: {len(board.unmatched_picks)} drafted player(s) are not in "
            "the rankings list and cannot be removed from the available pool."
        )

    def _fetch_picks(self) -> list[DraftPick]:
        """Fetch current draft picks from ESPN."""
        try:
            filter_obj = {"draftDetail": {"isAvailable": True}}
            data = self.espn._get_league(
                self.league_id, self.season, ["mDraftDetail"], filter_obj
            )
        except Exception:
            # ESPN returns 400 for mDraftDetail with filter on some leagues/seasons;
            # fall back to unfiltered fetch (works for 485702 / 2026 live draft).
            data = self.espn._get_league(
                self.league_id, self.season, ["mDraftDetail"]
            )

        draft_id = f"espn-{self.league_id}"
        picks = []
        for raw in (data.get("draftDetail") or {}).get("picks", []):
            # Skip empty slots (ESPN uses playerId -1 before pick is made)
            if raw.get("playerId", -1) in (-1, "-1", None) and not raw.get("player"):
                continue
            pick = DraftPick.from_espn(raw)
            pick.draft_id = draft_id
            pick.picked_by = str(pick.roster_id)
            picks.append(pick)
        return picks

    def run(self) -> None:
        """Main polling loop."""
        if not self.board:
            raise RuntimeError("Call setup() first")

        self._print_header()
        # Initial lookahead so you can plan before any picks
        if not self.board.is_user_pick_next:
            if self.preview == 0:
                self._print_status()
            elif 0 < self.board.picks_until_user_turn() <= self.preview:
                self._print_recommendation(count=self.rec_count, preview=True)
            else:
                self._print_lookahead(count=self.rec_count)

        last_count = len(self.board.picks)
        try:
            while True:
                # Fetch latest picks
                new_picks_raw = self._fetch_picks()
                new_picks = self.board.update_picks(new_picks_raw)

                if new_picks:
                    for pick in new_picks:
                        self._print_new_pick(pick)

                if self.board.is_complete:
                    print("\n*** DRAFT COMPLETE ***")
                    break

                # Show recommendation if it's user's turn, otherwise always
                # look ahead to your next pick so you can plan.
                if self.board.is_user_pick_next:
                    self._print_recommendation(count=self.rec_count, preview=False)
                elif len(self.board.picks) != last_count:
                    # Only print when board actually changed (avoids spamming same
                    # forecast every poll). preview=0 = status only.
                    if self.preview == 0:
                        self._print_status()
                    elif 0 < self.board.picks_until_user_turn() <= self.preview:
                        self._print_recommendation(count=self.rec_count, preview=True)
                    else:
                        self._print_lookahead(count=self.rec_count)

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
        print("  ESPN DRAFT ASSISTANT")
        print(f"  League: {self.league_id}  |  Team: {self.user_team_name}")
        if board.user_draft_slot:
            print(f"  Draft slot: {board.user_draft_slot} of {board.total_teams}")
        print(
            f"  Pick: R{board.current_round}P{board.pick_in_round} "
            f"(#{board.current_pick_number} overall)"
        )
        next_pick = board.next_user_pick()
        if next_pick:
            print(f"  Your next pick: #{next_pick}")
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

    def _print_recommendation(self, count: int = 10, preview: bool = False) -> None:
        board = self.board
        if not board:
            return

        recs = board.recommend_pick(count)
        scarcity = board.get_positional_scarcity()

        print("\n" + "-" * 60)
        if preview:
            print(
                f"  >>> PREVIEW: Your pick in {board.picks_until_user_turn()} "
                f"(#{board.next_user_pick()} | R{board.current_round}P{board.pick_in_round} now #{board.current_pick_number}) <<<"
            )
        else:
            print(
                f"  *** YOUR PICK: R{board.current_round}P{board.pick_in_round} "
                f"(#{board.current_pick_number} overall) ***"
            )
        print("-" * 60)

        # Show positional scarcity
        print("\n  Positional Scarcity:")
        for pos in ("QB", "RB", "WR", "TE"):
            s = scarcity.get(pos, {})
            remaining = s.get("remaining", 0)
            pct = s.get("pct_taken", 0)
            filled = min(20, int(pct / 5))
            bar = "#" * filled + "-" * (20 - filled)
            print(f"    {pos}: {remaining:3d} left  [{bar}] {pct:.0f}% taken")

        # Show top N overall
        print(f"\n  TOP {len(recs)} OVERALL:")
        for i, rp in enumerate(recs, 1):
            details = []
            if rp.tier:
                details.append(f"Tier {rp.tier}")
            if rp.projected_points:
                details.append(f"{rp.projected_points:.1f} proj pts")
            if rp.adp:
                details.append(f"ADP {rp.adp:.0f}")
            info = f" ({' | '.join(details)})" if details else ""
            print(
                f"    {i:2d}. [{rp.rank:3d}] {rp.display_name:<25s} "
                f"{rp.position:<4s} {rp.team:<4s}{info}"
            )

        # Options by position - best 2 per position still available
        print("\n  OPTIONS BY POSITION:")
        available = board.get_available()
        for pos in ("RB", "WR", "QB", "TE"):
            pos_players = [p for p in available if p.position == pos][:2]
            if not pos_players:
                continue
            s = scarcity.get(pos, {})
            remaining = s.get("remaining", 0)
            print(f"    {pos} ({remaining} left):")
            for rp in pos_players:
                details = []
                if rp.tier:
                    details.append(f"T{rp.tier}")
                if rp.adp:
                    details.append(f"ADP{rp.adp:.0f}")
                info = f" ({' | '.join(details)})" if details else ""
                print(f"      - [{rp.rank:3d}] {rp.display_name:<22s} {rp.team:<4s}{info}")

        # Clear tiered recommendation: primary + 2 alternates
        if recs:
            print("\n  RECOMMENDATION:")
            print(f"    1) PRIMARY: {recs[0].display_name} ({recs[0].position} - {recs[0].team}) [Rank {recs[0].rank}]")
            if len(recs) > 1:
                print(f"    2) ALTERNATE: {recs[1].display_name} ({recs[1].position}) [Rank {recs[1].rank}]")
            if len(recs) > 2:
                print(f"    3) FALLBACK: {recs[2].display_name} ({recs[2].position}) [Rank {recs[2].rank}]")
            # Context for primary
            best = recs[0]
            pos_remaining = scarcity.get(best.position, {}).get("remaining", 0)
            if pos_remaining <= 3:
                print(f"       -> Last top-tier {best.position} available!")
            else:
                print(f"       -> {pos_remaining} top {best.position}s still on board")
            if best.projected_points:
                print(f"       -> {best.projected_points:.1f} projected points")
            if best.tier and len(recs) > 1 and recs[1].tier != best.tier:
                print(f"       -> Tier drop after this pick (Tier {best.tier} -> {recs[1].tier})")

        print("-" * 60)

    def _print_lookahead(self, count: int = 8) -> None:
        """Always-on lookahead: what will likely be there at your next pick."""
        board = self.board
        if not board:
            return
        nxt = board.next_user_pick()
        until = board.picks_until_user_turn()
        if not nxt:
            self._print_status()
            return
        print(
            f"\n  [Lookahead: Pick #{board.current_pick_number} now | "
            f"your next #{nxt} in {until} picks | Current: R{board.current_round}P{board.pick_in_round}]"
        )
        available = board.get_available()
        if not available:
            return
        # Simple chalk forecast: the next `until` best-available will be taken
        # before you pick. Use ADP when present, else consensus rank.
        def sort_key(rp):
            # ADP is pick-number comparable; rank is fallback. Prefer the earlier.
            adp = rp.adp if rp.adp and rp.adp > 0 else 9999
            return min(adp, rp.rank)

        # Don't sort by forecast key for overall display (keep rank order),
        # but use it to estimate at-risk vs safe.
        # At-risk = players whose rank/ADP is within the next `until` picks.
        at_risk = []
        safe = []
        for rp in available:
            # If consensus rank or ADP is before your next pick, it's at risk
            # if you wait.
            threshold = nxt
            key = sort_key(rp)
            if key < threshold and len(at_risk) < until:
                at_risk.append(rp)
            elif len(safe) < count:
                safe.append(rp)
            if len(at_risk) >= until and len(safe) >= count:
                break
        # Fallback if ADP/rank heuristic produced empty buckets
        if not at_risk:
            at_risk = available[: min(until, 5)]
            safe = available[until : until + count]

        print(f"\n  NEXT PICK FORECAST (#{nxt} in {until}):")
        if at_risk:
            print(f"    AT RISK if you wait ({len(at_risk)} will likely be gone):")
            for rp in at_risk[:5]:
                adp_s = f" ADP{rp.adp:.0f}" if rp.adp else ""
                print(f"      - [{rp.rank:3d}] {rp.display_name:<22s} {rp.position:<3s}{adp_s} T{rp.tier}")
        if safe:
            print(f"    LIKELY THERE at #{nxt}:")
            for rp in safe[: count]:
                adp_s = f" ADP{rp.adp:.0f}" if rp.adp else ""
                print(f"      + [{rp.rank:3d}] {rp.display_name:<22s} {rp.position:<3s}{adp_s} T{rp.tier}")

        # Also show current best available for context
        print(f"\n  BEST AVAILABLE NOW (Top {min(5, len(available))}):")
        for i, rp in enumerate(available[:5], 1):
            adp_s = f" ADP{rp.adp:.0f}" if rp.adp else ""
            print(f"    {i}. [{rp.rank:3d}] {rp.display_name:<22s} {rp.position:<3s}{adp_s}")

    def _print_status(self) -> None:
        board = self.board
        if not board:
            return
        print(
            f"\n  [Pick #{board.current_pick_number} | "
            f"{board.picks_until_user_turn()} picks until your turn]"
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

"""Live guillotine draft assistant - survival-focused pick recommendations."""

from __future__ import annotations

import time

from ff_tools.draft import order
from ff_tools.draft.assistant import resolve_draft_slot
from ff_tools.draft.rankings import RankingsSource
from ff_tools.guillotine.board import GuillotineDraftBoard
from ff_tools.guillotine.injury import (
    InjuryAssessment,
    load_assessments,
    write_assessment_template,
)
from ff_tools.guillotine.strategy import GuillotineScorer
from ff_tools.models.draft import DraftPick
from ff_tools.sleeper.client import SleeperClient

# Regular-season length. A guillotine league runs until one team is left, so the
# season is bounded by both the NFL schedule and the number of chops available.
_REGULAR_SEASON_WEEKS = 14


class GuillotineDraftAssistant:
    """Live draft assistant for guillotine leagues.

    Connects to a Sleeper draft, polls for new picks in real-time,
    and recommends survival-focused picks when it's your turn.
    """

    def __init__(
        self,
        league_id: str,
        username: str,
        poll_interval: float = 3.0,
        roster_size: int = 0,
        espn_s2: str | None = None,
        swid: str | None = None,
        use_injury_status: bool = True,
        injury_notes: str | None = None,
        write_injury_template: str | None = None,
    ) -> None:
        self.sleeper = SleeperClient()
        self.rankings_source = RankingsSource(espn_s2=espn_s2, swid=swid)
        self.league_id = league_id
        self.username = username
        self.poll_interval = poll_interval
        # 0 means "read it from the league settings".
        self.roster_size = roster_size
        self.use_injury_status = use_injury_status
        self.injury_notes = injury_notes
        self.write_injury_template = write_injury_template
        self.injury_assessments: dict[str, InjuryAssessment] = {}
        self.board: GuillotineDraftBoard | None = None
        self.draft_id: str = ""
        self.user_display_name: str = ""
        self.total_teams: int = 0
        self.starters: dict[str, int] = {}
        # Set only when ADP came from a smaller board than this league, so the
        # displayed figures can say which format they describe.
        self.adp_reference_teams: int = 0

    def setup(
        self, rankings_csv: str | None = None, total_rounds: int = 0
    ) -> None:
        """Initialize the guillotine draft assistant.

        1. Resolve user
        2. Find draft in league
        3. Load rankings
        4. Build guillotine-aware draft board

        `total_rounds` overrides the league's own setting; leave it at 0 to use
        whatever the draft reports.
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
            drafts = self.sleeper.get_user_drafts(user_id, league.season)
            if drafts:
                self.draft_id = drafts[0].draft_id

        if not self.draft_id:
            raise ValueError(f"No draft found for league {self.league_id}")

        # Load rankings - FantasyPros ECR is primary
        if rankings_csv:
            rankings = self.rankings_source.load_csv(rankings_csv)
        else:
            rankings = self.rankings_source.fetch_best_available()

        # Fetch ADP data from Fantasy Football Calculator
        draft = self.sleeper.get_draft(self.draft_id)
        self.total_teams = draft.total_teams or league.total_rosters
        try:
            adp = self.rankings_source.fetch_fantasycalculator_adp(
                teams=self.total_teams or 12
            )
            rankings = self.rankings_source.merge_rankings_with_adp(rankings, adp)
            # Record which board answered, so per-player ADP can be labelled.
            # `merge_rankings_with_adp` copies the figures but not the source,
            # and one draft only ever has one reference board.
            adp_teams = self.rankings_source.adp_reference_teams(
                adp[0].source if adp else ""
            )
            if adp_teams and adp_teams < self.total_teams:
                self.adp_reference_teams = adp_teams
                print(
                    f"  Merged ADP from Fantasy Football Calculator "
                    f"({adp_teams}-team reference; no ADP published for "
                    f"{self.total_teams} teams)"
                )
            else:
                print("  Merged ADP data from Fantasy Football Calculator")
        except Exception:
            print("  ADP data unavailable (using ECR only)")

        # Injury designations. The consensus board carries none, so they come
        # from Sleeper's player pool. Failing this must not abort a draft, but it
        # does have to say so: silently scoring an injured board as healthy is
        # exactly the mistake this merge exists to prevent.
        if self.use_injury_status:
            try:
                flagged = self.rankings_source.merge_injury_status(
                    rankings, self.sleeper.get_players().values()
                )
                if flagged:
                    print(f"  Merged injury designations from Sleeper ({flagged} flagged)")
                else:
                    print(
                        "  WARNING: injury merge matched no designations. Either "
                        "the board is entirely healthy or the match failed; "
                        "availability is not being scored."
                    )
            except Exception as e:
                print(
                    f"  WARNING: injury data unavailable ({type(e).__name__}); "
                    f"players are being scored as if healthy."
                )
            self._load_injury_assessments()
            if self.write_injury_template:
                written = write_assessment_template(
                    self.write_injury_template, rankings
                )
                print(
                    f"  Wrote {written} injury assessment stubs to "
                    f"{self.write_injury_template}"
                )
        else:
            print("  Injury scoring disabled (--ignore-injuries)")

        # League settings, not assumptions, drive the draft's shape.
        total_rounds = total_rounds or draft.rounds or 15
        self.roster_size = self.roster_size or league.roster_size or total_rounds
        # The draft's own slot settings are the most specific source for the
        # starting lineup; the league's roster_positions is the fallback.
        self.starters = draft.roster_slots or league.starting_slots

        if self.starters:
            shape = ", ".join(f"{k}{v}" for k, v in sorted(self.starters.items()))
            print(f"  Lineup: {shape}")
        else:
            print("  WARNING: no lineup settings found; using generic defaults.")

        # Scarcity is measured against the players actually on the board.
        scorer = GuillotineScorer.from_rankings(
            rankings,
            total_teams=self.total_teams,
            roster_size=self.roster_size,
            starters=self.starters or None,
            use_injury_status=self.use_injury_status,
            injury_assessments=self.injury_assessments or None,
        )

        self.board = GuillotineDraftBoard(
            draft_id=self.draft_id,
            total_rounds=total_rounds,
            total_teams=self.total_teams,
            reversal_round=draft.reversal_round,
            ranked_players=rankings,
            user_roster_id=roster_id,
            scorer=scorer,
        )

        slot = resolve_draft_slot(draft, roster_id, user_id)
        self.board.user_draft_slot = slot
        if slot:
            self.board.user_picks = order.calculate_pick_numbers(
                slot, self.total_teams, total_rounds, draft.reversal_round
            )
        else:
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

    def _load_injury_assessments(self) -> None:
        """Read supplied injury judgements, reporting what was wrong with them.

        A bad assessment file is worse than none: it silently changes the ranking
        of the players you are about to pick. So a malformed file is fatal, and a
        stale one is named rather than quietly applied.
        """
        if not self.injury_notes:
            return

        parsed = load_assessments(self.injury_notes)
        self.injury_assessments = parsed.assessments
        print(
            f"  Loaded {len(parsed.assessments)} injury assessment(s) from "
            f"{self.injury_notes}"
        )
        if parsed.skipped:
            shown = ", ".join(parsed.skipped[:5])
            more = len(parsed.skipped) - min(5, len(parsed.skipped))
            suffix = f" (+{more} more)" if more > 0 else ""
            print(
                f"  NOTE: {len(parsed.skipped)} entr(ies) left blank and skipped: "
                f"{shown}{suffix}"
            )
        for stale in parsed.stale:
            age = stale.age_days()
            when = f"{age} days old" if age is not None else "undated"
            print(f"  WARNING: assessment for {stale.name} is {when}; re-check it.")

    def _warn_unmatched(self) -> None:
        """Flag drafted players that could not be tied to the rankings list."""
        board = self.board
        if not board or not board.unmatched_picks:
            return
        names = [p.player_name or p.player_id for p in board.unmatched_picks[:5]]
        more = len(board.unmatched_picks) - len(names)
        suffix = f" (+{more} more)" if more > 0 else ""
        print(
            f"  NOTE: {len(board.unmatched_picks)} drafted player(s) are not in the "
            f"rankings list and cannot be removed from the pool: "
            f"{', '.join(names)}{suffix}"
        )

    @property
    def weeks_remaining(self) -> int:
        """Weeks left in the guillotine season.

        One team is chopped per week, so the season ends when either the NFL
        regular season runs out or only one team is left standing.
        """
        board = self.board
        if not board:
            return _REGULAR_SEASON_WEEKS
        by_chops = max(1, board.total_teams - 1)
        return min(_REGULAR_SEASON_WEEKS, by_chops)

    def run(self) -> None:
        """Main polling loop."""
        if not self.board:
            raise RuntimeError("Call setup() first")

        self._print_header()

        last_count = len(self.board.picks)
        try:
            while True:
                raw_picks = self.sleeper.get_draft_picks(self.draft_id)
                new_picks = self.board.update_picks(raw_picks)

                if new_picks:
                    for pick in new_picks:
                        self._print_new_pick(pick)

                if self.board.is_complete:
                    print("\n*** DRAFT COMPLETE ***")
                    break

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
        print("\n" + "=" * 70)
        print("  GUILLOTINE DRAFT ASSISTANT")
        print(f"  League: {self.league_id}  |  User: {self.user_display_name}")
        print(f"  Teams: {self.total_teams}  |  Roster spots: {self.roster_size}")
        if board.user_draft_slot:
            print(f"  Draft slot: {board.user_draft_slot} of {board.total_teams}")
        if board.reversal_round:
            print(f"  Format: snake with round-{board.reversal_round} reversal")
        print(
            f"  Pick: R{board.current_round}P{board.pick_in_round} "
            f"(#{board.current_pick_number} overall)"
        )
        print(f"  Your next pick: #{board.next_user_pick()}")
        print("=" * 70)

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

        scored = board.get_scored_recommendations(5)
        runs = board.detect_positional_runs()
        supply = board.get_position_supply()

        print("\n" + "-" * 70)
        print(
            f"  *** YOUR PICK: R{board.current_round}P{board.pick_in_round} "
            f"(#{board.current_pick_number} overall) ***"
        )
        print("-" * 70)

        # Show position supply - the key metric for draft strategy
        # "Pool" counts all remaining players at the position; "startable"
        # counts only tier-1 and tier-2 players (the ones that matter for
        # survival scoring). The run detector's shortage alert uses startable.
        print("\n  POSITION SUPPLY (pool / startable(tier<=2) / teams still needing):")
        for pos in ("QB", "RB", "WR", "TE"):
            s = supply.get(pos, {})
            total = s.get("total_remaining", 0)
            startable = s.get("startable_remaining", 0)
            teams_without = s.get("teams_without", 0)
            print(
                f"    {pos}: {total:3d} pool | {startable:2d} startable | "
                f"{teams_without:2d} teams still need one"
            )
            # A position is in trouble when startable supply cannot cover the
            # teams that still have to fill the slot.
            if teams_without and startable < teams_without:
                print(
                    f"         *** SHORTAGE: {startable} startable for "
                    f"{teams_without} teams ***"
                )

        # Show positional runs with supply context
        if runs:
            print("\n  *** POSITIONAL RUN ALERT ***")
            for run in runs:
                supply_msg = ""
                if run.supply_remaining <= 10:
                    supply_msg = f" - ONLY {run.supply_remaining} STARTABLE LEFT"
                print(
                    f"    {run.severity.upper()}: {run.count} {run.position}s "
                    f"taken in last {run.window_size} picks{supply_msg}"
                )

        # Show recommendations with survival scores and ADP
        print(f"\n  TOP {len(scored)} RECOMMENDATIONS (by survival score):")
        for i, ps in enumerate(scored, 1):
            rp = ps.ranked_player
            tier_label = f"Tier {rp.tier}" if rp.tier else "-"
            factors = (
                f"F:{ps.floor_bonus:.2f} P:{ps.position_value:.2f} "
                f"N:{ps.need_bonus:.2f} S:{ps.scarcity_bonus:.2f}"
            )
            # Only shown when it bites, so a clean board stays readable.
            if ps.availability < 1.0:
                factors += f" A:{ps.availability:.2f}"
            # A borrowed-board ADP is tagged inline. The startup banner says it
            # once, but these lines are what gets read 50 picks later, and an
            # unlabelled "ADP 45" invites treating a 14-team estimate as this
            # league's own.
            adp_str = ""
            if rp.adp > 0:
                ref = f"/{self.adp_reference_teams}t" if self.adp_reference_teams else ""
                adp_str = f" | ADP {rp.adp:.0f}{ref}"
            bye_str = f" | bye {rp.bye_week}" if rp.bye_week else ""
            print(
                f"    {i}. [{rp.rank:3d}] {ps.display_name:<25s} "
                f"{ps.position:<4s} {rp.team:<4s} "
                f"({tier_label} | {factors}{adp_str}{bye_str})"
            )
            print(f"       Survival Score: {ps.survival_score:.1f}")
            if ps.availability < 1.0:
                # The replacement ratio is why the penalty is the size it is, so
                # it belongs next to it rather than buried in the model.
                print(
                    f"       *** INJURY: {ps.injury_label} "
                    f"(replacement worth {ps.replacement_ratio:.0%} of him) ***"
                )
                if ps.assessment is not None and ps.assessment.note:
                    print(f"           note: {ps.assessment.note}")

        # Pick reasoning
        if scored:
            best = scored[0]
            print(f"\n  RECOMMENDATION: {best.display_name} ({best.position})")
            if best.need_bonus >= 1.5:
                print(f"    - CRITICAL NEED: You have no {best.position} starter!")
            elif best.need_bonus >= 1.15:
                print(f"    - Depth pick: Adds insurance at {best.position}")
            if best.floor_bonus >= 1.10:
                print("    - HIGH FLOOR: Consistent producer, low bust risk")
            if best.position_value >= 1.20:
                print(f"    - POSITIONAL VALUE: {best.position} is hard to replace")
            if best.scarcity_bonus >= 1.20:
                print(f"    - THINNING SUPPLY: {best.position} is running out")
            if best.ranked_player.rank_std >= 10:
                print(
                    f"    - CAUTION: experts disagree "
                    f"(rank std {best.ranked_player.rank_std:.0f})"
                )
            if best.availability < 1.0:
                print(f"    - CAUTION: {best.injury_label}")
                # Week 1 is a live elimination, so an unavailable starter is not
                # a slow leak here; it is the whole risk.
                print(
                    f"      If he sits, the best replacement is worth "
                    f"{best.replacement_ratio:.0%} of him."
                )
                clean = next(
                    (s for s in scored[1:] if s.availability >= 1.0), None
                )
                if clean:
                    print(
                        f"      Healthy alternative: {clean.display_name} "
                        f"({clean.position}, score {clean.survival_score:.1f})"
                    )

        print("-" * 70)

    def _print_status(self) -> None:
        board = self.board
        if not board:
            return
        picks_until = board.picks_until_user_turn()
        runs = board.detect_positional_runs()
        supply = board.get_position_supply()

        parts = [f"Pick #{board.current_pick_number}", f"{picks_until} until your turn"]

        if runs:
            parts.append("RUN: " + ", ".join(f"{r.position}({r.count})" for r in runs))

        # Surface any position whose startable supply no longer covers demand.
        short = [
            f"{pos}: {s.get('startable_remaining', 0)}/{s.get('teams_without', 0)}"
            for pos, s in supply.items()
            if s.get("teams_without", 0)
            and s.get("startable_remaining", 0) < s.get("teams_without", 0)
        ]
        if short:
            parts.append("SHORT: " + ", ".join(short))

        print(f"\n  [{' | '.join(parts)}]")

    def _print_final_board(self) -> None:
        board = self.board
        if not board:
            return

        print("\n" + "=" * 70)
        print("  FINAL DRAFT BOARD - GUILLOTINE ANALYSIS")
        print("=" * 70)

        rounds = board.get_picks_by_round()
        for round_num, picks in sorted(rounds.items()):
            print(f"\n  Round {round_num}:")
            for pick in sorted(picks, key=lambda p: p.pick_no):
                name = board.player_name(pick)
                marker = " *" if board.is_user_pick(pick) else ""
                print(f"    #{pick.pick_no:03d} {name}{marker}")

        roster = board.get_roster_positions()
        if roster:
            shape = ", ".join(f"{pos} {n}" for pos, n in sorted(roster.items()))
            print(f"\n  YOUR ROSTER: {shape}")

        # Print waiver projection
        weeks_left = self.weeks_remaining
        proj = board.get_waiver_projection(weeks_left)
        if proj:
            print("\n  WAIVER POOL PROJECTION:")
            print(f"  (One roster hits waivers per week over {weeks_left} weeks)")
            for pos in ("QB", "RB", "WR", "TE"):
                p = proj.get(pos, {})
                streamable = " (streamable)" if p.get("streamable") else ""
                print(
                    f"    {pos}: ~{p.get('projected_released', 0)} "
                    f"will hit waivers{streamable}"
                )

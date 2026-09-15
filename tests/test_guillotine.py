"""Tests for guillotine draft strategy components."""

from ff_tools.draft.rankings import RankedPlayer
from ff_tools.guillotine.board import GuillotineDraftBoard
from ff_tools.guillotine.strategy import (
    DEFAULT_REPLACEMENT_RATIO,
    GuillotineScorer,
    compute_need_bonus,
    estimate_position_scarcity,
    floor_bonus_from_rank,
    injury_availability,
    startable_depth,
    streaming_discount,
)
from ff_tools.models.draft import DraftPick
from ff_tools.models.player import Player


def _make_ranked(
    rank: int, name: str, pos: str, team: str = "ARI", pts: float = 200.0
) -> RankedPlayer:
    """Helper to create a RankedPlayer for tests."""
    return RankedPlayer(
        rank=rank,
        player=Player(player_id=str(rank), full_name=name, position=pos, team=team),
        projected_points=pts,
        tier=(rank - 1) // 12 + 1,
    )


# --- Strategy tests ---


def test_floor_bonus_top_rank():
    assert floor_bonus_from_rank(1) == 1.15
    assert floor_bonus_from_rank(12) == 1.15


def test_floor_bonus_mid_rank():
    assert floor_bonus_from_rank(50) == 1.00
    assert floor_bonus_from_rank(100) == 0.95


def test_floor_bonus_low_rank():
    assert floor_bonus_from_rank(200) == 0.90
    assert floor_bonus_from_rank(250) == 0.85


def test_position_scarcity_severe_shortage():
    # 5 remaining, 32 teams need 2 each = 64 needed
    scar = estimate_position_scarcity("RB", 75, 80, 32)
    assert scar >= 1.25  # moderate to severe shortage


def test_position_scarcity_adequate():
    # 35 remaining, 32 teams need 1 each = 32 needed (ratio > 1.0)
    scar = estimate_position_scarcity("QB", 5, 40, 32)
    assert scar <= 1.0  # adequate or surplus


def test_position_scarcity_run_out():
    scar = estimate_position_scarcity("TE", 30, 30, 32)
    assert scar == 1.5  # position is run out


def test_need_bonus_critical():
    roster = {"QB": 0, "RB": 2, "WR": 3, "TE": 1}
    assert compute_need_bonus("QB", roster) == 1.50  # critical - no QB


def test_need_bonus_depth():
    roster = {"QB": 1, "RB": 2, "WR": 3, "TE": 1}
    assert compute_need_bonus("QB", roster) == 1.15  # depth - have 1, need 1


def test_need_bonus_luxury():
    roster = {"QB": 2, "RB": 4, "WR": 5, "TE": 2}
    assert compute_need_bonus("QB", roster) == 1.00  # luxury - have 2


def test_scorer_basic():
    players = [
        _make_ranked(1, "Elite RB", "RB", pts=300),
        _make_ranked(2, "Elite WR", "WR", pts=280),
        _make_ranked(10, "Good TE", "TE", pts=180),
    ]
    scorer = GuillotineScorer(total_teams=32)
    scored = scorer.score_players(
        ranked_players=players,
        taken_player_ids=set(),
        user_roster_positions={"QB": 1, "RB": 1, "WR": 1, "TE": 0, "K": 1, "DEF": 1},
        taken_at_position={"RB": 0, "WR": 0, "TE": 0},
    )
    assert len(scored) == 3
    # Should be sorted by survival score descending
    assert scored[0].survival_score >= scored[1].survival_score
    assert scored[1].survival_score >= scored[2].survival_score


def test_scorer_excludes_taken():
    players = [
        _make_ranked(1, "Player A", "RB", pts=300),
        _make_ranked(2, "Player B", "WR", pts=280),
    ]
    scorer = GuillotineScorer(total_teams=32)
    scored = scorer.score_players(
        ranked_players=players,
        taken_player_ids={"1"},  # Player A is taken
        user_roster_positions={},
        taken_at_position={"RB": 1},
    )
    assert len(scored) == 1
    assert scored[0].player.full_name == "Player B"


def test_scorer_need_bonus_matters():
    """A lower-ranked player at a critical need should rank higher."""
    players = [
        _make_ranked(30, "Okay WR", "WR", pts=180),
        _make_ranked(5, "Great RB", "RB", pts=260),
    ]
    scorer = GuillotineScorer(total_teams=32)

    # User has no TE, but has RBs - the WR (which fills FLEX) might rank differently
    scored_critical = scorer.score_players(
        ranked_players=players,
        taken_player_ids=set(),
        user_roster_positions={"QB": 1, "RB": 3, "WR": 0, "TE": 1},
        taken_at_position={},
    )
    # The WR should get a need bonus since count=0 < starters_needed=2
    wr_score = next(s for s in scored_critical if s.position == "WR")
    rb_score = next(s for s in scored_critical if s.position == "RB")
    assert wr_score.need_bonus > rb_score.need_bonus


def test_waiver_projection():
    scorer = GuillotineScorer(total_teams=32)
    proj = scorer.get_waiver_projection(weeks_remaining=14)
    assert "QB" in proj
    assert "RB" in proj
    assert proj["QB"]["streamable"] is True
    assert proj["TE"]["streamable"] is False
    assert proj["RB"]["projected_released"] > 0


# --- Board tests ---


def test_guillotine_board_recommendations():
    players = [
        _make_ranked(1, "Elite RB", "RB", pts=300),
        _make_ranked(2, "Elite WR", "WR", pts=280),
        _make_ranked(3, "Elite WR2", "WR", pts=270),
        _make_ranked(4, "Good QB", "QB", pts=350),
        _make_ranked(10, "Good TE", "TE", pts=180),
    ]
    board = GuillotineDraftBoard(
        draft_id="test",
        total_rounds=3,
        total_teams=32,
        ranked_players=players,
        scorer=GuillotineScorer(total_teams=32),
    )
    recs = board.recommend_pick(5)
    assert len(recs) == 5
    # Should return RankedPlayer objects
    assert all(hasattr(r, "rank") for r in recs)


def test_guillotine_board_positional_runs():
    players = [
        _make_ranked(1, "RB A", "RB"),
        _make_ranked(2, "RB B", "RB"),
        _make_ranked(3, "RB C", "RB"),
        _make_ranked(4, "WR A", "WR"),
        _make_ranked(5, "RB D", "RB"),
    ]
    board = GuillotineDraftBoard(
        draft_id="test",
        total_rounds=2,
        total_teams=32,
        ranked_players=players,
    )
    # Simulate 4 RBs taken in last 5 picks
    board.picks = [
        DraftPick(pick_no=1, player_id="1", roster_id=1),
        DraftPick(pick_no=2, player_id="2", roster_id=2),
        DraftPick(pick_no=3, player_id="3", roster_id=3),
        DraftPick(pick_no=4, player_id="5", roster_id=4),
        DraftPick(pick_no=5, player_id="4", roster_id=5),
    ]
    board.taken_player_ids = {"1", "2", "3", "4", "5"}

    runs = board.detect_positional_runs(window=5)
    rb_run = next((r for r in runs if r.position == "RB"), None)
    assert rb_run is not None
    assert rb_run.count == 4
    assert rb_run.severity == "severe"


def test_guillotine_board_floor_analysis():
    players = [
        _make_ranked(1, "Elite", "RB"),
        _make_ranked(50, "Mid", "WR"),
        _make_ranked(150, "Risky", "TE"),
    ]
    board = GuillotineDraftBoard(
        draft_id="test",
        total_rounds=2,
        total_teams=32,
        ranked_players=players,
    )
    analysis = board.get_floor_analysis(3)
    assert len(analysis) == 3
    assert analysis[0]["floor"] == "ELITE"
    assert analysis[1]["floor"] == "SOLID"
    assert analysis[2]["floor"] == "RISKY"


def test_guillotine_board_positional_depth():
    players = [
        _make_ranked(1, "QB1", "QB"),
        _make_ranked(2, "QB2", "QB"),
        _make_ranked(3, "RB1", "RB"),
        _make_ranked(4, "WR1", "WR"),
    ]
    board = GuillotineDraftBoard(
        draft_id="test",
        total_rounds=2,
        total_teams=32,
        ranked_players=players,
    )
    depth = board.get_positional_depth()
    assert "QB" in depth
    assert depth["QB"]["top_tier"] == 2
    assert depth["RB"]["top_tier"] == 1


def test_guillotine_board_waiver_projection():
    board = GuillotineDraftBoard(
        draft_id="test",
        total_rounds=15,
        total_teams=32,
        ranked_players=[],
        scorer=GuillotineScorer(total_teams=32),
    )
    proj = board.get_waiver_projection(weeks_remaining=10)
    assert "QB" in proj
    assert proj["QB"]["streamable"] is True


# --- Startable depth and the conditional streaming discount ---


def _board(qbs: int, wrs: int, tail: int = 0) -> list[RankedPlayer]:
    """A board of `qbs` QBs and `wrs` WRs, plus an unstartable tail of QBs.

    The tail is the thing that used to break scarcity: extra bodies at a
    position that inflate supply without being startable anywhere.
    """
    players = []
    rank = 1
    for i in range(qbs):
        players.append(_make_ranked(rank, f"QB {i}", "QB"))
        rank += 1
    for i in range(wrs):
        players.append(_make_ranked(rank, f"WR {i}", "WR"))
        rank += 1
    for i in range(tail):
        players.append(_make_ranked(rank, f"Backup QB {i}", "QB"))
        rank += 1
    return players


def test_startable_depth_cuts_the_board_at_what_the_league_drafts():
    players = _board(qbs=5, wrs=5, tail=90)
    depth = startable_depth(players, total_teams=2, roster_size=5)
    # 10 players drafted: ranks 1-10, so the 90-deep QB tail is excluded.
    assert depth == {"QB": 5, "WR": 5}


def test_startable_depth_reads_ranks_not_list_order():
    """An unsorted board must not be sampled by arrival order."""
    players = _board(qbs=3, wrs=3)
    shuffled = [players[4], players[0], players[3], players[2], players[1], players[5]]
    assert startable_depth(shuffled, total_teams=1, roster_size=3) == {"QB": 3}


def test_startable_depth_declines_to_guess_without_a_league_shape():
    assert startable_depth(_board(2, 2), total_teams=0, roster_size=5) == {}
    assert startable_depth(_board(2, 2), total_teams=5, roster_size=0) == {}


def test_scarcity_clamp_exposes_a_shortage_the_deep_tail_was_hiding():
    """The bug this replaced: a long board made every position a surplus."""
    # 32 teams need one QB each; 35 are startable but the board lists 49.
    unclamped = estimate_position_scarcity("QB", 0, 49, 32, starters_needed=1.0)
    clamped = estimate_position_scarcity(
        "QB", 0, 49, 32, starters_needed=1.0, startable_total=35
    )
    assert unclamped < 1.0  # read as surplus
    assert clamped >= unclamped


def test_scarcity_clamp_counts_picks_against_the_whole_board():
    """`taken` is board-scoped, so the clamp must not double-subtract it."""
    # 20 TEs startable of 85 listed, 10 already drafted: 10 startable left.
    both_ways = estimate_position_scarcity(
        "TE", 10, 85, 32, starters_needed=1.0, startable_total=20
    )
    # 10 left against 32 needed is a ratio of 0.31, a severe shortage.
    assert both_ways == 1.4


def test_streaming_discount_full_when_the_waiver_wire_is_deep():
    """One spare startable per team means streaming genuinely works."""
    assert streaming_discount(0.65, 24, league_demand=12, total_teams=12) == 0.65


def test_streaming_discount_disappears_when_nothing_is_left_to_stream():
    """32 teams starting one QB each drafts every startable QB."""
    assert streaming_discount(0.65, 32, league_demand=32, total_teams=32) == 1.0


def test_streaming_discount_scales_between_the_two():
    mid = streaming_discount(0.65, 35, league_demand=32, total_teams=32)
    assert 0.65 < mid < 1.0


def test_deep_league_stops_marking_quarterbacks_down_as_streamable():
    """A 32-team league has no QB waiver wire, so the discount should lift."""
    shallow = GuillotineScorer.from_rankings(
        _board(qbs=25, wrs=100, tail=24),
        total_teams=12,
        roster_size=16,
        starters={"QB": 1, "WR": 2},
    )
    deep = GuillotineScorer.from_rankings(
        _board(qbs=35, wrs=100, tail=14),
        total_teams=32,
        roster_size=9,
        starters={"QB": 1, "WR": 2},
    )
    assert shallow._position_value("QB") < deep._position_value("QB")
    # The shallow league is where the hand-tuned prior still applies as written.
    assert shallow._position_value("QB") == 0.65


def test_non_streamable_positions_keep_their_hand_tuned_prior():
    scorer = GuillotineScorer.from_rankings(
        _board(qbs=35, wrs=100), total_teams=32, roster_size=9
    )
    assert scorer._position_value("WR") == 1.05
    assert scorer._position_value("TE") == 1.00


def test_a_scorer_built_without_a_board_does_not_invent_a_depth():
    """No board means no startable measurement, so behaviour is unchanged."""
    scorer = GuillotineScorer(total_teams=32)
    assert scorer.startable_totals == {}
    assert scorer._position_value("QB") == 0.65


# --- Injury / availability tests ---


def _injured(
    rank: int,
    name: str,
    pos: str,
    injury_status: str = "",
    body_part: str = "",
    status: str = "Active",
) -> RankedPlayer:
    """A RankedPlayer carrying an injury designation."""
    rp = _make_ranked(rank, name, pos)
    rp.player.injury_status = injury_status
    rp.player.injury_body_part = body_part
    rp.player.status = status
    return rp


def test_a_clean_player_is_not_penalized():
    assert injury_availability("") == 1.0
    assert injury_availability("Active") == 1.0
    # ESPN publishes the uppercase form where Sleeper publishes null.
    assert injury_availability("ACTIVE", "") == 1.0


def test_designations_are_ordered_by_how_likely_a_player_is_to_play():
    questionable = injury_availability("Questionable")
    doubtful = injury_availability("Doubtful")
    out = injury_availability("Out")
    ir = injury_availability("IR")
    assert 1.0 > questionable > doubtful > out > ir > 0.0


def test_an_out_player_beats_one_on_ir_because_he_is_coming_back():
    """Sleeper's "Out" means out for this game; IR means gone for a stretch."""
    assert injury_availability("Out", replacement_ratio=0.4) > injury_availability(
        "IR", replacement_ratio=0.4
    )


def test_designation_matching_is_case_insensitive():
    assert injury_availability("questionable") == injury_availability("QUESTIONABLE")


def test_a_structural_knee_injury_is_worse_than_generic_soreness():
    """Sleeper qualifies the joint, so "Knee - ACL" must not read as "Knee"."""
    acl = injury_availability("Questionable", "Knee - ACL")
    knee = injury_availability("Questionable", "Knee")
    undisclosed = injury_availability("Questionable", "Undisclosed")
    assert acl < knee < undisclosed


def test_a_compound_ligament_tear_is_treated_as_structural():
    assert injury_availability("Questionable", "Knee - ACL + MCL") == (
        injury_availability("Questionable", "Knee - ACL")
    )


def test_an_illness_is_lighter_than_a_soft_tissue_injury():
    illness = injury_availability("Questionable", "Illness")
    hamstring = injury_availability("Questionable", "Hamstring")
    assert hamstring < illness < 1.0


def test_an_unrecognized_body_part_reads_the_same_as_a_withheld_one():
    """Both mean "flagged, diagnosis unknown" - neither is a reason to guess."""
    assert injury_availability("Questionable", "Eyelash") == (
        injury_availability("Questionable", "")
    )


def test_roster_standing_catches_a_player_with_no_game_day_designation():
    """A player parked on IR is unavailable whether or not this week says so."""
    assert injury_availability("", "", "Injured Reserve") < 0.25
    assert injury_availability("", "", "Physically Unable to Perform") < 0.5


def test_the_worse_of_the_two_availability_signals_wins():
    """Sleeper reports a Questionable player as status "Active"."""
    both = injury_availability("Questionable", "", "Injured Reserve")
    assert both == injury_availability("", "", "Injured Reserve")


def test_an_unknown_designation_is_not_assigned_an_invented_penalty():
    assert injury_availability("Rehabbing", "Knee - ACL") == 1.0


def test_the_penalty_never_reaches_zero():
    """Even the worst case keeps a player comparable rather than unrankable."""
    assert injury_availability("IR", "Knee - ACL + MCL") > 0.0


def test_an_injury_designation_lowers_a_players_survival_score():
    scorer = GuillotineScorer.from_rankings(
        _board(qbs=35, wrs=100), total_teams=32, roster_size=9
    )
    healthy = scorer.score_player(_make_ranked(5, "Clean Back", "RB"))
    hurt = scorer.score_player(
        _injured(5, "Sore Back", "RB", "Questionable", "Knee")
    )
    assert hurt.survival_score < healthy.survival_score
    assert hurt.availability < 1.0
    # Everything except availability is identical, so the gap is only the injury.
    assert hurt.rank_score == healthy.rank_score
    assert hurt.floor_bonus == healthy.floor_bonus
    assert healthy.availability == 1.0


def test_survival_score_is_the_product_of_its_factors():
    scorer = GuillotineScorer.from_rankings(
        _board(qbs=35, wrs=100), total_teams=32, roster_size=9
    )
    ps = scorer.score_player(_injured(5, "Sore Back", "RB", "Out", "Achilles"))
    expected = (
        ps.rank_score
        * ps.floor_bonus
        * ps.position_value
        * ps.scarcity_bonus
        * ps.need_bonus
        * ps.availability
    )
    assert abs(ps.survival_score - expected) < 1e-9


def test_injury_scoring_can_be_switched_off():
    board = _board(qbs=35, wrs=100)
    on = GuillotineScorer.from_rankings(board, total_teams=32, roster_size=9)
    off = GuillotineScorer.from_rankings(
        board, total_teams=32, roster_size=9, use_injury_status=False
    )
    hurt = _injured(5, "Sore Back", "RB", "Out", "Achilles")
    assert off.score_player(hurt).availability == 1.0
    assert off.score_player(hurt).survival_score > on.score_player(hurt).survival_score


def test_a_severe_injury_can_move_a_player_behind_a_healthy_one():
    """The point of the factor: availability outranks a small rank edge."""
    scorer = GuillotineScorer.from_rankings(
        _board(qbs=35, wrs=100), total_teams=32, roster_size=9
    )
    scored = scorer.score_players(
        [
            _injured(5, "Torn Ligament", "RB", "Out", "Knee - ACL"),
            _make_ranked(9, "Fully Healthy", "RB"),
        ]
    )
    assert scored[0].display_name == "Fully Healthy"


def test_injury_label_reads_as_designation_and_body_part():
    scorer = GuillotineScorer.from_rankings(
        _board(qbs=35, wrs=100), total_teams=32, roster_size=9
    )
    ps = scorer.score_player(_injured(5, "Sore Back", "RB", "Questionable", "Knee"))
    assert ps.injury_label == "Questionable (Knee)"


def test_injury_label_omits_a_body_part_that_says_nothing():
    scorer = GuillotineScorer.from_rankings(
        _board(qbs=35, wrs=100), total_teams=32, roster_size=9
    )
    ps = scorer.score_player(
        _injured(5, "Sore Back", "RB", "Questionable", "Undisclosed")
    )
    assert ps.injury_label == "Questionable"


def test_a_healthy_player_has_no_injury_label():
    scorer = GuillotineScorer.from_rankings(
        _board(qbs=35, wrs=100), total_teams=32, roster_size=9
    )
    assert scorer.score_player(_make_ranked(5, "Clean Back", "RB")).injury_label == ""


def test_a_stretch_absence_ignores_the_body_part():
    """For a player on IR, "how limited when he plays" is not the live question."""
    assert injury_availability("IR", "Knee - ACL") == injury_availability("IR", "Illness")
    assert injury_availability("IR", "Knee - ACL") == injury_availability("IR", "")


def test_designation_ordering_survives_the_worst_body_part():
    """No body part may push a milder designation below a harsher one."""
    worst_q = injury_availability("Questionable", "Knee - ACL + MCL")
    worst_d = injury_availability("Doubtful", "Knee - ACL + MCL")
    best_out = injury_availability("Out", "Illness")
    best_ir = injury_availability("IR", "Illness")
    assert worst_q > worst_d > best_out > best_ir


# --- Replacement-aware availability ---


def _deep_board(pos: str, count: int, tail: int = 0) -> list[RankedPlayer]:
    """A board holding `count` players at one position, then filler."""
    board = [_make_ranked(i, f"{pos}{i}", pos) for i in range(1, count + 1)]
    board += [
        _make_ranked(count + i, f"Filler{i}", "WR")
        for i in range(1, tail + 1)
    ]
    return board


def test_replacement_ratio_is_lower_for_a_better_player():
    """Losing the best back at a position costs more than losing a marginal one."""
    scorer = GuillotineScorer.from_rankings(
        _deep_board("RB", 120), total_teams=12, roster_size=9, starters={"RB": 2}
    )
    board = _deep_board("RB", 120)
    elite = scorer.replacement_ratio(board[0])
    marginal = scorer.replacement_ratio(board[60])
    assert elite < marginal


def test_a_deep_league_has_a_worse_replacement_than_a_shallow_one():
    """The same player, the same board - only the league's depth differs."""
    board = _deep_board("RB", 120)
    shallow = GuillotineScorer.from_rankings(
        board, total_teams=10, roster_size=9, starters={"RB": 2, "BN": 1}
    )
    deep = GuillotineScorer.from_rankings(
        board, total_teams=32, roster_size=9, starters={"RB": 2, "BN": 1}
    )
    assert deep.replacement_ratio(board[0]) < shallow.replacement_ratio(board[0])


def test_replacement_past_the_end_of_the_board_is_extrapolated():
    """A league deeper than the board must not clamp to the last ranked player."""
    board = _deep_board("RB", 20)
    scorer = GuillotineScorer.from_rankings(
        board, total_teams=32, roster_size=9, starters={"RB": 2, "BN": 1}
    )
    ratio = scorer.replacement_ratio(board[0])
    assert 0.0 < ratio < 1.0


def test_replacement_ratio_declines_to_guess_without_a_board():
    scorer = GuillotineScorer(total_teams=32)
    assert scorer.replacement_ratio(_make_ranked(1, "Someone", "RB")) == (
        DEFAULT_REPLACEMENT_RATIO
    )


def test_the_same_injury_costs_more_where_the_replacement_is_worse():
    """The whole point of the rewrite: the penalty is league-derived, not fixed."""
    replaceable = injury_availability("Questionable", "Knee", replacement_ratio=0.9)
    irreplaceable = injury_availability("Questionable", "Knee", replacement_ratio=0.1)
    assert irreplaceable < replaceable


def test_a_hobbled_player_is_never_worth_less_than_his_replacement():
    """You would simply start the replacement, so the value floors there."""
    avail = injury_availability(
        "Questionable", "Knee - ACL", replacement_ratio=0.95
    )
    assert avail >= 0.95


def test_availability_is_monotonic_in_the_chance_of_playing():
    """Guaranteed by max(e, r); a plain p*e + (1-p)*r inverts when r > e.

    With a replacement better than the hobbled player, both collapse to the
    replacement's value - the point is that the worse designation never scores
    higher, which is what the naive form gets wrong.
    """
    for ratio in (0.1, 0.5, 0.9):
        high = injury_availability(
            "Questionable", "Knee - ACL", replacement_ratio=ratio
        )
        low = injury_availability("Doubtful", "Knee - ACL", replacement_ratio=ratio)
        assert high >= low - 1e-9


def test_a_stretch_absence_gets_no_replacement_credit_on_a_thin_bench():
    """With one bench spot an IR pick is dead weight, not a stashed asset."""
    thin = injury_availability("IR", replacement_ratio=0.5, carryable=0.0)
    deep = injury_availability("IR", replacement_ratio=0.5, carryable=1.0)
    assert thin < deep


def test_bench_spots_come_from_the_lineup_settings():
    scorer = GuillotineScorer(
        total_teams=32,
        roster_size=9,
        starters={"QB": 1, "RB": 2, "WR": 2, "TE": 1, "FLEX": 2, "BN": 1},
    )
    assert scorer.bench_spots == 1
    # A one-spot bench cannot stash anybody: that spot is the only bye cover.
    assert scorer._carryable() == 0.0


def test_bench_spots_are_inferred_when_the_lineup_does_not_name_them():
    scorer = GuillotineScorer(
        total_teams=12, roster_size=16, starters={"QB": 1, "RB": 2, "WR": 2, "TE": 1}
    )
    assert scorer.bench_spots == 10
    assert scorer._carryable() == 1.0


def test_a_guillotine_league_penalizes_an_injury_harder_than_a_shallow_one():
    """End to end: identical player and designation, different league."""
    board = _deep_board("RB", 120)
    for rp in (board[0],):
        rp.player.injury_status = "Questionable"
        rp.player.injury_body_part = "Knee"
    shape = {"starters": {"RB": 2, "BN": 1}, "roster_size": 9}
    shallow = GuillotineScorer.from_rankings(board, total_teams=10, **shape)
    deep = GuillotineScorer.from_rankings(board, total_teams=32, **shape)
    assert deep.availability_of(board[0]) < shallow.availability_of(board[0])

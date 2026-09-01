"""Tests for cross-provider pick matching and draft-slot resolution.

Picks and rankings normally come from different providers with unrelated player
ID spaces, so these paths are what keep drafted players from sitting on the
board forever.
"""

from ff_tools.draft.assistant import resolve_draft_slot
from ff_tools.draft.board import DraftBoard
from ff_tools.draft.rankings import RankedPlayer
from ff_tools.espn_draft.assistant import slot_from_pick_order
from ff_tools.models.draft import Draft, DraftPick, _sleeper_roster_slots
from ff_tools.models.player import Player


def _ranked(rank: int, name: str, pos: str, team: str = "CIN") -> RankedPlayer:
    return RankedPlayer(
        rank=rank,
        player=Player(
            player_id=f"fp-{rank}", full_name=name, position=pos, team=team
        ),
    )


def _board(*ranked: RankedPlayer) -> DraftBoard:
    return DraftBoard(
        draft_id="t", total_rounds=3, total_teams=4, ranked_players=list(ranked)
    )


# ── Pick -> ranked player ───────────────────────────────────────────


def test_pick_matches_by_name_when_ids_are_disjoint():
    board = _board(_ranked(1, "Ja'Marr Chase", "WR"))
    pick = DraftPick(
        pick_no=1, player_id="sleeper-7564", player_name="JaMarr Chase",
        position="WR", team="CIN",
    )
    board.update_picks([pick])

    assert board.get_available() == []
    assert board.unmatched_picks == []


def test_pick_matches_by_id_when_sources_agree():
    board = _board(_ranked(1, "Whoever", "RB"))
    board.update_picks([DraftPick(pick_no=1, player_id="fp-1")])
    assert board.get_available() == []


def test_unmatched_pick_is_recorded_not_swallowed():
    """An unmatched pick must be visible, or a silent mismatch looks like a hit."""
    board = _board(_ranked(1, "Rostered Guy", "WR"))
    stray = DraftPick(
        pick_no=1, player_id="sleeper-1", player_name="Some Rookie",
        position="WR", team="NYJ",
    )
    board.update_picks([stray])

    assert board.unmatched_picks == [stray]
    # The ranked player was not taken, so he stays on the board.
    assert len(board.get_available()) == 1


def test_taken_key_registered_for_both_spellings():
    """A later pick of the same player under either name resolves as taken."""
    board = _board(_ranked(1, "Marvin Harrison Jr.", "WR", "ARI"))
    board.update_picks([
        DraftPick(pick_no=1, player_name="Marvin Harrison", position="WR", team="ARI")
    ])
    assert board.is_taken(board.ranked_players[0])


def test_defense_pick_matches_a_differently_named_defense():
    board = _board(_ranked(80, "Philadelphia Eagles", "DEF", "PHI"))
    board.update_picks([
        DraftPick(pick_no=1, player_name="Eagles D/ST", position="DST", team="PHI")
    ])
    assert board.get_available() == []


def test_position_of_prefers_the_pick_over_the_rankings():
    board = _board(_ranked(1, "Travis Etienne", "RB", "JAX"))
    pick = DraftPick(pick_no=1, player_name="Travis Etienne", position="WR", team="JAX")
    # The platform's own label wins; the rankings provider is the fallback.
    assert board.position_of(pick) == "WR"
    assert board.position_of(DraftPick(pick_no=2, player_id="fp-1")) == "RB"


def test_player_name_falls_back_through_rankings_then_id():
    board = _board(_ranked(1, "Bijan Robinson", "RB", "ATL"))
    assert board.player_name(DraftPick(pick_no=1, player_id="fp-1")) == "Bijan Robinson"
    assert board.player_name(DraftPick(pick_no=2, player_id="xyz")) == "xyz"
    assert board.player_name(DraftPick(pick_no=3)) == "?"


def test_set_rankings_rebuilds_the_index():
    board = _board(_ranked(1, "Old Guy", "RB"))
    board.set_rankings([_ranked(1, "New Guy", "WR")])
    board.update_picks([DraftPick(pick_no=1, player_name="New Guy", position="WR")])
    assert board.get_available() == []


# ── Board state ─────────────────────────────────────────────────────


def test_is_complete():
    board = _board(*[_ranked(n, f"P{n}", "RB") for n in range(1, 20)])
    assert board.is_complete is False
    board.update_picks([DraftPick(pick_no=n) for n in range(1, 13)])
    assert board.is_complete is True


def test_next_user_pick_and_countdown():
    board = _board()
    board.user_picks = [3, 6, 11]
    assert board.next_user_pick() == 3
    assert board.picks_until_user_turn() == 2

    board.update_picks([DraftPick(pick_no=n) for n in (1, 2, 3, 4, 5)])
    assert board.next_user_pick() == 6
    assert board.picks_until_user_turn() == 0
    assert board.is_user_pick_next is True

    board.update_picks([DraftPick(pick_no=n) for n in range(6, 12)])
    assert board.next_user_pick() is None
    assert board.picks_until_user_turn() == 0


def test_is_user_pick_uses_roster_id_then_pick_number():
    board = _board()
    board.user_roster_id = 22
    assert board.is_user_pick(DraftPick(pick_no=1, roster_id=22)) is True
    assert board.is_user_pick(DraftPick(pick_no=1, roster_id=3)) is False

    # Platforms that omit a roster ID fall back to the pick number.
    board.user_picks = [7]
    assert board.is_user_pick(DraftPick(pick_no=7)) is True
    assert board.is_user_pick(DraftPick(pick_no=8)) is False


# ── Draft slot resolution ───────────────────────────────────────────


def test_resolve_draft_slot_uses_the_seating_chart_not_the_roster_id():
    """A roster ID is not a draft slot; roster 22 can sit in seat 1."""
    draft = Draft(draft_id="d", slot_to_roster_id={1: 22, 2: 5, 3: 9})
    assert resolve_draft_slot(draft, roster_id=22) == 1
    assert resolve_draft_slot(draft, roster_id=9) == 3


def test_resolve_draft_slot_falls_back_to_the_user_id():
    draft = Draft(draft_id="d", draft_order={"abc123": 4})
    assert resolve_draft_slot(draft, roster_id=7, user_id="abc123") == 4


def test_resolve_draft_slot_returns_zero_when_unknown():
    """Zero means unresolved - better than guessing another team's picks."""
    draft = Draft(draft_id="d", slot_to_roster_id={1: 22})
    assert resolve_draft_slot(draft, roster_id=99, user_id="nobody") == 0


def test_slot_from_pick_order():
    assert slot_from_pick_order([7, 3, 9, 1], 9) == 3
    assert slot_from_pick_order(["7", "3"], 3) == 2
    assert slot_from_pick_order([7, 3], 99) == 0
    assert slot_from_pick_order(None, 3) == 0


# ── Sleeper draft settings ──────────────────────────────────────────


def test_sleeper_roster_slots_sums_flex_variants_and_drops_empties():
    settings = {
        "rounds": 15,
        "slots_qb": 1,
        "slots_rb": 2,
        "slots_wr": 2,
        "slots_te": 1,
        "slots_flex": 1,
        "slots_wr_te_flex": 1,
        "slots_super_flex": 1,
        "slots_k": 0,
        "slots_bn": 6,
        "slots_def": None,
    }
    assert _sleeper_roster_slots(settings) == {
        "QB": 1, "RB": 2, "WR": 2, "TE": 1, "FLEX": 2, "SUPER_FLEX": 1, "BN": 6,
    }


def test_draft_from_sleeper_reads_seating_and_reversal():
    draft = Draft.from_sleeper({
        "draft_id": "d1",
        "league_id": "l1",
        "type": "snake",
        "settings": {"rounds": 15, "reversal_round": 3, "teams": 12, "slots_qb": 1},
        "draft_order": {"user-a": 1, "user-b": 2, "user-c": None},
        "slot_to_roster_id": {"1": 22, "2": 5},
    })

    assert draft.rounds == 15
    assert draft.reversal_round == 3
    assert draft.total_teams == 12
    assert draft.slot_for_roster(22) == 1
    assert draft.slot_for_user("user-a") == 1
    assert draft.slot_for_user("user-c") is None
    assert draft.roster_slots == {"QB": 1}

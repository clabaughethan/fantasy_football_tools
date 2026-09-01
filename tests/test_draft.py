"""Tests for draft assistant components."""

from ff_tools.draft.board import DraftBoard
from ff_tools.draft.rankings import RankedPlayer, _tier_from_rank
from ff_tools.models.draft import DraftPick
from ff_tools.models.player import Player


def test_tier_from_rank():
    assert _tier_from_rank(1) == 1
    assert _tier_from_rank(12) == 1
    assert _tier_from_rank(13) == 2
    assert _tier_from_rank(24) == 2
    assert _tier_from_rank(25) == 3
    assert _tier_from_rank(48) == 3
    assert _tier_from_rank(49) == 4
    assert _tier_from_rank(120) == 5
    assert _tier_from_rank(121) == 6


def test_draft_board_update_picks():
    board = DraftBoard(
        draft_id="test",
        total_rounds=3,
        total_teams=2,
        ranked_players=[
            RankedPlayer(rank=1, player=Player(player_id="1", full_name="Player A", position="QB")),
            RankedPlayer(rank=2, player=Player(player_id="2", full_name="Player B", position="RB")),
            RankedPlayer(rank=3, player=Player(player_id="3", full_name="Player C", position="WR")),
        ],
    )

    picks = [DraftPick(pick_no=1, player_id="1", roster_id=1)]
    new = board.update_picks(picks)
    assert len(new) == 1
    assert "1" in board.taken_player_ids

    # Same pick again = no new
    new2 = board.update_picks(picks)
    assert len(new2) == 0


def test_draft_board_best_available():
    board = DraftBoard(
        draft_id="test",
        total_rounds=2,
        total_teams=2,
        ranked_players=[
            RankedPlayer(rank=1, player=Player(player_id="1", full_name="Player A", position="QB")),
            RankedPlayer(rank=2, player=Player(player_id="2", full_name="Player B", position="RB")),
            RankedPlayer(rank=3, player=Player(player_id="3", full_name="Player C", position="WR")),
        ],
    )

    # Pick player 1
    board.update_picks([DraftPick(pick_no=1, player_id="1", roster_id=1)])

    available = board.get_best_available(5)
    assert len(available) == 2
    assert available[0].player.player_id == "2"
    assert available[1].player.player_id == "3"


def test_draft_board_positional_scarcity():
    board = DraftBoard(
        draft_id="test",
        total_rounds=2,
        total_teams=2,
        ranked_players=[
            RankedPlayer(rank=1, player=Player(player_id="1", full_name="A", position="QB")),
            RankedPlayer(rank=2, player=Player(player_id="2", full_name="B", position="QB")),
            RankedPlayer(rank=3, player=Player(player_id="3", full_name="C", position="RB")),
            RankedPlayer(rank=4, player=Player(player_id="4", full_name="D", position="WR")),
        ],
    )

    board.update_picks([DraftPick(pick_no=1, player_id="1", roster_id=1)])
    scarcity = board.get_positional_scarcity()

    assert scarcity["QB"]["taken"] == 1
    assert scarcity["QB"]["remaining"] == 1
    assert scarcity["RB"]["taken"] == 0
    assert scarcity["RB"]["remaining"] == 1


def test_draft_board_picks_by_round():
    board = DraftBoard(
        draft_id="test",
        total_rounds=2,
        total_teams=2,
    )

    board.update_picks([
        DraftPick(pick_no=1, player_id="1", roster_id=1),
        DraftPick(pick_no=2, player_id="2", roster_id=2),
        DraftPick(pick_no=3, player_id="3", roster_id=2),
        DraftPick(pick_no=4, player_id="4", roster_id=1),
    ])

    rounds = board.get_picks_by_round()
    assert len(rounds) == 2
    assert len(rounds[1]) == 2
    assert len(rounds[2]) == 2

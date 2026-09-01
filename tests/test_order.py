"""Tests for snake draft pick math, plain and with Nth-round reversal."""

import pytest

from ff_tools.draft import order


def test_plain_snake_directions():
    assert order.is_ascending(1) is True
    assert order.is_ascending(2) is False
    assert order.is_ascending(3) is True
    assert order.is_ascending(4) is False


def test_reversal_repeats_then_resumes_alternating():
    """3RR: R2 and R3 both run in the same direction, parity flips after."""
    directions = [order.is_ascending(r, 3) for r in range(1, 7)]
    assert directions == [True, False, False, True, False, True]


def test_reversal_round_of_one_is_ignored():
    """A reversal in round 1 has no previous round to repeat."""
    assert [order.is_ascending(r, 1) for r in range(1, 5)] == [
        order.is_ascending(r) for r in range(1, 5)
    ]


def test_pick_number_first_slot_plain_snake():
    # 12 teams, slot 1: 1, 24, 25, 48, 49...
    assert order.calculate_pick_numbers(1, 12, 5) == [1, 24, 25, 48, 49]


def test_pick_number_last_slot_plain_snake():
    # Slot 12 mirrors slot 1: 12, 13, 36, 37, 60
    assert order.calculate_pick_numbers(12, 12, 5) == [12, 13, 36, 37, 60]


def test_pick_number_middle_slot_plain_snake():
    assert order.calculate_pick_numbers(5, 12, 4) == [5, 20, 29, 44]


def test_pick_number_with_3rr():
    """Slot 1 loses the R3 turn it would get in a plain snake."""
    assert order.calculate_pick_numbers(1, 12, 5, 3) == [1, 24, 36, 37, 60]
    assert order.calculate_pick_numbers(12, 12, 5, 3) == [12, 13, 25, 48, 49]


def test_3rr_hurts_the_first_slot():
    """The point of 3RR: slot 1's second and third picks come later."""
    plain = order.calculate_pick_numbers(1, 12, 3)
    rr = order.calculate_pick_numbers(1, 12, 3, 3)
    assert rr[2] > plain[2]


def test_every_pick_assigned_exactly_once():
    """Across all slots and rounds, pick numbers tile 1..teams*rounds."""
    for reversal in (0, 3, 4):
        picks = [
            p
            for slot in range(1, 13)
            for p in order.calculate_pick_numbers(slot, 12, 6, reversal)
        ]
        assert sorted(picks) == list(range(1, 12 * 6 + 1))


def test_round_of_pick():
    assert order.round_of_pick(1, 12) == 1
    assert order.round_of_pick(12, 12) == 1
    assert order.round_of_pick(13, 12) == 2
    assert order.round_of_pick(24, 12) == 2
    assert order.round_of_pick(25, 12) == 3


def test_pick_in_round_is_sequential_not_slot():
    """P01 of a descending round is still P01, though slot 12 owns it."""
    assert order.pick_in_round(13, 12) == 1
    assert order.slot_at_pick(13, 12) == 12


@pytest.mark.parametrize("reversal", [0, 3, 4])
def test_slot_at_pick_inverts_pick_number(reversal):
    for slot in range(1, 13):
        for round_num in range(1, 8):
            pick = order.pick_number(slot, round_num, 12, reversal)
            assert order.slot_at_pick(pick, 12, reversal) == slot


def test_zero_teams_degrades_quietly():
    assert order.pick_number(1, 1, 0) == 0
    assert order.calculate_pick_numbers(1, 0, 5) == []
    assert order.slot_at_pick(1, 0) == 0
    assert order.round_of_pick(1, 0) == 1


def test_zero_slot_has_no_picks():
    """An unresolved draft slot yields no picks rather than a bogus list."""
    assert order.calculate_pick_numbers(0, 12, 15) == []

"""Snake draft pick-number math, including Nth-round reversal.

A plain snake alternates direction every round. A "reversal round" (Sleeper's
``reversal_round`` setting, commonly 3 and known as 3RR) repeats the previous
round's direction once, then resumes alternating from there:

    reversal_round=0 (plain snake)   R1 ->  R2 <-  R3 ->  R4 <-  R5 ->
    reversal_round=3 (3RR)           R1 ->  R2 <-  R3 <-  R4 ->  R5 <-

Inserting that one repeat shifts the parity of every later round, which the
``_effective_round`` helper captures so a single formula covers both cases.
"""

from __future__ import annotations


def _effective_round(round_num: int, reversal_round: int = 0) -> int:
    """Map a real round onto the round whose direction it shares.

    Rounds at or after the reversal borrow the previous round's parity.
    """
    if reversal_round >= 2 and round_num >= reversal_round:
        return round_num - 1
    return round_num


def is_ascending(round_num: int, reversal_round: int = 0) -> bool:
    """True if `round_num` drafts from slot 1 upward."""
    return _effective_round(round_num, reversal_round) % 2 == 1


def pick_number(
    slot: int,
    round_num: int,
    total_teams: int,
    reversal_round: int = 0,
) -> int:
    """Overall pick number for a draft `slot` in a given round (all 1-indexed)."""
    if total_teams <= 0:
        return 0
    base = (round_num - 1) * total_teams
    if is_ascending(round_num, reversal_round):
        return base + slot
    return base + (total_teams - slot + 1)


def calculate_pick_numbers(
    slot: int,
    total_teams: int,
    total_rounds: int,
    reversal_round: int = 0,
) -> list[int]:
    """Every overall pick number belonging to a draft slot."""
    if slot <= 0 or total_teams <= 0:
        return []
    return [
        pick_number(slot, r, total_teams, reversal_round)
        for r in range(1, total_rounds + 1)
    ]


def round_of_pick(pick_no: int, total_teams: int) -> int:
    """Round containing an overall pick number."""
    if total_teams <= 0:
        return 1
    return (pick_no - 1) // total_teams + 1


def pick_in_round(pick_no: int, total_teams: int) -> int:
    """Sequential position of a pick within its round (1..total_teams).

    This is the number shown in a draft log - the 5th pick of round 2 is P05
    regardless of which slot owns it. Use `slot_at_pick` for the owning seat.
    """
    if total_teams <= 0:
        return 1
    return (pick_no - 1) % total_teams + 1


def slot_at_pick(pick_no: int, total_teams: int, reversal_round: int = 0) -> int:
    """Draft slot that owns an overall pick number.

    Inverse of `pick_number`: on a descending round the 1st pick of the round
    belongs to the last slot.
    """
    if total_teams <= 0:
        return 0
    position = pick_in_round(pick_no, total_teams)
    round_num = round_of_pick(pick_no, total_teams)
    if is_ascending(round_num, reversal_round):
        return position
    return total_teams - position + 1

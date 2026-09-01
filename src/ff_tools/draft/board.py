"""Track draft state, detect new picks, calculate best available."""

from __future__ import annotations

from dataclasses import dataclass, field

from ff_tools.draft import order
from ff_tools.draft.rankings import RankedPlayer
from ff_tools.models.draft import DraftPick

_FANTASY_POSITIONS = ("QB", "RB", "WR", "TE", "K", "DEF")


@dataclass
class DraftBoard:
    """Live draft board state tracker.

    Tracks which players have been picked, which are available,
    and computes recommendations for the user's next pick.

    Picks and rankings routinely come from different providers whose player IDs
    do not overlap, so a drafted player is matched against the rankings by ID
    *or* by normalized name. Matching on ID alone leaves drafted players sitting
    on the board forever.
    """

    draft_id: str
    total_rounds: int = 0
    total_teams: int = 0
    # 0 for a plain snake; 3 for a 3rd-round reversal.
    reversal_round: int = 0
    picks: list[DraftPick] = field(default_factory=list)
    taken_player_ids: set[str] = field(default_factory=set)
    taken_keys: set[str] = field(default_factory=set)
    ranked_players: list[RankedPlayer] = field(default_factory=list)
    # User info
    user_roster_id: int = 0
    user_draft_slot: int = 0
    user_picks: list[int] = field(default_factory=list)  # overall pick numbers
    # Picks that could not be tied to a ranked player, by either ID or name.
    unmatched_picks: list[DraftPick] = field(default_factory=list)

    def __post_init__(self) -> None:
        self._by_id: dict[str, RankedPlayer] = {}
        self._by_key: dict[str, RankedPlayer] = {}
        self.refresh_index()

    # ── Rankings index ──────────────────────────────────────────────

    def refresh_index(self) -> None:
        """Rebuild the ID and name lookups over `ranked_players`."""
        self._by_id = {}
        self._by_key = {}
        for rp in self.ranked_players:
            pid = rp.player.player_id
            if pid:
                self._by_id.setdefault(pid, rp)
            key = rp.match_key
            if key:
                self._by_key.setdefault(key, rp)

    def set_rankings(self, ranked: list[RankedPlayer]) -> None:
        """Replace the rankings list and rebuild the lookups."""
        self.ranked_players = ranked
        self.refresh_index()

    def resolve_pick(self, pick: DraftPick) -> RankedPlayer | None:
        """Find the ranked player a pick refers to.

        Tries the provider's own ID first (exact when picks and rankings share a
        source), then falls back to the normalized name key.
        """
        if not self._by_id and not self._by_key and self.ranked_players:
            self.refresh_index()

        if pick.player_id:
            found = self._by_id.get(pick.player_id)
            if found is not None:
                return found

        key = pick.match_key
        if key:
            return self._by_key.get(key)
        return None

    def player_name(self, pick: DraftPick) -> str:
        """Best available display name for a pick."""
        if pick.player_name:
            return pick.player_name
        resolved = self.resolve_pick(pick)
        if resolved is not None:
            return resolved.display_name
        return pick.player_id or "?"

    def position_of(self, pick: DraftPick) -> str:
        """Best available position for a pick.

        Sleeper and ESPN both include a position on the pick itself, which is
        more reliable than whatever the rankings provider said.
        """
        if pick.position:
            return pick.position
        resolved = self.resolve_pick(pick)
        if resolved is not None and resolved.position:
            return resolved.position
        return "UNK"

    def is_taken(self, rp: RankedPlayer) -> bool:
        """Whether a ranked player has already been drafted."""
        pid = rp.player.player_id
        if pid and pid in self.taken_player_ids:
            return True
        key = rp.match_key
        return bool(key) and key in self.taken_keys

    # ── Draft position ──────────────────────────────────────────────

    @property
    def current_pick_number(self) -> int:
        """The next overall pick number (1-indexed)."""
        if not self.picks:
            return 1
        return max(p.pick_no for p in self.picks) + 1

    @property
    def current_round(self) -> int:
        """The current round number."""
        return order.round_of_pick(self.current_pick_number, self.total_teams)

    @property
    def pick_in_round(self) -> int:
        """Sequential position of the next pick within its round."""
        return order.pick_in_round(self.current_pick_number, self.total_teams)

    @property
    def current_slot(self) -> int:
        """Draft slot that owns the next pick."""
        return order.slot_at_pick(
            self.current_pick_number, self.total_teams, self.reversal_round
        )

    @property
    def total_picks(self) -> int:
        """Total picks in the draft."""
        return self.total_rounds * self.total_teams

    @property
    def is_complete(self) -> bool:
        """Whether every pick has been made."""
        return self.total_picks > 0 and len(self.picks) >= self.total_picks

    @property
    def is_user_pick_next(self) -> bool:
        """Check if the next pick belongs to the user."""
        if not self.user_picks:
            return False
        return self.current_pick_number in self.user_picks

    def next_user_pick(self) -> int | None:
        """The user's next upcoming overall pick number."""
        current = self.current_pick_number
        for p in sorted(self.user_picks):
            if p >= current:
                return p
        return None

    def picks_until_user_turn(self) -> int:
        """How many picks until the user is on the clock."""
        nxt = self.next_user_pick()
        return (nxt - self.current_pick_number) if nxt else 0

    # ── State updates ───────────────────────────────────────────────

    def update_picks(self, new_picks: list[DraftPick]) -> list[DraftPick]:
        """Update board with new picks. Returns newly detected picks."""
        existing = {p.pick_no for p in self.picks}
        truly_new = [p for p in new_picks if p.pick_no not in existing]
        if not truly_new:
            return []

        self.picks = sorted(self.picks + truly_new, key=lambda p: p.pick_no)
        for p in truly_new:
            self._mark_taken(p)
        return truly_new

    def _mark_taken(self, pick: DraftPick) -> None:
        """Record a pick as removing a player from the available pool."""
        if pick.player_id:
            self.taken_player_ids.add(pick.player_id)

        # Register the pick's own name key and, when it resolves to a ranked
        # player, that player's key too - the two can differ if one source
        # spells a name in a way normalization does not fully reconcile.
        key = pick.match_key
        if key:
            self.taken_keys.add(key)

        resolved = self.resolve_pick(pick)
        if resolved is None:
            self.unmatched_picks.append(pick)
            return
        if resolved.match_key:
            self.taken_keys.add(resolved.match_key)
        if resolved.player.player_id:
            self.taken_player_ids.add(resolved.player.player_id)

    # ── Queries ─────────────────────────────────────────────────────

    def get_available(self) -> list[RankedPlayer]:
        """Get available players in rank order."""
        return [rp for rp in self.ranked_players if not self.is_taken(rp)]

    def get_best_available(self, count: int = 5) -> list[RankedPlayer]:
        """Get top N best available players."""
        return self.get_available()[:count]

    def get_taken_at_position(self) -> dict[str, int]:
        """Count drafted players by position."""
        counts: dict[str, int] = {}
        for pick in self.picks:
            pos = self.position_of(pick)
            counts[pos] = counts.get(pos, 0) + 1
        return counts

    def get_positional_scarcity(self) -> dict[str, dict]:
        """Show how many players are taken vs remaining at each position."""
        position_counts: dict[str, int] = {}
        for rp in self.ranked_players:
            pos = rp.position or "UNK"
            position_counts[pos] = position_counts.get(pos, 0) + 1

        taken_by_pos: dict[str, int] = {}
        for rp in self.ranked_players:
            if not self.is_taken(rp):
                continue
            pos = rp.position or "UNK"
            taken_by_pos[pos] = taken_by_pos.get(pos, 0) + 1

        scarcity = {}
        for pos in _FANTASY_POSITIONS:
            total = position_counts.get(pos, 0)
            taken = taken_by_pos.get(pos, 0)
            scarcity[pos] = {
                "total": total,
                "taken": taken,
                "remaining": total - taken,
                "pct_taken": (taken / total * 100) if total > 0 else 0,
            }
        return scarcity

    def get_picks_by_round(self) -> dict[int, list[DraftPick]]:
        """Organize picks by round for display."""
        rounds: dict[int, list[DraftPick]] = {}
        for pick in self.picks:
            r = pick.round or order.round_of_pick(pick.pick_no, self.total_teams)
            rounds.setdefault(r, []).append(pick)
        return rounds

    def get_user_picks_made(self) -> list[DraftPick]:
        """Every pick the user has made so far."""
        return [p for p in self.picks if self.is_user_pick(p)]

    def is_user_pick(self, pick: DraftPick) -> bool:
        """Whether a pick belongs to the user.

        Matches on roster ID when the platform provides one, and otherwise on
        the pick number, which is derived from the user's draft slot.
        """
        if self.user_roster_id and pick.roster_id:
            return pick.roster_id == self.user_roster_id
        return pick.pick_no in self.user_picks

    def get_roster_positions(self) -> dict[str, int]:
        """Count the user's drafted players by position."""
        positions: dict[str, int] = {}
        for pick in self.get_user_picks_made():
            pos = self.position_of(pick)
            positions[pos] = positions.get(pos, 0) + 1
        return positions

    def recommend_pick(self, count: int = 5) -> list[RankedPlayer]:
        """Recommend the best available picks for the user.

        The base board recommends by consensus rank alone; subclasses layer
        format-specific valuation on top.
        """
        return self.get_best_available(count)

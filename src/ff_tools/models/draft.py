from __future__ import annotations

from dataclasses import dataclass, field

from ff_tools.utils.names import match_key, normalize_team

# Sleeper reports starting-lineup slots as `slots_<name>` keys in draft
# settings. These map onto the position labels used throughout the package.
_SLEEPER_SLOT_NAMES = {
    "qb": "QB",
    "rb": "RB",
    "wr": "WR",
    "te": "TE",
    "k": "K",
    "def": "DEF",
    "flex": "FLEX",
    "wr_rb_flex": "FLEX",
    "wr_te_flex": "FLEX",
    "rb_te_flex": "FLEX",
    "super_flex": "SUPER_FLEX",
    "idp_flex": "IDP_FLEX",
    "bn": "BN",
}


@dataclass
class DraftPick:
    """A single draft pick."""

    draft_id: str = ""
    pick_no: int = 0
    round: int = 0
    roster_id: int = 0
    player_id: str = ""
    picked_by: str = ""
    # Derived from pick metadata or a player lookup
    player_name: str = ""
    position: str = ""
    team: str = ""
    draft_slot: int = 0

    @property
    def match_key(self) -> str:
        """Cross-source identity key, empty when the pick carries no name."""
        if not self.player_name:
            return ""
        return match_key(self.player_name, self.position, self.team)

    @classmethod
    def from_sleeper(cls, data: dict) -> DraftPick:
        """Create from Sleeper API draft pick data.

        Sleeper nests the player's name, position and team under `metadata`.
        Keeping them is what lets a pick be matched against a rankings list
        from a different provider, since the player IDs never line up.
        """
        meta = data.get("metadata")
        if not isinstance(meta, dict):
            meta = {}

        name = " ".join(
            p for p in (meta.get("first_name"), meta.get("last_name")) if p
        )

        return cls(
            draft_id=data.get("draft_id", ""),
            pick_no=data.get("pick_no", 0),
            round=data.get("round", 0),
            roster_id=data.get("roster_id") or 0,
            player_id=str(data.get("player_id", "")),
            picked_by=data.get("picked_by", ""),
            player_name=name,
            position=meta.get("position", ""),
            team=normalize_team(meta.get("team")),
            draft_slot=data.get("draft_slot") or 0,
        )

    @classmethod
    def from_espn(cls, data: dict) -> DraftPick:
        """Create from ESPN API draft pick data."""
        player = data.get("player") or {}
        return cls(
            pick_no=data.get("overallPickNumber", 0),
            round=data.get("roundId", 0),
            roster_id=data.get("teamId") or 0,
            player_id=str(player.get("id", "")),
            player_name=player.get("fullName", ""),
            position=(player.get("position") or {}).get("abbreviation", ""),
            team=normalize_team((player.get("proTeam") or {}).get("abbreviation")),
        )


@dataclass
class Draft:
    """A fantasy football draft."""

    draft_id: str
    league_id: str = ""
    status: str = ""
    draft_type: str = ""
    rounds: int = 0
    picks: list[DraftPick] = field(default_factory=list)
    # Draft seating. A roster ID is NOT a draft slot - Sleeper assigns them
    # independently, so slot 1 routinely belongs to some arbitrary roster.
    draft_order: dict[str, int] = field(default_factory=dict)
    slot_to_roster_id: dict[int, int] = field(default_factory=dict)
    # 0 means a plain snake; 3 is the common "3rd round reversal".
    reversal_round: int = 0
    total_teams: int = 0
    # Starting-lineup shape, e.g. {"QB": 1, "RB": 2, "WR": 2, "TE": 1, "FLEX": 2}
    roster_slots: dict[str, int] = field(default_factory=dict)

    def slot_for_roster(self, roster_id: int) -> int | None:
        """Find the draft slot a roster is sitting in."""
        for slot, rid in self.slot_to_roster_id.items():
            if rid == roster_id:
                return slot
        return None

    def slot_for_user(self, user_id: str) -> int | None:
        """Find the draft slot a user is sitting in."""
        return self.draft_order.get(str(user_id))

    @classmethod
    def from_sleeper(cls, data: dict) -> Draft:
        """Create from Sleeper API draft data."""
        settings = data.get("settings") or {}

        raw_order = data.get("draft_order")
        draft_order = (
            {str(k): int(v) for k, v in raw_order.items() if v is not None}
            if isinstance(raw_order, dict)
            else {}
        )

        raw_slots = data.get("slot_to_roster_id")
        slot_to_roster = (
            {int(k): int(v) for k, v in raw_slots.items() if v is not None}
            if isinstance(raw_slots, dict)
            else {}
        )

        return cls(
            draft_id=data.get("draft_id", ""),
            league_id=data.get("league_id", ""),
            status=data.get("status", ""),
            draft_type=data.get("type", ""),
            rounds=settings.get("rounds", 0),
            draft_order=draft_order,
            slot_to_roster_id=slot_to_roster,
            reversal_round=settings.get("reversal_round", 0) or 0,
            total_teams=settings.get("teams", 0) or 0,
            roster_slots=_sleeper_roster_slots(settings),
        )


def _sleeper_roster_slots(settings: dict) -> dict[str, int]:
    """Extract the starting lineup shape from Sleeper draft settings.

    Sleeper exposes one `slots_<name>` key per lineup slot type and simply omits
    the ones a league does not use - a league with no kicker has no `slots_k`.
    Several distinct flex types collapse onto FLEX, so counts are summed.
    """
    slots: dict[str, int] = {}
    for key, value in settings.items():
        if not key.startswith("slots_"):
            continue
        try:
            count = int(value)
        except (TypeError, ValueError):
            continue
        if count <= 0:
            continue
        label = _SLEEPER_SLOT_NAMES.get(key[len("slots_") :])
        if label:
            slots[label] = slots.get(label, 0) + count
    return slots

from __future__ import annotations

from dataclasses import dataclass, field

from ff_tools.utils.names import match_key, normalize_team

# ESPN `defaultPositionId` -> position. Note that ESPN also publishes lineup
# *slot* IDs (which include FLEX and IDP slots) from a separate enumeration;
# only the position IDs below belong here.
ESPN_POSITIONS = {
    1: "QB", 2: "RB", 3: "WR", 4: "TE", 5: "K", 16: "DEF",
}

# ESPN lineup *slot* IDs -> slot label. A separate enumeration from the position
# IDs above: slot 2 is RB here but WR there. Slots not listed are IDP or
# coach/punter slots that no standard league uses.
ESPN_LINEUP_SLOTS = {
    0: "QB",
    2: "RB",
    3: "FLEX",  # RB/WR
    4: "WR",
    5: "FLEX",  # WR/TE
    6: "TE",
    7: "SUPER_FLEX",  # ESPN calls this OP (offensive player)
    16: "DEF",
    17: "K",
    20: "BN",
    21: "IR",
    23: "FLEX",  # RB/WR/TE
}

# ESPN team ID mapping
ESPN_TEAMS = {
    1: "ATL", 2: "BUF", 3: "CHI", 4: "CIN", 5: "CLE", 6: "DAL",
    7: "DEN", 8: "DET", 9: "GB", 10: "TEN", 11: "IND", 12: "KC",
    13: "LV", 14: "LAR", 15: "MIA", 16: "MIN", 17: "NE", 18: "NO",
    19: "NYG", 20: "NYJ", 21: "PHI", 22: "ARI", 23: "PIT", 24: "LAC",
    25: "SF", 26: "SEA", 27: "TB", 28: "WAS", 29: "CAR", 30: "JAX",
    33: "BAL", 34: "HOU",
}

# ESPN stat block selectors. statSourceId 1 is projections (0 is actuals) and
# statSplitTypeId 0 with scoringPeriodId 0 is the full-season total.
_ESPN_STAT_PROJECTED = 1
_ESPN_SPLIT_SEASON = 0


@dataclass
class Player:
    """A fantasy football player."""

    player_id: str
    full_name: str
    first_name: str = ""
    last_name: str = ""
    position: str = ""
    team: str = ""
    age: int | None = None
    status: str = ""
    number: int | None = None
    # Sleeper-specific
    fantasy_positions: list[str] = field(default_factory=list)
    active: bool = True
    # ESPN-specific
    espn_id: int | None = None
    injury_status: str = ""
    projected_points: float = 0.0
    ownership_pct: float = 0.0
    # Auction dollar value - a draft-market price, not a points projection.
    auction_value: float = 0.0
    adp: float = 0.0
    eligible_slots: list[int] = field(default_factory=list)

    @property
    def match_key(self) -> str:
        """Cross-source identity key. See `ff_tools.utils.names.match_key`."""
        return match_key(self.full_name, self.position, self.team)

    @classmethod
    def from_sleeper(cls, data: dict) -> Player:
        """Create from Sleeper API player data."""
        # Team defenses carry no full_name on Sleeper; build one from the parts.
        full_name = data.get("full_name") or " ".join(
            p for p in (data.get("first_name"), data.get("last_name")) if p
        )
        return cls(
            player_id=str(data.get("player_id", "")),
            full_name=full_name,
            first_name=data.get("first_name", ""),
            last_name=data.get("last_name", ""),
            position=data.get("position") or "",
            team=normalize_team(data.get("team")),
            age=data.get("age"),
            status=data.get("status", ""),
            number=data.get("number"),
            fantasy_positions=data.get("fantasy_positions") or [],
            active=data.get("active", True),
        )

    @classmethod
    def from_espn(cls, data: dict, season: str | int | None = None) -> Player:
        """Create from ESPN API player data.

        Handles two shapes:
        - `players_wl`: flat, with defaultPositionId / proTeamId
        - `kona_player_info`: the same fields nested under a "player" key, plus
          projections, ownership and ADP
        """
        # Handle nested format (kona_player_info)
        if "player" in data and isinstance(data["player"], dict):
            pdata = data["player"]
        else:
            pdata = data

        pos_id = pdata.get("defaultPositionId", 0)
        team_id = pdata.get("proTeamId", 0)

        ownership = pdata.get("ownership")
        if not isinstance(ownership, dict):
            ownership = {}

        return cls(
            player_id=str(pdata.get("id", "")),
            full_name=pdata.get("fullName", ""),
            first_name=pdata.get("firstName", ""),
            last_name=pdata.get("lastName", ""),
            position=ESPN_POSITIONS.get(pos_id, f"POS_{pos_id}"),
            team=ESPN_TEAMS.get(team_id, f"T{team_id}"),
            age=pdata.get("age"),
            status=pdata.get("injuryStatus", ""),
            espn_id=pdata.get("id"),
            injury_status=pdata.get("injuryStatus", ""),
            active=pdata.get("active", True),
            projected_points=_espn_season_projection(pdata, season),
            ownership_pct=ownership.get("percentOwned", 0.0) or 0.0,
            auction_value=ownership.get("auctionValueAverage", 0.0) or 0.0,
            adp=ownership.get("averageDraftPosition", 0.0) or 0.0,
            eligible_slots=pdata.get("eligibleSlots") or [],
        )


def _espn_season_projection(
    pdata: dict, season: str | int | None = None
) -> float:
    """Pull the full-season projected points total out of an ESPN stat block.

    ESPN returns ~57 stat entries per player - weekly actuals, weekly
    projections and season totals for multiple seasons. The season projection is
    the entry with statSourceId=1, statSplitTypeId=0 and scoringPeriodId=0.

    When `season` is given, only that season counts; otherwise the latest
    projected season wins (so a preseason payload yields the upcoming year).
    """
    stats = pdata.get("stats")
    if not isinstance(stats, list):
        return 0.0

    want_season = int(season) if season is not None else None
    best_season = -1
    best_total = 0.0

    for entry in stats:
        if not isinstance(entry, dict):
            continue
        if entry.get("statSourceId") != _ESPN_STAT_PROJECTED:
            continue
        if entry.get("statSplitTypeId") != _ESPN_SPLIT_SEASON:
            continue
        if (entry.get("scoringPeriodId") or 0) != 0:
            continue

        entry_season = entry.get("seasonId")
        try:
            entry_season = int(entry_season)
        except (TypeError, ValueError):
            continue

        if want_season is not None:
            if entry_season == want_season:
                return float(entry.get("appliedTotal") or 0.0)
            continue

        if entry_season > best_season:
            best_season = entry_season
            best_total = float(entry.get("appliedTotal") or 0.0)

    return best_total

from __future__ import annotations

from dataclasses import dataclass, field

from ff_tools.utils.names import starting_slot_counts


@dataclass
class Roster:
    """A team's roster in a league."""

    roster_id: int
    owner_id: str = ""
    players: list[str] = field(default_factory=list)
    starters: list[str] = field(default_factory=list)
    wins: int = 0
    losses: int = 0
    ties: int = 0
    fpts: float = 0.0
    fpts_against: float = 0.0
    # Team metadata (from league users)
    display_name: str = ""
    team_name: str = ""

    @classmethod
    def from_sleeper(cls, data: dict) -> Roster:
        """Create from Sleeper API roster data."""
        return cls(
            roster_id=data.get("roster_id", 0),
            owner_id=data.get("owner_id", ""),
            players=data.get("players", []),
            starters=data.get("starters", []),
            wins=data.get("wins", 0),
            losses=data.get("losses", 0),
            ties=data.get("ties", 0),
            fpts=data.get("fpts", 0.0),
            fpts_against=data.get("fpts_against", 0.0),
        )


@dataclass
class Matchup:
    """A matchup between two teams."""

    matchup_id: int
    roster_id: int
    players: list[str] = field(default_factory=list)
    starters: list[str] = field(default_factory=list)
    points: float = 0.0
    starters_points: list[float] = field(default_factory=list)
    players_points: list[float] = field(default_factory=list)
    roster_id_2: int | None = None

    @classmethod
    def from_sleeper(cls, data: dict) -> Matchup:
        """Create from Sleeper API matchup data."""
        return cls(
            matchup_id=data.get("matchup_id", 0),
            roster_id=data.get("roster_id", 0),
            players=data.get("players", []),
            starters=data.get("starters", []),
            points=data.get("points", 0.0),
            starters_points=data.get("starters_points", []),
            players_points=data.get("players_points", []),
        )


@dataclass
class League:
    """A fantasy football league."""

    league_id: str
    name: str = ""
    status: str = ""
    sport: str = "nfl"
    season: str = ""
    total_rosters: int = 0
    scoring_settings: dict = field(default_factory=dict)
    roster_positions: list[str] = field(default_factory=list)
    # Sleeper-specific
    previous_league_id: str = ""
    draft_id: str = ""
    # ESPN-specific
    espn_id: int | None = None

    @property
    def roster_size(self) -> int:
        """Total roster spots per team, bench included."""
        return len(self.roster_positions)

    @property
    def starting_slots(self) -> dict[str, int]:
        """Starting-lineup shape, e.g. {"QB": 1, "RB": 2, "WR": 2, "FLEX": 1}.

        Bench spots are excluded - only slots that must be filled each week
        create demand on the player pool.
        """
        return starting_slot_counts(self.roster_positions)

    @classmethod
    def from_sleeper(cls, data: dict) -> League:
        """Create from Sleeper API league data."""
        return cls(
            league_id=data.get("league_id", ""),
            name=data.get("name", ""),
            status=data.get("status", ""),
            sport=data.get("sport", "nfl"),
            season=str(data.get("season", "")),
            total_rosters=data.get("total_rosters", 0),
            scoring_settings=data.get("scoring_settings", {}),
            roster_positions=data.get("roster_positions", []),
            previous_league_id=data.get("previous_league_id", ""),
            draft_id=data.get("draft_id", ""),
        )

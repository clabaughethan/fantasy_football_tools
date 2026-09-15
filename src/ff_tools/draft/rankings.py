"""Fetch and manage player rankings for draft recommendations."""

from __future__ import annotations

import csv
import json
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date

import requests

from ff_tools.models.player import Player
from ff_tools.utils.names import match_key, normalize_position, normalize_team


def default_season() -> int:
    """The fantasy season to request when the caller does not name one.

    Projections for season N are published from roughly March of year N, so
    before then the meaningful target is still the prior season.
    """
    today = date.today()
    return today.year if today.month >= 3 else today.year - 1

# FantasyPros publishes positions as a string ("RB") in the current payload but
# has used numeric IDs historically, so both are accepted.
_FP_POSITION_IDS = {
    1: "QB",
    2: "RB",
    3: "WR",
    4: "TE",
    5: "K",
    6: "DEF",
}


@dataclass
class RankedPlayer:
    """A player with a consensus rank."""

    rank: int
    player: Player
    projected_points: float = 0.0
    tier: int = 0
    adp: float = 0.0
    adp_round: float = 0.0  # ADP converted to round number
    times_drafted: int = 0  # how many times drafted in ADP pool
    # Auction dollar value - a market price, not a points projection.
    auction_value: float = 0.0
    # Rank within position, e.g. "RB3"
    pos_rank: str = ""
    bye_week: int = 0
    # Standard deviation of the expert ranks behind the consensus. High values
    # mean the experts disagree, which is a risk signal.
    rank_std: float = 0.0
    source: str = ""
    extra: dict = field(default_factory=dict)

    @property
    def display_name(self) -> str:
        return self.player.full_name

    @property
    def position(self) -> str:
        return self.player.position

    @property
    def team(self) -> str:
        return self.player.team

    @property
    def match_key(self) -> str:
        """Cross-source identity key. See `ff_tools.utils.names.match_key`."""
        return self.player.match_key

    @property
    def injury_status(self) -> str:
        """Game-day injury designation, empty when nothing is published."""
        return self.player.injury_status

    @property
    def injury_body_part(self) -> str:
        return self.player.injury_body_part


class RankingsSource:
    """Fetch public consensus rankings.

    Primary: FantasyPros Expert Consensus Rankings (ECR).
    Fallback: ESPN kona_player_info endpoint.
    Last resort: Sleeper player pool.
    """

    def __init__(self, espn_s2: str | None = None, swid: str | None = None) -> None:
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            )
        })
        self.espn_s2 = espn_s2
        self.swid = swid

    def fetch_best_available(
        self,
        season: str | int | None = None,
        limit: int = 500,
        verbose: bool = True,
    ) -> list[RankedPlayer]:
        """Fetch rankings from the best source that answers.

        Walks the chain this class documents: FantasyPros ECR, then ESPN
        projections, then the Sleeper player pool. Every caller used to inline
        its own version of this and they had drifted apart - two of the draft
        assistants skipped FantasyPros entirely, so the same league got graded
        against a real consensus board or against sorted projected points
        depending on which entry point you happened to run.

        The Sleeper fallback has neither ranks nor projections, so it is a way
        to stay running rather than a source of advice; that is why reaching it
        says so.
        """
        attempts = (
            ("FantasyPros ECR", lambda: self.fetch_fantasypros_rankings(limit=limit)),
            (
                "ESPN projections",
                lambda: self.fetch_espn_rankings(season=season, limit=limit),
            ),
        )

        for label, fetch in attempts:
            try:
                ranked = fetch()
            except Exception:
                continue
            if ranked:
                if verbose:
                    print(f"  Loaded {len(ranked)} players from {label}")
                return ranked

        ranked = self.fetch_sleeper_rankings(limit=limit)
        if verbose:
            print(
                f"  WARNING: fell back to the Sleeper player pool "
                f"({len(ranked)} players). It carries no consensus rank and no "
                f"projections, so recommendations will be close to arbitrary."
            )
        return ranked

    def fetch_fantasypros_rankings(
        self, scoring: str = "half-point-ppr", limit: int = 500
    ) -> list[RankedPlayer]:
        """Fetch Expert Consensus Rankings from FantasyPros.

        Uses the ecrData embedded in the cheatsheets page.
        This is a consensus of 100+ expert rankings, updated weekly.
        """
        url = f"https://www.fantasypros.com/nfl/rankings/{scoring}-cheatsheets.php"
        resp = self.session.get(url, timeout=15)
        resp.raise_for_status()

        # Extract ecrData from script tags
        ecr_match = re.search(
            r"var\s+ecrData\s*=\s*(\{.*?\});", resp.text, re.DOTALL
        )
        if not ecr_match:
            raise ValueError("Could not find FantasyPros ECR data in page")

        ecr = json.loads(ecr_match.group(1))
        players = ecr.get("players", [])
        if not players:
            raise ValueError("FantasyPros ECR data contained no players")

        ranked = []
        for pdata in players[:limit]:
            player = Player(
                player_id=str(pdata.get("player_id", "")),
                full_name=pdata.get("player_name", ""),
                position=_fp_position(pdata),
                team=normalize_team(pdata.get("player_team_id")),
            )

            rank = _as_int(pdata.get("rank_ecr"), len(ranked) + 1)
            # FantasyPros publishes real expert tiers; only fall back to the
            # rank-bucket approximation when the payload omits them.
            tier = _as_int(pdata.get("tier"), 0) or _tier_from_rank(rank)

            ranked.append(
                RankedPlayer(
                    rank=rank,
                    player=player,
                    tier=tier,
                    pos_rank=str(pdata.get("pos_rank") or ""),
                    bye_week=_as_int(pdata.get("player_bye_week"), 0),
                    rank_std=_as_float(pdata.get("rank_std"), 0.0),
                    source="fantasypros",
                )
            )

        _warn_if_positions_missing(ranked, "FantasyPros")
        return ranked

    def fetch_fantasycalculator_adp(
        self, scoring: str = "ppr", limit: int = 300, teams: int = 12
    ) -> list[RankedPlayer]:
        """Fetch ADP data from Fantasy Football Calculator.

        Free API, no auth required. Returns players sorted by ADP.
        Attribution required: https://fantasyfootballcalculator.com

        FantasyFootballCalculator only serves 8/10/12/14-team ADP. For deeper
        leagues the request falls back to the largest available size (14) and
        records which one answered in each player's `source` field, readable
        back with `adp_reference_teams`. 32-team ADP does not exist on any free
        public API because the format is too rare.

        A fallback ADP is still directly comparable to a pick number, because
        ADP counts players off the board rather than rounds: "ADP 45" means the
        45th player taken in either format. What it is not is unbiased - a
        14-team league needs 14 starting quarterbacks and a 32-team league needs
        32, so positional demand differs and some positions genuinely go earlier
        in a deep league than the reference board says. Treat a fallback ADP as
        a directional read, which is why the reference size is preserved rather
        than quietly folded into a bare number.
        """
        url = f"https://fantasyfootballcalculator.com/api/v1/adp/{scoring}"
        # Try the requested size first; fall back through the available sizes.
        for try_teams in (teams, 14, 12, 10, 8):
            resp = self.session.get(url, params={"teams": try_teams}, timeout=15)
            if resp.status_code == 200:
                break
        else:
            resp.raise_for_status()

        data = resp.json()
        adp_teams = try_teams
        label = f"fantasyfootballcalculator-{adp_teams}t"

        ranked = []
        for i, pdata in enumerate(data.get("players", [])[:limit], 1):
            player = Player(
                player_id=str(pdata.get("player_id", "")),
                full_name=pdata.get("name", ""),
                position=normalize_position(pdata.get("position")),
                team=normalize_team(pdata.get("team")),
            )
            adp = _as_float(pdata.get("adp"), 0.0)
            # Rounds are relative to the league being drafted, not to whichever
            # board supplied the ADP. The ordinal transfers between formats; the
            # round it lands in does not, so dividing by the fallback size would
            # report round 4 of 14 for a pick that is round 2 of the user's 32.
            adp_round = ((adp - 1) // teams + 1) if adp > 0 and teams > 0 else 0

            ranked.append(
                RankedPlayer(
                    rank=i,
                    player=player,
                    adp=adp,
                    adp_round=adp_round,
                    times_drafted=_as_int(pdata.get("times_drafted"), 0),
                    tier=_tier_from_rank(i),
                    source=label,
                )
            )
        return ranked

    @staticmethod
    def adp_reference_teams(source: str) -> int | None:
        """Read the league size an ADP figure came from out of its source label.

        `fetch_fantasycalculator_adp` tags results `fantasyfootballcalculator-14t`
        when it falls back, so consumers can say which board a number describes.
        Returns None for a label that carries no size, including ADP loaded from
        a user CSV, where the provenance is the user's own business.
        """
        if not source:
            return None
        tail = source.rsplit("-", 1)[-1]
        if tail.endswith("t") and tail[:-1].isdigit():
            return int(tail[:-1])
        return None

    def merge_rankings_with_adp(
        self,
        rankings: list[RankedPlayer],
        adp: list[RankedPlayer],
    ) -> list[RankedPlayer]:
        """Merge ADP data into a rankings list.

        Ranking order comes from the first list (ECR); ADP fields are copied
        across from the second. Matching goes through the normalized name key,
        since the two providers use unrelated player IDs.
        """
        adp_by_key: dict[str, RankedPlayer] = {}
        for rp in adp:
            key = rp.match_key
            if key:
                adp_by_key.setdefault(key, rp)

        for rp in rankings:
            source = adp_by_key.get(rp.match_key)
            if source is None:
                continue
            rp.adp = source.adp
            rp.adp_round = source.adp_round
            rp.times_drafted = source.times_drafted
            # ADP feeds sometimes carry a position the ECR payload lacked.
            if not rp.player.position and source.player.position:
                rp.player.position = source.player.position
            if not rp.player.team and source.player.team:
                rp.player.team = source.player.team

        return rankings

    def merge_injury_status(
        self,
        rankings: list[RankedPlayer],
        players: Iterable[Player],
    ) -> int:
        """Copy injury designations from a player pool onto a rankings list.

        Consensus rankings carry no injury data - the FantasyPros ECR payload has
        no such field - so availability has to come from a roster source. Sleeper
        publishes `injury_status` and `injury_body_part` for the whole league,
        which is what `SleeperClient.get_players()` returns.

        `players` is taken as an iterable of `Player` rather than fetched here so
        the caller's already-cached pool is reused; the full Sleeper player list
        is ~14MB and no draft needs to download it twice.

        Returns the number of players that were flagged, so a caller can tell a
        clean league from a merge that silently matched nothing.
        """
        pool = self._index_pool_for_injuries(players)

        flagged = 0
        for rp in rankings:
            key = rp.match_key
            if not key:
                continue
            # Position-qualified first: name-only keys collide across positions,
            # and a namesake's injury is worse than no injury data at all.
            source = pool.get((key, rp.position)) or pool.get((key, ""))
            if source is None:
                continue
            rp.player.injury_status = source.injury_status
            rp.player.injury_body_part = source.injury_body_part
            # Roster standing is a second availability signal (IR, PUP) that
            # moves independently of the game-day designation.
            if source.status:
                rp.player.status = source.status
            if source.injury_status:
                flagged += 1

        return flagged

    @staticmethod
    def _index_pool_for_injuries(
        players: Iterable[Player],
    ) -> dict[tuple[str, str], Player]:
        """Index a player pool by match key, both with and without position.

        Sleeper's pool holds roughly 4,000 players at fantasy positions, many of
        them practice-squad namesakes of real starters. Where two share a name,
        an active player on a real team wins, because attaching a fringe player's
        injury to a first-round pick is the failure mode that matters here.
        """
        index: dict[tuple[str, str], Player] = {}
        for p in players:
            key = p.match_key
            if not key:
                continue
            for variant in ((key, p.position), (key, "")):
                held = index.get(variant)
                if held is None or _pool_priority(p) > _pool_priority(held):
                    index[variant] = p
        return index

    def fetch_espn_rankings(
        self, season: str | int | None = None, limit: int = 300
    ) -> list[RankedPlayer]:
        """Fetch rankings from ESPN's player pool.

        Uses the kona_player_info view, which returns players sorted by
        ownership together with season projections, ADP and auction values.

        `season` defaults to the current fantasy season rather than a literal
        year, so the tool does not quietly start requesting a past season.
        """
        if season is None:
            season = default_season()
        ppr_id = 1  # 0=standard, 1=half-PPR, 2=PPR
        url = (
            f"https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl"
            f"/seasons/{season}/segments/0/leaguedefaults/{ppr_id}"
        )
        params = {"view": "kona_player_info"}
        filter_obj = {
            "players": {
                "limit": limit,
                "sortPercOwned": {"sortPriority": 4, "sortAsc": False},
            }
        }
        headers = {"X-Fantasy-Filter": json.dumps(filter_obj)}

        cookies = {}
        if self.espn_s2:
            cookies["espn_s2"] = self.espn_s2
        if self.swid:
            cookies["SWID"] = self.swid

        resp = self.session.get(
            url, params=params, headers=headers, cookies=cookies, timeout=15
        )
        resp.raise_for_status()
        data = resp.json()

        entries = data if isinstance(data, list) else data.get("players", [])
        players = [Player.from_espn(entry, season=season) for entry in entries]

        # ESPN returns the pool ordered by ownership, which is a popularity
        # signal rather than a ranking. Rank by projected points instead, and
        # only fall back to the response order if no projections came through.
        if any(p.projected_points > 0 for p in players):
            players.sort(key=lambda p: p.projected_points, reverse=True)

        ranked = []
        for i, player in enumerate(players, 1):
            ranked.append(
                RankedPlayer(
                    rank=i,
                    player=player,
                    projected_points=player.projected_points,
                    auction_value=player.auction_value,
                    adp=player.adp,
                    tier=_tier_from_rank(i),
                    source="espn",
                )
            )
        return ranked

    def fetch_sleeper_rankings(
        self, limit: int = 300, position: str | None = None
    ) -> list[RankedPlayer]:
        """Fetch the Sleeper player pool as a last-resort rankings stand-in.

        Sleeper publishes no projections or consensus ranks, so this orders by
        `search_rank` - Sleeper's own popularity ordering - which is a rough
        proxy at best. Prefer FantasyPros or ESPN whenever they are reachable.
        """
        url = "https://api.sleeper.app/v1/players/nfl"
        params = {}
        if position:
            params["position"] = position
            params["active"] = "true"

        resp = self.session.get(url, params=params, timeout=30)
        resp.raise_for_status()
        raw = resp.json()

        fantasy_positions = {"QB", "RB", "WR", "TE", "K", "DEF"}
        scored: list[tuple[int, Player]] = []
        for pid, pdata in raw.items():
            if not pdata.get("active", True):
                continue
            pdata["player_id"] = pid
            player = Player.from_sleeper(pdata)
            if player.position not in fantasy_positions:
                continue
            if not player.full_name:
                continue
            # search_rank is Sleeper's popularity order; unranked players get a
            # sentinel so they sort to the back rather than to the front.
            search_rank = _as_int(pdata.get("search_rank"), 999999) or 999999
            scored.append((search_rank, player))

        scored.sort(key=lambda item: (item[0], item[1].full_name))

        ranked = []
        for i, (_, player) in enumerate(scored[:limit], 1):
            ranked.append(
                RankedPlayer(
                    rank=i,
                    player=player,
                    tier=_tier_from_rank(i),
                    source="sleeper",
                )
            )
        return ranked

    def load_csv(self, filepath: str) -> list[RankedPlayer]:
        """Load rankings from a CSV file.

        Expected columns: rank, name, position, team, projected_points (optional)
        """
        ranked = []
        with open(filepath, newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                player = Player(
                    player_id="",
                    full_name=row.get("name", row.get("player", "")),
                    position=normalize_position(row.get("position")),
                    team=normalize_team(row.get("team")),
                )
                rank = _as_int(row.get("rank"), len(ranked) + 1)
                ranked.append(
                    RankedPlayer(
                        rank=rank,
                        player=player,
                        projected_points=_as_float(row.get("projected_points"), 0.0),
                        tier=_as_int(row.get("tier"), 0) or _tier_from_rank(rank),
                        adp=_as_float(row.get("adp"), 0.0),
                        source="csv",
                    )
                )

        _warn_if_positions_missing(ranked, filepath)
        return ranked


def _pool_priority(player: Player) -> tuple[int, int]:
    """Rank a pool entry's claim to a name, best last.

    Active beats inactive and a rostered team beats a free agent, so a fringe
    namesake cannot outrank the starter whose name the rankings list means.
    """
    return (1 if player.active else 0, 1 if player.team else 0)


def _fp_position(pdata: dict) -> str:
    """Resolve a position from a FantasyPros player record.

    `player_position_id` is a string ("RB") in the current payload but was an
    integer ID historically, and `player_positions` is a fallback on some pages.
    """
    raw = pdata.get("player_position_id")
    if isinstance(raw, int):
        return _FP_POSITION_IDS.get(raw, "")
    if isinstance(raw, str) and raw.strip():
        # A numeric string is still a legacy ID.
        if raw.strip().isdigit():
            return _FP_POSITION_IDS.get(int(raw), "")
        return normalize_position(raw)
    return normalize_position(pdata.get("player_positions"))


def _warn_if_positions_missing(ranked: list[RankedPlayer], source: str) -> None:
    """Print a warning when a rankings source yielded no positions.

    Every positional calculation downstream (scarcity, roster need, position
    multipliers) silently degrades to defaults without positions, so this is
    worth surfacing loudly rather than debugging through bad recommendations.
    """
    if not ranked:
        return
    missing = sum(1 for rp in ranked if not rp.position)
    if missing > len(ranked) // 2:
        print(
            f"  WARNING: {missing}/{len(ranked)} players from {source} have no "
            "position. Positional analysis will be unreliable."
        )


def _as_int(value: object, default: int) -> int:
    """Coerce a value to int, falling back to `default`."""
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def _as_float(value: object, default: float) -> float:
    """Coerce a value to float, falling back to `default`."""
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def _tier_from_rank(rank: int) -> int:
    """Assign a tier based on rank."""
    if rank <= 12:
        return 1
    if rank <= 24:
        return 2
    if rank <= 48:
        return 3
    if rank <= 72:
        return 4
    if rank <= 120:
        return 5
    return 6


def index_by_key(ranked: list[RankedPlayer]) -> dict[str, RankedPlayer]:
    """Index a rankings list by cross-source match key.

    Later duplicates are discarded so the best-ranked entry wins.
    """
    index: dict[str, RankedPlayer] = {}
    for rp in ranked:
        key = rp.match_key
        if key:
            index.setdefault(key, rp)
    return index


def index_by_id(ranked: list[RankedPlayer]) -> dict[str, RankedPlayer]:
    """Index a rankings list by the source's own player ID."""
    index: dict[str, RankedPlayer] = {}
    for rp in ranked:
        pid = rp.player.player_id
        if pid:
            index.setdefault(pid, rp)
    return index


__all__ = [
    "RankedPlayer",
    "RankingsSource",
    "index_by_id",
    "index_by_key",
    "match_key",
]

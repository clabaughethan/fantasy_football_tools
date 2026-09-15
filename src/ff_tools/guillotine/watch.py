"""Monday-night elimination watch for guillotine leagues.

In a guillotine league only the lowest-scoring team each week is cut, and its
entire roster hits the waiver pool. Once most games are final, the bottom of
the live leaderboard is a strong preview of who that will be - so this module
ranks the at-risk teams, flags starters who may still play (Monday night),
and lists the prize assets on each candidate roster for FAAB prep.

Starters sitting on exactly 0.00 are marked as *possibly pending*: they may
not have played yet, or they may have simply scored nothing. The output says
which - it cannot tell the difference from matchup data alone.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ff_tools.draft.rankings import RankedPlayer
from ff_tools.models.league import Matchup, Roster
from ff_tools.models.player import Player


@dataclass
class StarterLine:
    """One starter's live score, with pending/injury flags for 0.00 scores."""

    name: str
    position: str
    team: str
    points: float
    pending: bool = False
    injury_status: str = ""
    injury_body_part: str = ""


@dataclass
class AssetLine:
    """One player on an at-risk roster, ordered by consensus rank."""

    rank: int | None
    name: str
    position: str
    team: str
    is_starter: bool
    plan_cap: str
    injury_status: str = ""
    injury_body_part: str = ""


@dataclass
class TeamWatch:
    """Elimination-watch snapshot for a single roster."""

    roster_id: int
    owner: str
    points: float
    margin_to_safety: float
    likely_out: bool
    starters: list[StarterLine] = field(default_factory=list)
    top_assets: list[AssetLine] = field(default_factory=list)


# Designations that mean the player is not taking the field. A 0.00 with one
# of these is not Monday-night upside - it is a dead slot. "Inactive" is
# Sleeper's roster-standing counterpart (used for IR players).
NON_PLAYING_DESIGNATIONS = frozenset({"out", "ir", "pup", "suspended", "inactive"})


def is_ruled_out(injury_status: str, roster_status: str = "") -> bool:
    """True when the designations say the player will not play."""
    return (
        (injury_status or "").strip().lower() in NON_PLAYING_DESIGNATIONS
        or (roster_status or "").strip().lower() == "inactive"
    )


def plan_cap_for_rank(rank: int | None, injury_status: str = "") -> str:
    """Rough max-bid planning cap from consensus rank.

    These are prep numbers, not bids - finalize once the cut is official.
    Ruled-out players are never prizes in a win-now guillotine week.
    """
    if is_ruled_out(injury_status):
        return f"{injury_status or 'OUT'} - skip"
    if rank is None:
        return "depth (~5%)"
    if rank <= 12:
        return "ELITE (~50%)"
    if rank <= 36:
        return "high (~35%)"
    if rank <= 75:
        return "mid (~25%)"
    if rank <= 150:
        return "low (~15%)"
    return "depth (~8%)"


def build_watch(
    matchups: list[Matchup],
    rosters: list[Roster],
    users: dict[str, str],
    players: dict[str, Player],
    rankings: list[RankedPlayer] | None = None,
    bottom_n: int = 8,
    user_roster_id: int | None = None,
    cuts: int = 1,
) -> list[TeamWatch]:
    """Build the elimination watch for the bottom_n teams by live score.

    Safety line is the first safe score: with `cuts` teams chopped per week,
    that is the (cuts+1)-th lowest score. Everyone below it is in danger and
    everyone above it is safe unless pending (Monday) players move the board.
    """
    if not matchups:
        return []

    ordered = sorted(matchups, key=lambda m: m.points)
    cuts = max(1, cuts)
    safety_line = ordered[cuts].points if len(ordered) > cuts else ordered[-1].points

    rosters_by_id = {r.roster_id: r for r in rosters}
    rank_by_key: dict[str, int] = {}
    if rankings:
        for rp in rankings:
            rank_by_key.setdefault(rp.match_key, rp.rank)

    watch: list[TeamWatch] = []
    for i, m in enumerate(ordered[:bottom_n]):
        roster = rosters_by_id.get(m.roster_id)
        owner = ""
        if roster and roster.owner_id:
            owner = users.get(roster.owner_id, "")
        if roster and user_roster_id == roster.roster_id and owner:
            owner = f"{owner} (YOU)"

        starters: list[StarterLine] = []
        starter_ids = set(m.starters or [])
        for sid, pts in zip(m.starters or [], m.starters_points or []):
            p = players.get(sid)
            name = p.full_name if p else str(sid)
            pos = p.position if p else "?"
            team = p.team if p else "?"
            inj = p.injury_status if p else ""
            body = p.injury_body_part if p else ""
            # A 0.00 is only "maybe still to play" when nothing rules him out.
            pending = pts == 0.0 and not is_ruled_out(
                inj, p.status if p else ""
            )
            starters.append(
                StarterLine(
                    name=name,
                    position=pos,
                    team=team,
                    points=pts,
                    pending=pending,
                    injury_status=inj,
                    injury_body_part=body,
                )
            )

        assets: list[AssetLine] = []
        if roster and roster.players:
            for pid in roster.players:
                p = players.get(pid)
                if not p:
                    continue
                rank = rank_by_key.get(p.match_key)
                assets.append(
                    AssetLine(
                        rank=rank,
                        name=p.full_name,
                        position=p.position,
                        team=p.team,
                        is_starter=pid in starter_ids,
                        plan_cap=plan_cap_for_rank(rank, p.injury_status),
                        injury_status=p.injury_status,
                        injury_body_part=p.injury_body_part,
                    )
                )
            assets.sort(key=lambda a: (a.rank is None, a.rank or 0))
            assets = assets[:6]

        watch.append(
            TeamWatch(
                roster_id=m.roster_id,
                owner=owner,
                points=m.points,
                margin_to_safety=m.points - safety_line,
                likely_out=(i < cuts),
                starters=starters,
                top_assets=assets,
            )
        )
    return watch


def print_watch(watch: list[TeamWatch], week: int, cuts: int = 1) -> None:
    """Print a human-readable elimination-watch report."""
    print()
    print("=" * 70)
    print(f"  GUILLOTINE ELIMINATION WATCH - Week {week}")
    print("=" * 70)
    print()
    cut_word = f"Bottom {cuts} cut" if cuts > 1 else "Only the lowest score is cut"
    print(f"  {cut_word}. Margin is vs the first safe score (safety line).")
    print("  '*' = 0.00 and could still play Monday.")
    print("  [OUT ...] / [IR ...] = ruled out, not pending - plan around them.")
    print()

    for t in watch:
        flag = "LIKELY OUT" if t.likely_out else f"margin {t.margin_to_safety:+.1f}"
        head = f"Roster {t.roster_id}"
        if t.owner:
            head += f" ({t.owner})"
        print(f"  {head}: {t.points:.1f} pts | {flag}")
        print("  " + "-" * 62)
        print("    Starters:")
        for s in t.starters:
            if is_ruled_out(s.injury_status):
                mark = f"[{s.injury_status.upper()}"
                if s.injury_body_part:
                    mark += f" {s.injury_body_part}"
                mark += "]"
                print(
                    f"    {mark:16} {s.position:3} {s.name:25} "
                    f"{s.team:4} {s.points:6.2f}"
                )
            else:
                mark = "*" if s.pending else " "
                extra = ""
                if s.injury_status:
                    extra = f" [{s.injury_status}]"
                print(
                    f"    {mark} {s.position:3} {s.name:25} {s.team:4} "
                    f"{s.points:6.2f}{extra}"
                )
        if t.top_assets:
            print("    Prize assets if cut (plan caps, not bids):")
            for a in t.top_assets:
                rk = str(a.rank) if a.rank is not None else "UR"
                role = "S" if a.is_starter else "b"
                extra = f" [{a.injury_status}]" if a.injury_status else ""
                print(
                    f"      [{rk:>3}] {a.position:3} {a.name:25} "
                    f"{a.team:4} ({role}) {a.plan_cap}{extra}"
                )
        print()
    print("=" * 70)
    print()

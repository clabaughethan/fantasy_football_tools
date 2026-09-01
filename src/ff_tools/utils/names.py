"""Cross-source player identity keys.

Sleeper, ESPN, FantasyPros and FantasyFootballCalculator each mint their own
player IDs, and none of them overlap - Jahmyr Gibbs is 9221 on Sleeper, 22968
on FantasyPros and 5672 on FFCalculator. Matching a drafted player against a
rankings list therefore has to go through the name.

`match_key` produces a stable key that survives the formatting differences
between sources (suffixes, punctuation, accents, team-defense naming).
"""

from __future__ import annotations

import re
import unicodedata

# Generational suffixes are inconsistently included across sources:
# "Marvin Harrison Jr." on FantasyPros vs "Marvin Harrison" elsewhere.
_SUFFIXES = frozenset({"jr", "sr", "ii", "iii", "iv", "v"})

_NON_ALNUM = re.compile(r"[^a-z0-9 ]+")
_WHITESPACE = re.compile(r"\s+")

# Positions that identify a whole team rather than a person. Sleeper uses the
# team abbreviation as the player ID; everyone else spells out the franchise.
DEFENSE_POSITIONS = frozenset({"DEF", "DST", "D/ST"})

# Franchise nickname -> team abbreviation, for resolving team defenses when the
# source gives us a name ("Philadelphia Eagles") but no team code.
_NICKNAME_TO_TEAM = {
    "cardinals": "ARI", "falcons": "ATL", "ravens": "BAL", "bills": "BUF",
    "panthers": "CAR", "bears": "CHI", "bengals": "CIN", "browns": "CLE",
    "cowboys": "DAL", "broncos": "DEN", "lions": "DET", "packers": "GB",
    "texans": "HOU", "colts": "IND", "jaguars": "JAX", "chiefs": "KC",
    "raiders": "LV", "chargers": "LAC", "rams": "LAR", "dolphins": "MIA",
    "vikings": "MIN", "patriots": "NE", "saints": "NO", "giants": "NYG",
    "jets": "NYJ", "eagles": "PHI", "steelers": "PIT", "49ers": "SF",
    "seahawks": "SEA", "buccaneers": "TB", "titans": "TEN", "commanders": "WAS",
}

# Team abbreviations that differ between sources, normalized to the Sleeper form.
TEAM_ALIASES = {
    "JAC": "JAX", "WSH": "WAS", "LA": "LAR", "STL": "LAR", "SD": "LAC",
    "OAK": "LV", "LVR": "LV", "GNB": "GB", "KAN": "KC", "NWE": "NE",
    "NOR": "NO", "SFO": "SF", "TAM": "TB", "ARZ": "ARI", "BLT": "BAL",
    "CLV": "CLE", "HST": "HOU", "TB ": "TB",
}


# Position labels that vary by source, normalized to the Sleeper vocabulary.
POSITION_ALIASES = {
    "DST": "DEF", "D/ST": "DEF", "DS": "DEF", "D": "DEF",
    "PK": "K", "KICKER": "K",
    "FB": "RB",
}

# Lineup-slot labels, normalized to the vocabulary the strategy code uses.
# Several distinct flex types collapse onto FLEX because they are all filled
# from the RB/WR/TE pool; SUPER_FLEX stays separate because it also takes a QB.
LINEUP_SLOT_ALIASES = {
    "QB": "QB", "RB": "RB", "WR": "WR", "TE": "TE", "K": "K",
    "DEF": "DEF", "DST": "DEF", "D/ST": "DEF",
    "FLEX": "FLEX",
    "WRRB_FLEX": "FLEX",
    "WR_RB_FLEX": "FLEX",
    "WR_TE_FLEX": "FLEX",
    "RB_TE_FLEX": "FLEX",
    "REC_FLEX": "FLEX",
    "SUPER_FLEX": "SUPER_FLEX",
    "SUPERFLEX": "SUPER_FLEX",
    "QB_WR_RB_TE": "SUPER_FLEX",
    "IDP_FLEX": "IDP_FLEX",
    "BN": "BN",
    "BE": "BN",
}

# Slots that hold no active starter and so create no demand on the player pool.
NON_STARTING_SLOTS = frozenset({"BN", "IR", "TAXI", "RES", "NA"})


def normalize_lineup_slot(slot: str | None) -> str:
    """Normalize a lineup-slot label, returning "" for unrecognized slots."""
    if not slot:
        return ""
    raw = slot.strip().upper()
    if raw in NON_STARTING_SLOTS:
        return raw if raw == "BN" else ""
    return LINEUP_SLOT_ALIASES.get(raw, "")


def starting_slot_counts(roster_positions: list[str] | None) -> dict[str, int]:
    """Count starting-lineup slots from a Sleeper-style roster_positions list.

    Bench, IR and taxi entries are excluded, so the result is the shape of the
    lineup that has to be filled each week - which is what drives positional
    demand and scarcity.
    """
    counts: dict[str, int] = {}
    for raw in roster_positions or []:
        label = normalize_lineup_slot(raw)
        if not label or label == "BN":
            continue
        counts[label] = counts.get(label, 0) + 1
    return counts


def normalize_team(team: str | None) -> str:
    """Normalize a team abbreviation to its canonical form."""
    if not team:
        return ""
    code = team.strip().upper()
    return TEAM_ALIASES.get(code, code)


def normalize_position(position: str | None) -> str:
    """Normalize a position label to the Sleeper vocabulary (QB/RB/WR/TE/K/DEF)."""
    if not position:
        return ""
    pos = position.strip().upper()
    return POSITION_ALIASES.get(pos, pos)


def normalize_name(name: str | None) -> str:
    """Reduce a player name to a comparable form.

    Strips accents, punctuation and generational suffixes, then lowercases and
    collapses whitespace:

        "Ja'Marr Chase"        -> "jamarr chase"
        "Marvin Harrison Jr."  -> "marvin harrison"
        "D.J. Moore"           -> "dj moore"
        "Kenneth Walker III"    -> "kenneth walker"
    """
    if not name:
        return ""

    # Decompose accents ("Kadarius Toney" vs "Kadarius Toné") and drop the marks.
    decomposed = unicodedata.normalize("NFKD", name)
    ascii_only = "".join(c for c in decomposed if not unicodedata.combining(c))

    cleaned = _NON_ALNUM.sub("", ascii_only.lower().replace("-", " ").replace(".", ""))
    cleaned = _WHITESPACE.sub(" ", cleaned).strip()

    tokens = cleaned.split()
    # Only drop a trailing suffix if a real name remains underneath it.
    while len(tokens) > 2 and tokens[-1] in _SUFFIXES:
        tokens.pop()
    return " ".join(tokens)


def match_key(
    name: str | None,
    position: str | None = None,
    team: str | None = None,
) -> str:
    """Build a cross-source match key for a player.

    Team defenses collapse to ``def:<TEAM>`` because their names vary wildly
    between sources ("PHI", "Philadelphia Eagles", "Eagles D/ST"). Everyone
    else keys on the normalized name.
    """
    pos = (position or "").strip().upper()

    if pos in DEFENSE_POSITIONS:
        code = normalize_team(team)
        if not code:
            code = _team_from_defense_name(name)
        return f"def:{code}" if code else f"def:{normalize_name(name)}"

    return normalize_name(name)


def _team_from_defense_name(name: str | None) -> str:
    """Resolve a team abbreviation from a defense's display name."""
    normalized = normalize_name(name)
    if not normalized:
        return ""

    # A bare abbreviation ("PHI") is already the answer.
    if " " not in normalized and len(normalized) <= 3:
        return normalized.upper()

    for token in reversed(normalized.split()):
        if token in _NICKNAME_TO_TEAM:
            return _NICKNAME_TO_TEAM[token]
    return ""

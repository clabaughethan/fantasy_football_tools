"""Agent-supplied injury assessments.

Sleeper publishes a designation and a body part, and neither carries much
information. "Questionable" covers a precautionary Wednesday rest day and a knee
that will not be right until October, and `injury_body_part` is "Undisclosed" for
roughly half the flagged players. A model reading news can judge those cases; a
lookup table keyed on two short strings cannot.

So this module is the seam. `write_assessment_template` emits the questions - one
entry per flagged player, with the raw Sleeper fields for context - and
`load_assessments` reads the answers back in. The scorer prefers an assessment
where one exists and falls back to the designation heuristic where it does not,
so a partly-filled file is useful rather than all-or-nothing.

Two numbers are asked for, because they are different questions:

    play_probability  - will he be on the field at all
    effectiveness     - if he plays, how close to full is he

The second is the one the designation cannot express, and in a guillotine league
it matters: a running back who plays through a knee at 70% is a different
proposition from one who is simply held out.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date

from ff_tools.draft.rankings import RankedPlayer
from ff_tools.utils.names import match_key

# An assessment older than this is reported as stale. Injury news moves daily in
# the week before a game, so a week-old read is a guess wearing a number's
# clothes.
STALE_AFTER_DAYS = 7

_TEMPLATE_FIELDS = ("play_probability", "effectiveness", "note", "source", "as_of")


@dataclass
class InjuryAssessment:
    """A judgement about one player's availability.

    Both probabilities are optional: an assessment that only sets `effectiveness`
    still overrides the effectiveness half of the heuristic and leaves the rest
    alone. `None` means "no opinion", which is different from 1.0.
    """

    play_probability: float | None = None
    effectiveness: float | None = None
    note: str = ""
    source: str = ""
    as_of: str = ""
    name: str = ""

    def __post_init__(self) -> None:
        for label in ("play_probability", "effectiveness"):
            value = getattr(self, label)
            if value is None:
                continue
            if not 0.0 <= float(value) <= 1.0:
                raise ValueError(
                    f"{label} for {self.name or 'player'} must be between 0 and 1, "
                    f"got {value!r}"
                )
            setattr(self, label, float(value))

    @property
    def is_empty(self) -> bool:
        """True when the entry carries no judgement, only the template scaffold."""
        return self.play_probability is None and self.effectiveness is None

    def age_days(self, today: date | None = None) -> int | None:
        """Days since the assessment was made, or None if it carries no date."""
        if not self.as_of:
            return None
        try:
            stamped = date.fromisoformat(self.as_of.strip())
        except ValueError:
            return None
        return (today or date.today()).toordinal() - stamped.toordinal()

    def is_stale(self, today: date | None = None, max_age_days: int = STALE_AFTER_DAYS) -> bool:
        """True when the assessment is old enough that it should not be trusted.

        An undated assessment counts as stale: a number with no date behind it
        cannot be checked, and quietly trusting it is the failure this guards.
        """
        age = self.age_days(today)
        return age is None or age > max_age_days


@dataclass
class AssessmentFile:
    """Parsed assessment file: judgements plus what was wrong with it."""

    assessments: dict[str, InjuryAssessment] = field(default_factory=dict)
    stale: list[InjuryAssessment] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)


def load_assessments(
    path: str,
    today: date | None = None,
    max_age_days: int = STALE_AFTER_DAYS,
) -> AssessmentFile:
    """Load injury assessments, keyed by cross-source match key.

    Entries left blank by the template are skipped rather than read as zeros -
    an unanswered question is not a judgement that the player will not play.

    Raises `ValueError` for a malformed file or an out-of-range probability.
    Reporting stale entries rather than dropping them is deliberate: the caller
    should say the read is old, because a silently ignored assessment looks
    exactly like one that was applied.
    """
    with open(path, encoding="utf-8") as f:
        try:
            raw = json.load(f)
        except json.JSONDecodeError as e:
            raise ValueError(f"{path} is not valid JSON: {e}") from e

    entries = raw.get("players") if isinstance(raw, dict) else raw
    if not isinstance(entries, list):
        raise ValueError(
            f"{path} must hold a list of players, either at the top level or "
            f'under a "players" key'
        )

    result = AssessmentFile()
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("name") or "").strip()
        if not name:
            continue

        assessment = InjuryAssessment(
            play_probability=_optional_float(entry.get("play_probability")),
            effectiveness=_optional_float(entry.get("effectiveness")),
            note=str(entry.get("note") or ""),
            source=str(entry.get("source") or ""),
            as_of=str(entry.get("as_of") or ""),
            name=name,
        )
        if assessment.is_empty:
            result.skipped.append(name)
            continue

        key = match_key(name, entry.get("position"), entry.get("team"))
        if not key:
            result.skipped.append(name)
            continue
        result.assessments[key] = assessment
        if assessment.is_stale(today=today, max_age_days=max_age_days):
            result.stale.append(assessment)

    return result


def write_assessment_template(
    path: str,
    ranked_players: list[RankedPlayer],
    limit: int = 0,
    today: date | None = None,
) -> int:
    """Write a template covering every player carrying an injury designation.

    Emits the raw Sleeper fields alongside empty judgement fields, so whatever
    fills the file in - a model, or a person reading a beat report - has the
    context and does not have to guess what is being asked. Returns the number of
    entries written.

    `limit` caps how far down the board to look; 0 means the whole board. Only
    flagged players are written, because asking for a judgement on 400 healthy
    players buries the ten that matter.
    """
    board = sorted(ranked_players, key=lambda r: r.rank)
    if limit > 0:
        board = board[:limit]

    stamp = (today or date.today()).isoformat()
    entries = []
    for rp in board:
        if not rp.injury_status:
            continue
        entries.append(
            {
                "name": rp.display_name,
                "position": rp.position,
                "team": rp.team,
                "rank": rp.rank,
                "sleeper_injury_status": rp.injury_status,
                "sleeper_body_part": rp.injury_body_part or "",
                "play_probability": None,
                "effectiveness": None,
                "note": "",
                "source": "",
                "as_of": stamp,
            }
        )

    payload = {
        "generated": stamp,
        "_schema": {
            "play_probability": "0-1, chance he is on the field at all",
            "effectiveness": "0-1, how close to full if he plays (1.0 = no limits)",
            "note": "one line of reasoning; shown next to the recommendation",
            "source": "where the read came from",
            "as_of": f"ISO date; entries older than {STALE_AFTER_DAYS} days are "
            f"reported as stale",
        },
        "players": entries,
    }
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(payload, f, indent=2)
        f.write("\n")
    return len(entries)


def _optional_float(value: object) -> float | None:
    """Read a probability that may legitimately be absent."""
    if value is None or value == "":
        return None
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError) as e:
        raise ValueError(f"expected a number between 0 and 1, got {value!r}") from e


__all__ = [
    "STALE_AFTER_DAYS",
    "AssessmentFile",
    "InjuryAssessment",
    "load_assessments",
    "write_assessment_template",
]

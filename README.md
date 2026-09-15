# ff-tools

Fantasy football draft and roster tools for Sleeper and ESPN leagues.

## Quick start

```bash
pip install -e ".[dev]"

# One-shot snapshot of a guillotine league
export PYTHONPATH=src
python examples/guillotine_board.py <league_id> <sleeper_username>

# Live draft assistant (polls every 3s)
python -m ff_tools.guillotine --league-id <league_id> --username <sleeper_username>
```

## Guillotine league strategy

The tool recommends picks by **survival score** — how likely a player helps you avoid the lowest weekly score. This prioritizes high-floor, consistent players over boom/bust options.

### Scoring model

```
survival_score = rank_score × floor × position_value × scarcity × need × availability
```

| Factor | What it measures | Range |
|--------|-----------------|-------|
| `rank_score` | `1000 / √rank` — value decay from ECR | 32–1000 |
| `floor` | Consistency bonus by tier | 0.85–1.15 |
| `position_value` | Positional multiplier (with streaming discount) | 0.50–1.05 |
| `scarcity` | Remaining supply vs league-wide starter demand | 0.85–1.50 |
| `need` | Roster gap at the position | 1.00–1.50 |
| `availability` | Injury risk, priced against the replacement | 0.02–1.00 |

### Positional multipliers

| Pos | Prior | Rationale |
|-----|-------|-----------|
| QB | 0.65 | Streamable — discount scales with waiver depth |
| RB | 1.04 | Moderate scarcity |
| WR | 1.05 | Reference position |
| TE | 1.00 | Scarcity measured independently (was 1.25, caused double-counting) |
| K | 0.50 | Pure stream |
| DEF | 0.50 | Pure stream |

The streaming discount (`streaming_discount()`) rescales QB/K/DEF based on actual waiver depth. In a 32-team league that drafts 32 of ~35 startable QBs, the discount lifts to ~1.0 because there is nothing left to stream.

### Availability (injury)

Consensus rankings carry no injury data, so designations are merged from Sleeper's player pool (`merge_injury_status()`).

A missed game does not score zero — you start someone else. So the cost of an injury is the **gap to the replacement**, and that gap is a property of the league, not of the player:

```
availability = p × max(e, r) + (1 − p) × r
```

| Term | Meaning | Source |
|------|---------|--------|
| `p` | Probability he is on the field | designation, or an assessment |
| `e` | Effectiveness if he plays | body part, or an assessment |
| `r` | Replacement's value as a fraction of his | the rankings board |

`max(e, r)` rather than `e`, because a hobbled player need not be started. It also keeps the score monotonic in `p`: the naive `p·e + (1 − p)·r` scores a *worse* designation higher whenever the replacement beats the hobbled player.

`r` comes from `replacement_ratio()`, which walks the position's own ranks out to `starter demand + bench picks × position share` and compares `rank_score` there to the player's. This is why the same "Questionable" costs a WR26 far more than a WR95 — the elite player's replacement is worth 30% of him, the marginal one's is worth 58%.

| Designation | `p` | Horizon |
|-------------|-----|---------|
| none / Active / Probable | 1.00 | — |
| Questionable | 0.75 | near-term |
| COVID | 0.60 | near-term |
| Doubtful | 0.28 | near-term |
| Out | 0.02 | near-term (out *this game*) |
| NA / PUP | 0.05 | stretch |
| IR / DNR / Sus | 0.02 | stretch |

**Near-term vs stretch matters** because the replacement means different things. A one-week absence is genuinely patchable off waivers, so it gets full credit for `r`. A season-ending absence is not: crediting it with a waiver replacement values the pick at whatever the waiver wire is worth, which you had anyway. Stretch absences therefore scale `r` by `carryable = (bench_spots − 1) / 2`, so in this league's one-bench-spot format an IR stash is worth ~nothing.

For near-term designations the body part sets `e` — a Questionable ACL is not a Questionable illness. Structural is checked before generic, so `Knee - ACL` does not read as knee soreness.

| Body part | `e` | Example |
|-----------|-----|---------|
| Structural | 0.80 | `Knee - ACL`, `Achilles`, `Lisfranc`, `Spine` |
| Lingering | 0.90 | `Knee - MCL`, `Hamstring`, `Heel`, `Ankle` |
| Unrecognised / `Undisclosed` / blank | 0.95 | no signal either way |
| Minor | 0.98 | `Illness`, `Rest`, `Thumb`, `Concussion` |

Stretch absences ignore the body part: "how limited is he when he plays" is not the operative question. An unrecognised *designation* is not penalized at all — the size of that penalty is not something to invent.

#### Assessments

"Questionable" covers a precautionary rest day and a knee that will not be right until October, and `injury_body_part` is `Undisclosed` for roughly half the flagged players. Two short strings cannot separate those; something reading the news can. So `--write-injury-template` emits one entry per flagged player with the raw Sleeper fields, and `--injury-notes` reads the filled file back:

```bash
python -m ff_tools.guillotine --league-id … --username … \
  --write-injury-template notes.json     # emit the questions
python -m ff_tools.guillotine --league-id … --username … \
  --injury-notes notes.json              # apply the answers
```

Each entry may set `play_probability`, `effectiveness`, or both; whichever is supplied overrides that half of the heuristic and the rest falls through, so a partly-filled file is useful. Blank entries are skipped rather than read as zeros — an unanswered question is not a judgement that he will not play. Entries carrying no `as_of` date, or one over a week old, are reported as stale rather than silently dropped, because an ignored assessment looks exactly like an applied one.

The corrections run in both directions, so this is not a uniform discount. On a live 2026 week-1 board: Sleeper listed Mike Evans' body part as `Foot`, which the heuristic scored as minor (0.79, among the *safer* flagged players) when the actual report had him not yet back at practice with a groin injury (0.28). Conversely Sleeper's `Knee - ACL` tag on Tucker Kraft triggered the structural branch, while the beat reporting had him "full go" for week 1 — he moved from #56 to #26.

Pass `--ignore-injuries` to score every player as healthy and skip the merge entirely.

### Scarcity

`startable_depth()` cuts the rankings board at `total_teams × roster_size` — the number of players who actually get drafted. This prevents the long tail of a 500-player board from masking genuine shortages. The scarcity ratio (remaining supply / league demand) maps to multipliers:

| Ratio | Multiplier | Label |
|-------|-----------|-------|
| < 0.3 | 1.50 | Extreme shortage |
| < 0.5 | 1.40 | Severe shortage |
| < 0.75 | 1.25 | Moderate shortage |
| < 1.0 | 1.10–1.22 | Slight shortage |
| 1.0–1.5 | 1.00 | Adequate |
| 1.5–2.0 | 0.95 | Surplus |
| > 2.0 | 0.85 | Large surplus |

### Data sources

1. **FantasyPros ECR** — Expert Consensus Rankings (100+ experts, ~500 players)
2. **ESPN projections** — fallback with projected points and ownership
3. **Sleeper player pool** — last resort (no projections)
4. **Fantasy Football Calculator ADP** — real draft data (free API, no auth)

ADP falls back through 14/12/10/8-team sizes since 32-team ADP is not available from any free source.

## CLI usage

### Guillotine draft assistant

```bash
# Live polling mode (Ctrl+C to stop)
python -m ff_tools.guillotine \
  --league-id 1389361006941073408 \
  --username eclabaugh \
  --poll-interval 3

# Override rounds/roster if league settings are wrong
python -m ff_tools.guillotine \
  --league-id 1389361006941073408 \
  --username eclabaugh \
  --rounds 9 \
  --roster-size 9
```

| Flag | Effect |
|------|--------|
| `--ignore-injuries` | Score everyone as healthy, skip the Sleeper merge |
| `--write-injury-template PATH` | Emit a JSON stub for every flagged player |
| `--injury-notes PATH` | Apply assessments from a filled stub |

### One-shot snapshot (no polling)

```bash
export PYTHONPATH=src
python examples/guillotine_board.py 1389361006941073408 eclabaugh
```

### Standalone analysis

```bash
python -m ff_tools.guillotine.analysis  # positional values from ESPN projections
```

## Architecture

```
src/ff_tools/
├── models/
│   ├── player.py          # Player dataclass (Sleeper/ESPN parsing)
│   └── draft.py           # Draft, DraftPick (3RR, slot mapping)
├── draft/
│   ├── board.py           # Base DraftBoard (pick tracking, BPA)
│   ├── rankings.py        # RankingsSource (FantasyPros/ESPN/Sleeper)
│   ├── order.py           # Snake + 3RR pick-number math
│   └── assistant.py       # Base DraftAssistant (polling loop)
├── guillotine/
│   ├── strategy.py        # Scoring engine (survival score, scarcity, need)
│   ├── injury.py          # Assessment template + loader (overrides the heuristic)
│   ├── board.py           # GuillotineDraftBoard (supply tracking, runs)
│   ├── assistant.py       # GuillotineDraftAssistant (live recommendations)
│   ├── analysis.py        # Data-driven positional values from projections
│   └── cli.py             # CLI entry point
├── utils/
│   └── names.py           # Cross-source player matching (normalized keys)
├── sleeper/               # Sleeper API client (read-only, no auth)
├── espn/                  # ESPN API client
└── waivers/               # Waiver wire analysis
```

### Key design decisions

- **Name-based matching**: Sleeper, ESPN, FantasyPros and FantasyFootballCalculator each mint unique player IDs. Drafted players are matched against rankings by normalized name key, not ID.
- **Rank-based scoring**: `rank_to_score = 1000 / √rank` — no projected points dependency. The multipliers (floor, scarcity, need) do the real work.
- **League-derived scarcity**: `startable_depth()` cuts the board at `total_teams × roster_size` rather than using a hand-tuned pool size. Scarcity tightens automatically in deeper leagues.
- **Injury is an availability factor, not a floor adjustment**: A player who does not suit up scores zero, which in a live-elimination format is the whole risk rather than a haircut on a season projection. It is a separate multiplier so it stays legible next to the rank-derived `floor`.
- **An injury is priced against the replacement, not against zero**: A missed game costs the gap to whoever you start instead, which is a fact about the league's depth rather than about the player. This keeps the penalty league-derived like `scarcity`, instead of a hand-tuned discount that ignores whether the position is deep.
- **The designation heuristic is a fallback, not the model**: Sleeper's two-string vocabulary cannot separate a rest day from a torn ACL, so `injury.py` exists as a seam for a real read to override it. The heuristic answers when nothing better is available; it is not the intended source of truth.
- **Streaming is conditional**: The QB/K/DEF discounts assume streaming is possible. `streaming_discount()` rescales based on actual waiver depth — a 32-team league has no QB waiver wire, so the discount lifts.

### Running tests

```bash
pytest tests/ -v          # 244 tests
ruff check src/ff_tools/  # lint
```

## SBA Guillotine league notes (2026, league 1389361006941073408)

32 teams, roster `QB/RB/RB/WR/WR/TE/FLEX/FLEX/BN`, no K/DEF.
Live tools: `python -m ff_tools.guillotine.watch_cli --league-id ... --week N --cuts C`
and `python -m ff_tools.guillotine.faab_cli --league-id ... --username ...`.

### Open questions (need league rules)

1. **FAAB budget**: league settings report `waiver_budget: 1000`, but analysis
   has been modeled on 100. Bid *percentages* scale either way — confirm the
   real number and pass it via `--budget` / `--remaining-budget`.
2. **Cut schedule**: week 1 chops 4 teams. Is it 4 every week, or does it taper
   (e.g. 4 early, then 1/week)? Pass the weekly number via `--cuts N`; if a
   fixed schedule exists, bake it in as the per-week default.

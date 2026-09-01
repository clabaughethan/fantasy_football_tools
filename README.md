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
survival_score = rank_score × floor × position_value × scarcity × need
```

| Factor | What it measures | Range |
|--------|-----------------|-------|
| `rank_score` | `1000 / √rank` — value decay from ECR | 32–1000 |
| `floor` | Consistency bonus by tier | 0.85–1.15 |
| `position_value` | Positional multiplier (with streaming discount) | 0.50–1.05 |
| `scarcity` | Remaining supply vs league-wide starter demand | 0.85–1.50 |
| `need` | Roster gap at the position | 1.00–1.50 |

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
- **Streaming is conditional**: The QB/K/DEF discounts assume streaming is possible. `streaming_discount()` rescales based on actual waiver depth — a 32-team league has no QB waiver wire, so the discount lifts.

### Running tests

```bash
pytest tests/ -v          # 183 tests
ruff check src/ff_tools/  # lint
```

# CUI // SP-CTI

# ACF Harvester — Genesis predictions + introspective telemetry + cross-source dedup (acf-harvest-02)

Extends the ACF signal harvester (`tools/foundry/harvester.py`, built in
acf-harvest-01) from three sources to **five**, and adds cross-source dedup so the
same capability surfaced by two engines collapses to a single `foundry_signals`
row.

## What shipped

### Two new harvest sources

| Source | Store / entry point | Theme | Score | Keywords |
|--------|--------------------|-------|-------|----------|
| **genesis** | `oracle_predictions` table | `prediction_text` | `confidence` | `prediction_type` + `severity` |
| **telemetry** | `tools/innovation/introspective_analyzer` read-only analyses | analysis-derived title | analysis-derived score | analysis type + salient finding tag |

- **genesis** covers BOTH Oracle lens predictions AND Internal Awareness gap
  nodes — the gap detector (`tools/awareness/gap_detector.py`) persists gaps as
  `oracle_predictions` rows under `lens_name='internal_awareness'` /
  `prediction_type='gap::<rule>'`, so one table read captures both. It fits the
  existing `_SOURCES` table-descriptor pattern (no special-casing).

- **telemetry** invokes only the analyzer's **pure read** functions
  (`analyze_gate_failures`, `analyze_unused_tools`, `analyze_slow_pipelines`,
  `analyze_knowledge_gaps`). It deliberately never calls
  `generate_introspective_signals()`, which would append to `innovation_signals`
  — keeping the harvest side-effect free, deterministic and air-gap safe (same
  invariant as the table-backed sources, which read stores rather than re-scoring).
  Telemetry signals reuse the analyzer's own `_signal_title` / `_signal_score`, so
  they are titled and scored exactly as they would be in the innovation pipeline.

### Cross-source dedup

After per-source caps are applied (so one noisy store can't eat another's
budget), all collected signals pass through `_dedupe_cross_source`. The dedup key
is a **SHA-256 of the normalized theme + sorted keywords** (case- and
whitespace-insensitive, keyword-order invariant). Duplicates collapse to the
**highest-scoring** representative; first-seen order is preserved for determinism.

## Files touched

- `tools/foundry/harvester.py` (+ `icdev/tools/foundry/harvester.py` mirror) —
  genesis descriptor, `_harvest_telemetry`, `_dedupe_key`/`_dedupe_cross_source`,
  5-engine harvest loop.
- `tests/test_foundry_harvester.py` — genesis source, telemetry gap → signal,
  cross-source dedup (stored once), dedup-key normalization.
- `tools/manifest/autonomous-capability-foundry.md` — Harvester entry updated to
  5 sources + dedup.

Config (`args/foundry_config.yaml` `sources.genesis` / `sources.telemetry`) and
constants (`SOURCE_ENGINES`) already anticipated both sources from acf-harvest-01.

## Acceptance criteria (met)

- A duplicate signal across two sources is stored once (`test_harvest_dedupes_same_signal_across_sources`).
- A telemetry gap becomes a signal (`test_harvest_telemetry_gap_becomes_signal`).
- A genesis prediction becomes a signal (`test_harvest_genesis_from_oracle_predictions`).
- 9/9 harvester tests pass; `ruff check` clean.

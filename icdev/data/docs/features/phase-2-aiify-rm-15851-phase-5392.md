# CUI // SP-CTI

# Phase 2 — Core Modernization: Adaptive SPRS Anomaly Detection in `comply_cmmc`

**Opportunity:** 5392 (scan 27, roadmap `rm-1585150d1c`)
**Pattern:** `hardcoded_threshold` → `anomaly_detection`
**Module:** `tools/proposal_genesis/reflexes/comply_cmmc.py` (R18 — CMMC Supply Chain Validator)
**Recommended model:** `claude-haiku-4-5-20251001` (scanner-tier; this modernization remains **zero-LLM / deterministic**)

## Problem

The Comply_CMMC reflex flagged teaming partners using three brittle hardcoded
thresholds:

- `cmmc_level < 2` — CMMC Level 2 regulatory floor for CUI handling
- `sprs_score < 110` — required perfect NIST 800-171 SPRS score
- `LIMIT 20` — opportunities scanned per run

The `sprs_score < 110` check is the problematic one: SPRS scores range from
**-203 to +110**, where 110 = full implementation of all 110 controls. Requiring
a perfect 110 flags nearly every real-world partner, producing noisy,
low-signal findings. A static cut-off cannot distinguish a partner who is a
genuine low outlier from one who is simply a point or two short of perfect.

## Change

Replaced the hardcoded thresholds with **config-driven, statistically adaptive
anomaly detection**, mirroring the established house pattern in sibling reflexes
(`map.py`, `publish.py`, `shape.py`).

### Code (`comply_cmmc.py`)

- Extracted module-level fallback constants: `_MIN_CMMC_LEVEL` (2),
  `_MIN_SPRS_SCORE` (110), `_OPP_PROCESS_LIMIT` (20).
- Added `_compute_sprs_threshold(anomaly_cfg)`: computes a lower control limit
  `mean - sigma*std` over the `pg_teaming_workshare.sprs_score` distribution,
  clamped to `[sprs_floor, sprs_ceil]`. A partner is flagged when its SPRS is an
  **anomalous low outlier relative to the partner population**, not merely shy
  of a perfect 110.
- `_check_teaming_cmmc()` now takes `min_cmmc_level` and `sprs_threshold`
  parameters (defaulting to the constants, so direct callers/tests are
  unaffected).
- `run()` resolves both thresholds from the `anomaly_detection` config block and
  records them in the audit row and the returned `details`.

### Config (`args/proposal_genesis_config.yaml`)

Added an `anomaly_detection` block under `reflexes.comply_cmmc`:
`enabled`, `min_samples` (15), `sigma_multiplier` (1.0), `min_cmmc_level` (2),
`fallback_sprs_threshold` (110), and `adaptive_bounds` (`sprs_floor` 70,
`sprs_ceil` 110).

## Safety / backward compatibility

- **CMMC level** stays a categorical regulatory floor (Level 2) — not subject to
  statistical relaxation, only config override.
- The SPRS threshold is **clamped to ≤ 110** (never weaker than the regulatory
  ideal in the relaxing direction beyond the configured floor) and **≥ 70**.
- With fewer than `min_samples` scored partners — or `enabled: false` — the
  helper returns the static `_MIN_SPRS_SCORE` (110), so **behavior is identical
  to the pre-modernization code when there is no history**.
- All DB access is wrapped in try/except with `get_connection()` and falls back
  to the static threshold on any error.

## Tests

`tests/genesis/test_comply_cmmc_anomaly.py`:

- `_compute_sprs_threshold`: disabled→fallback, insufficient history→fallback,
  sufficient history→computed lower control limit, ceil/floor clamps, DB-error
  fallback, `None` cfg defaults.
- `_check_teaming_cmmc`: default thresholds flag imperfect SPRS, adaptive
  threshold passes scores above the limit, CMMC level below floor flagged,
  missing data flagged.
- Constant sanity checks.

# CUI // SP-CTI

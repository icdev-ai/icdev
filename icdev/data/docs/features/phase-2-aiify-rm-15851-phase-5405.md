# CUI // SP-CTI

# Phase 2 — Core Modernization: fulfill.py `hardcoded_threshold` → `anomaly_detection`

**AI-ify opportunity:** 5405 (scan 27, roadmap rm-1585150d1c)
**Target:** `tools/proposal_genesis/reflexes/fulfill.py` (R11 Fulfill Reflex)
**Pattern:** `hardcoded_threshold` → `anomaly_detection`

## Context

Opp 5404 (same file/pattern/paradigm) extracted the inline magic numbers in
`fulfill.py` — the deliverables lookahead window, per-run generation cap, and
stale-doc age — into named, config-aligned module constants overridable from
`args/proposal_genesis_config.yaml` (`reflexes.fulfill`).

That work left one threshold still hardcoded: the **anomaly gate itself** — the
GovEval composite score below which a generated compliance CDRL is flagged
`needs_review`. For the `anomaly_detection` paradigm this is the load-bearing
threshold, so opp 5405 completes the slice.

## Change

1. **Constant block** — replicated the 5404 module constants (`_DEFAULT_DAYS_AHEAD`,
   `_DEFAULT_MAX_GENERATIONS`, `_DEFAULT_STALE_THRESHOLD_DAYS`,
   `_GOVEVAL_GATE_THRESHOLD`, `_CDRL_GEN_TIMEOUT_SECS`) so the worktree base matches
   the canonical version; replaced remaining inline `0.5` / `300` / `14` / `90`
   literals with the named constants.
2. **Config-overridable anomaly gate** — `_generate_cdrl()` now takes a
   `goveval_gate_threshold` parameter (default `_GOVEVAL_GATE_THRESHOLD`). The gate
   comparison uses the parameter instead of a hardcoded `0.5`.
3. **`run()` wiring** — reads `goveval_gate_threshold` from config
   (`config.get("goveval_gate_threshold", _GOVEVAL_GATE_THRESHOLD)`) and threads it
   into `_generate_cdrl()`.
4. **Config key** — added `goveval_gate_threshold: 0.5` under `reflexes.fulfill` in
   `args/proposal_genesis_config.yaml`.

Change config, not code: the anomaly threshold is now tunable per environment
without editing Python.

## Tests

`tests/genesis/test_fulfill_constants.py` (8 tests, all pass):
- existing constant/range/wiring assertions (5404)
- `TestAnomalyThresholdOverridable::test_generate_cdrl_exposes_threshold_param`
- `TestAnomalyThresholdOverridable::test_config_exposes_goveval_gate_threshold`

## Verification

- `ruff check` clean on `fulfill.py` and the test file.
- Module imports cleanly; `_generate_cdrl` signature includes `goveval_gate_threshold`.
- `pytest tests/test_proposal_genesis.py` — 187 passed (1 pre-existing unrelated
  failure: `test_daemon_get_status`, missing `pg_proposal_genesis_state` table in
  the minimal test schema).

## Notes

Opp 5404's modernization existed only as **uncommitted** edits in the main working
tree (never committed to a branch). This branch commits the consistent, complete
version (5404 constants + the 5405 anomaly-gate override) so the work is captured
in git rather than left as loose working-tree state.

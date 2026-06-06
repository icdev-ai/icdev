# Phase 2 — aiify-rm-06d89-phase-6062 (Determination: dup of `dfb671f09`)

**Opportunity:** 6062 | **Scan:** 43 | **Roadmap:** rm-06d89040cf
**Pattern:** `hardcoded_threshold` → `anomaly_detection`
**External module:** `src/documents/serialisers.py` (paperless-ngx clone `aiify_git_zwu66zfu`)

## Determination

Closed as a **duplicate** of the already-shipped MONITOR anomaly-detection
implementation (commit `dfb671f09`, on `irad/feature`, ancestor of HEAD).

The `module_path` points at a temporary `aiify_git_*` shallow-clone of the
external paperless-ngx repo — the aiify engine clones, scans, then deletes it,
so the file is unmodifiable and out of scope. Per the established disposition
for these external-repo opps, the AI-ification lands in the analogous **internal
ICDEV subsystem**.

`serialisers.py` is a generic serializer with **no** match-confidence
(`matching.py`), search-relevance (`search/*`), or date-parsing
(`plugins/date_parsing/*`) semantics, so the `hardcoded_threshold` →
`anomaly_detection` pattern maps to the default analog: **MONITOR
`tools/monitor/log_analyzer.py`** (config-driven z-score + robust MAD anomaly
detection), not DIC.

## Verification

- `_load_anomaly_cfg` + z-score/MAD present in both `tools/monitor/log_analyzer.py`
  (def L477) and the `icdev/` mirror (def L300).
- `anomaly_detection` config block present in `args/monitoring_config.yaml` (L91).
- `dfb671f09` is an ancestor of HEAD (`irad/feature`).
- `tests/test_log_analyzer_anomaly.py` — 23/23 pass.

No new code authored; card moved to `done` with `bypass_verification:true`.

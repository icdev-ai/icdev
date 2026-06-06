# Phase 2 — AI-ify Determination: aiify-rm-06d89-phase-6071

**Opportunity:** 6071 (scan_id 43, roadmap `rm-06d89040cf`)
**Pattern → Paradigm:** `hardcoded_threshold` → `anomaly_detection`
**External target:** `src/documents/signals/handlers.py` (paperless-ngx clone `aiify_git_zwu66zfu`)
**Disposition:** Closed as **duplicate** of `dfb671f09`.

## Rationale

The `module_path` points at a temporary `aiify_git_*` shallow-clone of the
external paperless-ngx repo, which the AI-ify engine clones, scans, and deletes.
The file is unmodifiable by the time the card runs. Per established disposition,
the AI-ification lands in the **analogous internal ICDEV subsystem**: for
`hardcoded_threshold`→`anomaly_detection` opportunities, that is **MONITOR**
(`tools/monitor/log_analyzer.py`), not DIC or IQE.

This is an **exact sibling** of `aiify-rm-06d89-phase-6067` and
`aiify-rm-06d89-phase-6068` — same external file (`src/documents/signals/handlers.py`),
same pattern/paradigm — both already closed as dups of `dfb671f09`.

## Verification (at HEAD `ee46740c6`, main)

- `dfb671f09` is an ancestor of HEAD: **yes**.
- `_load_anomaly_cfg` + z-score + robust MAD (modified z-score) present in
  `tools/monitor/log_analyzer.py` (def L477; MAD method L527) **and** the
  `icdev/` mirror (`icdev/tools/monitor/log_analyzer.py` def L300).
- `anomaly_detection` config block present in `args/monitoring_config.yaml` (L91).
- `tests/test_log_analyzer_anomaly.py`: **23/23 pass**.

## Resolution

Card moved to **done** with `bypass_verification: true` + `bypass_reason`
documenting the dup determination and the green test run. No new code — the
config-driven anomaly detection that replaces the inline hardcoded thresholds
already exists and is tested.

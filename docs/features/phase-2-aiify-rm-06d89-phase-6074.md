# Phase 2 — AI-ify Determination: aiify-rm-06d89-phase-6074

**Opportunity:** 6074 (scan_id 43, roadmap `rm-06d89040cf`)
**Pattern:** `hardcoded_threshold` → `anomaly_detection`
**External module:** `src/documents/tasks.py` (paperless-ngx shallow clone `aiify_git_zwu66zfu`)
**Disposition:** Closed as **duplicate** of `dfb671f09` (MONITOR `log_analyzer.py` anomaly-detection).

## Rationale

The `module_path` points at a temp `aiify_git_*` clone of an **external** open-source
repo (paperless-ngx) that the AI-ify engine shallow-clones, scans, and deletes. The
file is not an ICDEV asset and is not a valid AI-ification target. Per the established
disposition, the AI-ification lands in the **analogous ICDEV internal subsystem**
selected by **pattern + paradigm**, not by filename.

For `hardcoded_threshold` → `anomaly_detection`, the analog is **MONITOR**
(`tools/monitor/log_analyzer.py`), where inline z-score / error-rate constants were
replaced with a config-driven `anomaly_detection` block in `args/monitoring_config.yaml`
plus a robust MAD (modified z-score) method. This is the same target as siblings
6119 / 6120 / 6121 / 6130 / 6133 (all paperless `src/*` files, same pattern), and is
complemented by 6090 (DIC anomaly severity AI-ify).

## Verification (at HEAD `b99d8b471`, branch `irad/feature`)

- `dfb671f09` is an ancestor of HEAD ✓
- `_load_anomaly_cfg` present in `tools/monitor/log_analyzer.py` ✓
- `_load_anomaly_cfg` present in `icdev/tools/monitor/log_analyzer.py` mirror ✓
- `anomaly_detection` block in `args/monitoring_config.yaml` (L91, zscore + mad) ✓

No competing implementation authored. Card moved to done with
`bypass_verification: true` + `bypass_reason`.

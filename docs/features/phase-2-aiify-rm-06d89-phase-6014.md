# Phase 2 — AI-ify Determination: aiify-rm-06d89-phase-6014

**Opportunity:** 6014 (scan_id 43, roadmap `rm-06d89040cf`)
**Pattern:** `hardcoded_threshold` → `anomaly_detection`
**External module:** `src/documents/filters.py` (paperless-ngx shallow clone `aiify_git_zwu66zfu`)
**Disposition:** Closed as **duplicate** of `dfb671f09` (MONITOR `log_analyzer.py` anomaly-detection), and a re-emission of sibling cards `aiify-rm-06d89-phase-5996` / `5997` / `6000` / `6005` (paperless `src/documents/*` files, identical pattern + paradigm).

## Rationale

The `module_path` points at a temp `aiify_git_*` clone of an **external** open-source
repo (paperless-ngx) that the AI-ify engine shallow-clones, scans, and deletes. The
file is unmodifiable by the time the kanban card runs (confirmed GONE). Per the
established disposition, the AI-ification lands in the **analogous ICDEV internal
subsystem** selected by **pattern + paradigm**, not by filename.

For `hardcoded_threshold` → `anomaly_detection`, the analog is **MONITOR**
(`tools/monitor/log_analyzer.py`), where inline z-score / error-rate constants were
replaced with a config-driven `anomaly_detection` block in `args/monitoring_config.yaml`
plus a robust MAD (modified z-score) method. This is the same target — and the same
underlying opportunity — as the sibling paperless `src/documents/*` cards. The scanner
re-emits one card per scan for the same files; no additional code change is warranted.

## Verification (at HEAD, branch `kanban/aiify-rm-06d89-phase-6014`)

- `dfb671f09` is an ancestor of HEAD ✓
- temp clone path `aiify_git_zwu66zfu/src/documents/filters.py` no longer exists ✓
- `_load_anomaly_cfg` present in `tools/monitor/log_analyzer.py` ✓
- `_load_anomaly_cfg` present in `icdev/tools/monitor/log_analyzer.py` mirror ✓
- `anomaly_detection` block in `args/monitoring_config.yaml` ✓

No competing implementation authored. Card moved to done with
`bypass_verification: true` + `bypass_reason`.

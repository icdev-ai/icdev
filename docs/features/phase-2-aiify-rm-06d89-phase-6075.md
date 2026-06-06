# Phase 2 — AI-ify Determination: aiify-rm-06d89-phase-6075

**Opportunity:** 6075 (scan_id 43, roadmap `rm-06d89040cf`)
**Pattern:** `hardcoded_threshold` → `anomaly_detection`
**External module:** `src/documents/tasks.py` (paperless-ngx shallow clone `aiify_git_zwu66zfu`)
**Disposition:** Closed as **duplicate** of `dfb671f09` (MONITOR `log_analyzer.py` anomaly-detection).

## Rationale

The `module_path` points at a temp `aiify_git_*` clone of an **external** open-source
repo (paperless-ngx) that the AI-ify engine shallow-clones, scans, and deletes. The
file is unmodifiable by the time the kanban card runs (confirmed GONE). Per the
established disposition, the AI-ification lands in the **analogous ICDEV internal
subsystem** selected by **pattern + paradigm**, not by filename.

This card is the **exact same opportunity** as sibling `aiify-rm-06d89-phase-6077` —
identical `module_path` (`src/documents/tasks.py`), pattern (`hardcoded_threshold`),
and paradigm (`anomaly_detection`). 6077 was already closed as a dup of `dfb671f09`.

For `hardcoded_threshold` → `anomaly_detection`, the analog is **MONITOR**
(`tools/monitor/log_analyzer.py`), where inline z-score / error-rate constants were
replaced with a config-driven `anomaly_detection` block in `args/monitoring_config.yaml`
plus a robust MAD (modified z-score) method. Same target as siblings 6067 / 6068 /
6070 / 6071 / 6077 / 6083 / 6087 / 6088 / 6119 / 6120 / 6121 / 6133 (all paperless
`src/*` files, same pattern).

## Verification (at HEAD `12c6c6812`, branch `kanban/aiify-rm-06d89-phase-6075`)

- `dfb671f09` is an ancestor of HEAD ✓
- temp clone path `aiify_git_zwu66zfu/src/documents/tasks.py` no longer exists ✓
- `_load_anomaly_cfg` present in `tools/monitor/log_analyzer.py` (def L477) ✓
- `_load_anomaly_cfg` present in `icdev/tools/monitor/log_analyzer.py` mirror (def L300) ✓
- `anomaly_detection` block in `args/monitoring_config.yaml` (L91, zscore + mad) ✓

No competing implementation authored. Card moved to done with
`bypass_verification: true` + `bypass_reason`.

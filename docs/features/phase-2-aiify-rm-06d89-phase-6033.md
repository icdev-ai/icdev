# Phase 2 — AI-ify Determination: aiify-rm-06d89-phase-6033

**Opportunity:** 6033 (scan_id 43, roadmap `rm-06d89040cf`)
**Pattern:** `hardcoded_threshold` → `anomaly_detection`
**External module:** `src/documents/matching.py` (paperless-ngx shallow clone `aiify_git_zwu66zfu`)
**Disposition:** Closed as **duplicate** of `dfb671f09` (MONITOR `log_analyzer.py` anomaly-detection), and a re-emission of sibling cards `aiify-rm-06d89-phase-6019` / `6028` / `6029` / `6031` (identical pattern + paradigm, same paperless clone).

## Rationale

The `module_path` points at a temp `aiify_git_*` clone of an **external** open-source
repo (paperless-ngx) that the AI-ify engine shallow-clones, scans, and deletes. The
file is unmodifiable by the time the kanban card runs (confirmed GONE). Per the
established disposition, the AI-ification lands in the **analogous ICDEV internal
subsystem** selected by **pattern + paradigm**, not by filename.

`src/documents/matching.py` holds paperless's fuzzy document-matching ratio
thresholds — a `hardcoded_threshold` instance. For `hardcoded_threshold` →
`anomaly_detection`, the analog is **MONITOR** (`tools/monitor/log_analyzer.py`),
where inline z-score / error-rate constants were replaced with a config-driven
`anomaly_detection` block in `args/monitoring_config.yaml` plus a robust MAD
(modified z-score) method. This is the same target — and the same underlying
opportunity — as the sibling paperless `src/*` cards under roadmap
`rm-06d89040cf` (all same pattern). The scanner re-emits one card per scan for the
same external file; no additional code change is warranted.

## Verification (branch `kanban/aiify-rm-06d89-phase-6033`)

- `dfb671f09` is an ancestor of HEAD ✓
- temp clone path `aiify_git_zwu66zfu/src/documents/matching.py` no longer exists ✓
- `_load_anomaly_cfg` present in `tools/monitor/log_analyzer.py` ✓
- `_load_anomaly_cfg` present in `icdev/tools/monitor/log_analyzer.py` mirror ✓
- `anomaly_detection` block in `args/monitoring_config.yaml` ✓

No competing implementation authored. Card moved to done with
`bypass_verification: true` + `bypass_reason`.

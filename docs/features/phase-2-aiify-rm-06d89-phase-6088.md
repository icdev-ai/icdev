<!-- CUI // SP-CTI -->

# Phase 2 — AI-ify Determination: aiify-rm-06d89-phase-6088

**Date:** 2026-06-05
**Roadmap:** rm-06d89040cf · **Scan:** 43 · **Opportunity:** 6088
**Pattern:** `hardcoded_threshold` → `anomaly_detection`
**External target:** `src/documents/views.py` (paperless-ngx shallow clone `aiify_git_zwu66zfu`)

## Determination: DUPLICATE of `dfb671f09` (MONITOR log_analyzer)

Opportunity 6088 points `module_path` at an external paperless-ngx clone the
AI-ify engine shallow-clones, scans, and deletes. The file is external and
unmodifiable by the time the kanban card runs. Per the established disposition,
external `hardcoded_threshold` → `anomaly_detection` opportunities are landed in
the analogous ICDEV internal subsystem — **MONITOR** (`tools/monitor/log_analyzer.py`),
not DIC or IQE. The pattern + paradigm decide the analog, not the filename.

This is an **exact sibling of 6083 and 6087** (same external `src/documents/views.py`,
same pattern/paradigm), both already closed as dups of `dfb671f09`. One external
file emits opps across multiple patterns (views.py also emitted fulltext_search
opps 6081/6082/6084/6085 → DIC); match on `pattern_type` to pick the analog.

## Verification at HEAD

- `dfb671f09` is an ancestor of HEAD (`56055cc12`).
- `_load_anomaly_cfg` present in `tools/monitor/log_analyzer.py` (def L477) and
  `icdev/tools/monitor/log_analyzer.py` mirror (def L300).
- z-score + robust MAD (Iglewicz-Hoaglin modified z-score) methods present
  (L527 docstring; config-driven).
- `anomaly_detection` config block present in `args/monitoring_config.yaml` (L91).
- `tests/test_log_analyzer_anomaly.py` — **23/23 pass**.

No new implementation required. Card closed with `bypass_verification: true` and
a `bypass_reason` naming `dfb671f09` as the source commit.

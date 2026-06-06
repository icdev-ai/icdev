# Phase 2 — aiify-rm-06d89-phase-6083 (Determination: duplicate of monitor anomaly_detection)

**Opportunity:** 6083 (scan_id 43, roadmap `rm-06d89040cf`)
**Pattern / paradigm:** `hardcoded_threshold` → `anomaly_detection`
**External module_path:** `…/aiify_git_zwu66zfu/src/documents/views.py` (paperless-ngx clone)
**Model recommendation:** claude-haiku-4-5-20251001

## Determination

Closed as a **duplicate** of the already-shipped MONITOR anomaly-detection work
(commit `dfb671f09`, ancestor of HEAD on `irad/feature`).

The `module_path` points at a temporary `aiify_git_zwu66zfu` shallow clone of an
external open-source repo (paperless-ngx) that the AI-ify engine clones, scans,
then deletes (`engine.py` `_clone_git_url` → `shutil.rmtree`). The file is gone
and was never part of this codebase. Per the established disposition for
`hardcoded_threshold → anomaly_detection` opportunities, the analogous **internal**
ICDEV subsystem is **MONITOR** (`tools/monitor/log_analyzer.py`) — pattern +
paradigm decide the analog, not the external filename. Note `src/documents/views.py`
emits several patterns: its `fulltext_search_engine → llm_generation` opps (6081,
6082, 6084, 6085) mapped to DIC `answer()`; this `hardcoded_threshold` opp maps to
MONITOR.

## Verification at HEAD

- `dfb671f09` **is an ancestor** of HEAD (`127dd8fca`, `irad/feature`).
- External clone `aiify_git_zwu66zfu/src/documents/views.py` — **GONE** (unmodifiable).
- `_load_anomaly_cfg` + config-driven z-score / robust MAD (modified z-score)
  present in **both** `tools/monitor/log_analyzer.py` (L477, L545–551) and the
  `icdev/tools/monitor/log_analyzer.py` mirror (L300, L370).
- `anomaly_detection` config block present in `args/monitoring_config.yaml` (L91).
- `tests/test_log_analyzer_anomaly.py` — **23/23 pass**.

No competing copy authored. Card moved to done with
`bypass_verification: true` + `bypass_reason`.

## Sibling history

Earlier `hardcoded_threshold → anomaly_detection` opps closed as dups of the same
`dfb671f09` impl: 5972, 5973, 5975, 5978 (log_parser), 5964, 5965, 5966, 5968
(aiify_git_b6mem203 clone), 6067, 6068, 6070 (paperless `src/documents/*`). 6083
follows the same disposition.

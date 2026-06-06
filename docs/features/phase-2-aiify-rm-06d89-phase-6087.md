# Phase 2 — AI-ify Determination: aiify-rm-06d89-phase-6087

**Opportunity:** 6087 (scan_id 43, roadmap `rm-06d89040cf`)
**Pattern:** `hardcoded_threshold` → `anomaly_detection`
**External module:** `…/aiify_git_zwu66zfu/src/documents/views.py` (paperless-ngx shallow clone)
**Disposition:** **Closed as duplicate of `dfb671f09`** (MONITOR `log_analyzer` anomaly detection).

## Rationale

The `module_path` points at a temporary `aiify_git_*` shallow clone of the
external paperless-ngx repo, which the AI-ify engine clones, scans, then
deletes. The clone (`aiify_git_zwu66zfu`) is already gone and the file is
external/unmodifiable. Per the established disposition, the AI-ification is
landed in the **analogous ICDEV internal subsystem** and the card is closed as
a dup.

For `hardcoded_threshold` → `anomaly_detection`, the analog subsystem is
**MONITOR** (`tools/monitor/log_analyzer.py`), not DIC/IQE. The pattern+paradigm
decide the analog, not the external filename (`views.py` here is the same as
the 6067/6068 `signals/handlers.py` siblings). This replaces inline z-score /
error-rate-spike constants with a config-driven `anomaly_detection` block plus a
robust MAD (modified z-score, Iglewicz-Hoaglin) method.

## Verification (HEAD `9c8001ee0`, irad/feature)

- `dfb671f09` **IS** an ancestor of HEAD.
- `_load_anomaly_cfg` + `zscore`/`mad` present in `tools/monitor/log_analyzer.py`
  (def L477, mad branch L545) and the `icdev/` mirror (3 occurrences).
- `anomaly_detection` block present in `args/monitoring_config.yaml` (L91).
- `tests/test_log_analyzer_anomaly.py`: **23/23 pass**.
- External clone `aiify_git_zwu66zfu` GONE.

## Outcome

Card `aiify-rm-06d89-phase-6087` moved to **done** with
`bypass_verification: true` (test-only/no-code change — determination doc only).

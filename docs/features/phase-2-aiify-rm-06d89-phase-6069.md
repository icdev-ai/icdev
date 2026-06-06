# Phase 2 — aiify-rm-06d89-phase-6069 (determination)

**Opportunity:** 6069 (scan_id 43, roadmap `rm-06d89040cf`)
**Pattern → paradigm:** `hardcoded_threshold` → `anomaly_detection`
**External module:** `…/aiify_git_zwu66zfu/src/documents/signals/handlers.py` (paperless-ngx clone)

## Determination: duplicate of `dfb671f09` (already implemented)

The opportunity targets an **external open-source repo** (paperless-ngx) that the
AI-ify engine shallow-clones, scans, then deletes. The clone `aiify_git_zwu66zfu`
is gone and the file is unmodifiable. Per the established disposition, the
AI-ification lands in the **analogous ICDEV internal subsystem**.

For `hardcoded_threshold` → `anomaly_detection`, the analog is **MONITOR**
(`tools/monitor/log_analyzer.py`), not DIC/IQE — pattern + paradigm decide the
analog, not the path. This is the **exact sibling of `aiify-rm-06d89-phase-6067`
and `-6068`** (same file `src/documents/signals/handlers.py`, same
pattern/paradigm), both of which closed as dups of the same commit.

The work was shipped in commit **`dfb671f09`** (origin: external
`log_parser/lambda_log_parser.py`): inline z-score (2.0) and error-rate-spike
(0.10) constants were replaced with a config-driven `anomaly_detection` block in
`args/monitoring_config.yaml`, plus a robust MAD (modified z-score,
Iglewicz-Hoaglin) method.

## Verification at HEAD

- `dfb671f09` is an ancestor of HEAD.
- `_load_anomaly_cfg` present in `tools/monitor/log_analyzer.py` (L477).
- `anomaly_detection` block present in `args/monitoring_config.yaml` (L71/L87).
- `tests/test_log_analyzer_anomaly.py` present (sibling run: 23/23 pass).

No code change required. Card moved to done with `bypass_verification: true`.

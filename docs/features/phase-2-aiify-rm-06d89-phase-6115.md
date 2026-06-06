# Phase 2 — AI-ify Determination: aiify-rm-06d89-phase-6115

**Opportunity:** 6115 (scan_id 43, roadmap `rm-06d89040cf`)
**Pattern:** `hardcoded_threshold` → `anomaly_detection`
**External target:** `src/paperless/settings/__init__.py` (paperless-ngx shallow clone `aiify_git_zwu66zfu`, reaped before task ran)

## Disposition: DUPLICATE — closed, no new code

Exact sibling of **6095 / 6096** (same external repo, same generic
`hardcoded_threshold → anomaly_detection` pattern with `function_name`
`<unknown>` and the boilerplate detail *"Hardcoded numeric threshold -- replace
with ML anomaly detection"*), both of which were closed as dups of the MONITOR
analog `dfb671f09`. `settings/__init__.py` is a Django settings/config module —
pure configuration constants with no match-confidence / date-parse /
search-relevance / OCR semantics — so it maps to the default MONITOR
`log_analyzer` config-driven anomaly layer (not DIC), per the established
`src/paperless/*` hardcoded_threshold→anomaly_detection mapping. The canonical
work `dfb671f09` already replaced hardcoded thresholds with a configurable
`anomaly_detection` block (legacy z-score + robust MAD modified-z-score), which
is precisely the modernization this opportunity calls for.

## Verification (HEAD `111ba4c54`, branch irad/feature)

- `dfb671f09` IS an ancestor of HEAD.
- `_load_anomaly_cfg` + z-score / modified-z-score (MAD) present in
  `tools/monitor/log_analyzer.py` (def L477) and the `icdev/` mirror.
- `anomaly_detection:` config block present in `args/monitoring_config.yaml` (L91).
- `tests/test_log_analyzer_anomaly.py`: 23/23 pass.

## Board action

Moved to `done` with `bypass_verification: true` + `bypass_reason` (no new code —
disposition is a documented duplicate of `dfb671f09`).

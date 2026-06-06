# Phase 2 — AI-ify Determination: aiify-rm-06d89-phase-6117

**Opportunity:** 6117 (scan_id 43, roadmap `rm-06d89040cf`)
**Pattern:** `hardcoded_threshold` → `anomaly_detection`
**External target:** `src/paperless/settings/__init__.py` (paperless-ngx shallow clone `aiify_git_zwu66zfu`, reaped before task ran)

## Disposition: DUPLICATE — closed, no new code

Third exact sibling from the **same scan** as **6115 / 6116** — identical external
repo (`aiify_git_zwu66zfu`), identical file (`src/paperless/settings/__init__.py`),
identical generic `hardcoded_threshold → anomaly_detection` pattern with
`function_name` `<unknown>` and the boilerplate detail *"Hardcoded numeric
threshold -- replace with ML anomaly detection"*. The scanner re-emits this same
Django settings module on every pass (historical family: 1849, 3161, 3763, 4188,
4610, 4774, 4973, 5156, 6115, 6116, 6117).

`settings/__init__.py` is a Django settings/config module — pure configuration
constants with no match-confidence / date-parse / search-relevance / OCR
semantics — so it maps to the default MONITOR `log_analyzer` config-driven
anomaly layer (not DIC), per the established `src/paperless/*`
hardcoded_threshold→anomaly_detection mapping. The canonical work `dfb671f09`
already replaced hardcoded thresholds with a configurable `anomaly_detection`
block (legacy z-score + robust MAD modified-z-score), which is precisely the
modernization this opportunity calls for. Siblings 6115 (`762b06628`) and 6116
(`96f31560e`) were closed the same way.

## Verification (HEAD `96f31560e`, branch irad/feature)

- `dfb671f09` IS an ancestor of HEAD.
- Siblings 6115 / 6116 closed as dups of `dfb671f09` in `762b06628` / `96f31560e`.
- `_load_anomaly_cfg` + z-score / modified-z-score (MAD) present in
  `tools/monitor/log_analyzer.py` and the `icdev/` mirror.
- `anomaly_detection:` config block present in `args/monitoring_config.yaml`.

## Board action

Moved to `done` with `bypass_verification: true` + `bypass_reason` (no new code —
disposition is a documented duplicate of `dfb671f09` and of same-scan siblings
6115 / 6116).

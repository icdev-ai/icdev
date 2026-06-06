# Phase 2 — AI-ify rm-06d89 / Opportunity 6119 (Determination)

**Task:** `aiify-rm-06d89-phase-6119`
**Roadmap:** `rm-06d89040cf` · **Scan:** 43 · **Opportunity:** 6119
**Pattern → Paradigm:** `hardcoded_threshold` → `anomaly_detection`
**Reported module_path:** `C:\Users\schuo\AppData\Local\Temp\claude\aiify_git_zwu66zfu\src\paperless\validators.py`

## Determination: DUPLICATE of `dfb671f09`

The reported `module_path` is a temporary shallow clone of the **external** open-source
paperless-ngx repository (`aiify_git_*`), which the AI-ify engine clones, scans, and then
deletes. The file is not part of the ICDEV codebase and is unmodifiable by the time the
kanban card runs.

Per the established disposition for external-repo opportunities, AI-ification lands in the
**analogous ICDEV internal subsystem**. For `hardcoded_threshold` → `anomaly_detection`
the analog is the **Monitor** subsystem's log analyzer (`tools/monitor/log_analyzer.py`),
not DIC/IQE. The pattern + paradigm decide the analog, not the external filename.

This work was already implemented in commit **`dfb671f09`** (merged to main via PR #27,
`0a6a80116`), and opportunity 6119 is a re-emission of the same `src/paperless/*` +
`hardcoded_threshold`→`anomaly_detection` shape already closed for siblings 6067/6068/6071/
6083/6087/6088/6121.

## Verification at HEAD (`5776f8e2c`)

- `dfb671f09` **is an ancestor of HEAD**.
- `_load_anomaly_cfg` + `zscore`/`mad` (Iglewicz-Hoaglin modified z-score) present in:
  - `tools/monitor/log_analyzer.py` (`_load_anomaly_cfg` L477, `mad` branch L545)
  - `icdev/tools/monitor/log_analyzer.py` mirror (`_load_anomaly_cfg` L300)
- `anomaly_detection` config block present in `args/monitoring_config.yaml` (L91), with
  `method`, `z_threshold` (2.0), and `mad_threshold` (3.5) — the previously hardcoded
  z-score / error-rate spike constants are now config-driven.
- `tests/test_log_analyzer_anomaly.py` — **23/23 pass**.

No new code required. Card moved to done with `bypass_verification: true` and a
`bypass_reason` naming this determination.

# AI-ify Determination: aiify-rm-a3344-phase-85

**Roadmap:** rm-a334408112  
**Opportunity ID:** 85  
**Scan ID:** 1  
**Pattern:** hardcoded_threshold → anomaly_detection  
**External file:** src/documents/signals/handlers.py (paperless temp clone — deleted)  
**Disposition:** DUPLICATE — closed, no new code authored

## Determination

The external module path (`C:\Users\schuo\AppData\Local\Temp\claude\aiify_git_5cc2wcba\src\documents\signals\handlers.py`) points to a paperless-ngx shallow clone deleted by the scanner after each run. The file is unmodifiable.

**Pattern mapping:** `hardcoded_threshold`→`anomaly_detection` for paperless `src/documents/signals/handlers.py` maps to **MONITOR log_analyzer** (`tools/monitor/log_analyzer.py`), not DIC. This is the same disposition as sibling opp 86 (closed in commit `92c50577b`), which also targeted this file and was determined to be a dup of commit `dfb671f09`.

The scanner emits multiple opportunities per source file (different function line ranges, `function_name: <unknown>`), producing near-identical opps 85 and 86 from the same file and roadmap scan. Both resolve to the same already-shipped internal analog.

## Verification

- `_load_anomaly_cfg` present in `tools/monitor/log_analyzer.py` (L477)
- `zscore` + `mad` methods present (L536–L579)
- `anomaly_detection` block present in `args/monitoring_config.yaml`
- 23/23 `tests/test_log_analyzer_anomaly.py` pass at HEAD

## Internal Analog

`tools/monitor/log_analyzer.py` — config-driven anomaly detection replacing hardcoded z-score (2.0) and error-rate spike (0.10) constants with a tunable `anomaly_detection` block in `args/monitoring_config.yaml`. Supports both `zscore` (mean/stdev) and `mad` (Iglewicz-Hoaglin modified z-score) methods.

Committed: `dfb671f09` (irad/feature), merged via PR #27 (`0a6a80116`).

## Sibling Opps

- **Opp 86** (same file, same scan) — closed as dup of `dfb671f09` in `92c50577b`
- **Opps 6067, 6068, 6071** — previously closed as dups of the same commit

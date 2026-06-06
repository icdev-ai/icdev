<!-- CUI // SP-CTI -->
# Phase 2 — AI-ify Determination: aiify-rm-06d89-phase-6050

- **Roadmap:** `rm-06d89040cf`
- **Scan ID:** 43
- **Opportunity ID:** 6050
- **Pattern:** `hardcoded_threshold` → `anomaly_detection`
- **External module:** `src/documents/plugins/date_parsing/base.py` (paperless-ngx, shallow-cloned into `C:\Users\schuo\AppData\Local\Temp\claude\aiify_git_zwu66zfu\...`)

## Determination: DUPLICATE of `dfb671f09` (no new code)

The `module_path` points at a temp `aiify_git_zwu66zfu` clone of the external
paperless-ngx repo, which the AI-ify engine shallow-clones, scans, then deletes.
The clone is **gone** (`CLONE-MISSING`) and the file is external/unmodifiable
regardless. Per the established disposition for external-repo opps, the
AI-ification is landed in the **analogous ICDEV internal subsystem**.

For paperless `src/documents/*` files with pattern `hardcoded_threshold` →
`anomaly_detection`, the analog is the **MONITOR log analyzer**
(`tools/monitor/log_analyzer.py`), which already replaced inline z-score /
error-rate-spike constants with a config-driven anomaly-detection block (z-score
+ robust MAD). `date_parsing/base.py` is a generic threshold (date-parse
fuzziness), not a match-confidence cutoff, so it maps to MONITOR — not the DIC
`_is_confident_match` path that `matching.py` uses.

### Verification at HEAD `42fd7b6cd` (irad/feature)
- `dfb671f09` is an ancestor of HEAD — **ANCESTOR-YES**
- `_load_anomaly_cfg` present in `tools/monitor/log_analyzer.py` (def L477) and
  in the `icdev/` mirror `icdev/tools/monitor/log_analyzer.py` (def L300)
- `anomaly_detection` block present in `args/monitoring_config.yaml` (L91) with
  `method: zscore` (z_threshold 2.0) and `mad_threshold 3.5`
- `tests/test_log_analyzer_anomaly.py` — **23/23 pass**

No production code change required. Card moved to **done** with
`bypass_verification: true` + `bypass_reason` naming this determination.

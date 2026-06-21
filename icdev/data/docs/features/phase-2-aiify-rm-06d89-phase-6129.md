# AI-ify Determination — aiify-rm-06d89-phase-6129 (opp 6129)

**Date:** 2026-06-06
**Disposition:** Closed as **duplicate** of `dfb671f09` (MONITOR `anomaly_detection`) — no new code.
**Move:** `bypass_verification:true` (external-repo opp; impl already landed in the analogous ICDEV subsystem).

## Opportunity

- **Roadmap:** `rm-06d89040cf`, scan_id 43, opportunity_id 6129
- **External module:** `src/paperless_mail/mail.py` (paperless-ngx mail consumer)
- **Pattern → paradigm:** `hardcoded_threshold` → `anomaly_detection`
- **Ask:** "Add a utility function that calculates the anomaly score/probability from current input data and returns it against a configurable limit."

## Why this is a duplicate

The target file lives in a temp shallow-clone (`aiify_git_zwu66zfu`) that the AI-ify engine clones, scans, then deletes — by run time the file is gone and unmodifiable anyway (see [[aiify-external-repo-opps-land-in-dic]]). The established disposition is to land the AI-ification in the **analogous internal ICDEV subsystem**, then close re-emissions as dups.

`src/paperless_mail/mail.py` is a **generic mail-fetch/consumer** module with no match-confidence, search-relevance, or date-parsing semantics. Per the pattern→analog mapping, generic `hardcoded_threshold → anomaly_detection` opps map to **MONITOR `tools/monitor/log_analyzer.py`**, not DIC. The DIC divergences (matching.py → `_is_confident_match`, search/* → `detect_search_anomalies`, date_parsing/* → `assess_document_dates`, chat.py → `_assess_grounding`) do not apply here.

The faithful "configurable-threshold anomaly score vs a limit" capability already shipped in `dfb671f09`:

- `_load_anomaly_cfg()` — config-driven thresholds (no magic constants), from `args/monitoring_config.yaml` `anomaly_detection:` block
- z-score (mean/std-dev) **and** robust MAD (Iglewicz-Hoaglin modified z-score) detection methods returning a per-count anomaly score against a configured threshold

## Verification at HEAD

- HEAD `bada9f3d4`; `dfb671f09` is an ancestor ✓
- Clone `aiify_git_zwu66zfu/src/paperless_mail/mail.py` GONE/unmodifiable ✓
- `_load_anomaly_cfg` + zscore/MAD present in `tools/monitor/log_analyzer.py` (def L477, mad L545) and `icdev/tools/monitor/log_analyzer.py` mirror (def L300) ✓
- `anomaly_detection:` block in `args/monitoring_config.yaml` (L91) ✓
- 23/23 `tests/test_log_analyzer_anomaly.py` pass ✓

## Conclusion

No competing implementation authored. Closed as dup of `dfb671f09`. Future `src/paperless_mail/*` `hardcoded_threshold → anomaly_detection` siblings: verify the MONITOR anomaly config/methods exist, close as dup.

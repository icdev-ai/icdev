# AI-ify Determination — aiify-rm-a3344-phase-146 (opp 146)

**Date:** 2026-06-15
**Disposition:** Closed as **duplicate** of `dfb671f09` (MONITOR `anomaly_detection`) — no new code.
**Move:** `bypass_verification:true` (external-repo opp; impl already landed in the analogous ICDEV subsystem).

## Opportunity

- **Roadmap:** `rm-a334408112`, scan_id 1, opportunity_id 146
- **External module:** `src/paperless_mail/mail.py` (paperless-ngx mail consumer)
- **Pattern → paradigm:** `hardcoded_threshold` → `anomaly_detection`
- **Ask:** Add anomaly detection over mail.py hardcoded thresholds (poll windows, attachment-size limits, retry/backoff counts).

## Why this is a duplicate

The target file lives in a temp shallow-clone (`aiify_git_5cc2wcba`) that the AI-ify engine clones, scans, then deletes — by run time the file is gone and unmodifiable (see [[aiify-external-repo-opps-land-in-dic]]). The established disposition is to land AI-ification in the **analogous internal ICDEV subsystem**, then close re-emissions as dups.

`src/paperless_mail/mail.py` is a **generic mail-fetch/consumer** module with no match-confidence, search-relevance, or date-parsing semantics. Per the pattern→analog mapping (see [[aiify-mail-py-monitor-dup-dfb671f09]]), generic `hardcoded_threshold → anomaly_detection` opps from `paperless_mail/mail.py` map to **MONITOR `tools/monitor/log_analyzer.py`**, not DIC. Prior siblings (opps 6125–6129, same file) were all closed as dups of `dfb671f09`.

The faithful "configurable-threshold anomaly score vs a limit" capability already shipped in `dfb671f09`:

- `_load_anomaly_cfg()` — config-driven thresholds (no magic constants), from `args/monitoring_config.yaml` `anomaly_detection:` block
- z-score (mean/std-dev) **and** robust MAD (Iglewicz-Hoaglin modified z-score) detection methods returning a per-count anomaly score against a configured threshold

## Conclusion

No competing implementation authored. Closed as dup of `dfb671f09`. This is a re-emission of the same `src/paperless_mail/mail.py` `hardcoded_threshold → anomaly_detection` family (opps 6125–6129) from a new aiify scan run under roadmap `rm-a3344`.

# Phase 2 — AI-ify determination: aiify-rm-06d89-phase-6049

**Opportunity:** 6049 (scan_id 43, roadmap `rm-06d89040cf`)
**Pattern:** `hardcoded_threshold` → `anomaly_detection`
**External module:** `…/aiify_git_zwu66zfu/src/documents/plugins/date_parsing/base.py` (paperless-ngx)

## Determination: duplicate of opp 6042 — no new code

The `module_path` points into a temporary `aiify_git_*` shallow clone of the
external paperless-ngx repo that the AI-ify engine clones, scans, then deletes
(`engine.py:_clone_git_url` → `shutil.rmtree`). The file is third-party and
unmodifiable; the AI-ification lands in the **analogous ICDEV internal
subsystem**.

`date_parsing/base.py` is a **date/temporal** parser. The right internal analog
for a date-semantic `hardcoded_threshold → anomaly_detection` opp is the
**Document Intelligence Canvas freshness engine**
(`tools/document_intelligence/freshness_engine.py`) — the same analog that the
sibling `src/documents/models.py` opps 6041/6042 landed on (date/recency-based
staleness + statistical outlier anomaly detection). This is NOT the generic
MONITOR `log_analyzer` catch-all (used for log-metric-drift files like
`handlers.py`/`views.py`/`validators.py`); the *filename semantics* (date
parsing) tip it to the freshness analog, consistent with the 6041/6042 mapping.

### Verified at HEAD (`0e08a3d07`, irad/feature — the 6042 commit itself)

`tools/document_intelligence/freshness_engine.py` carries the full
config-driven anomaly detection from opp 6042:
- `_FRESH_THRESHOLD` / `_STALE_THRESHOLD` named constants replacing the inline
  `0.35` / `0.70` cutoffs (L32–33); `_ANOMALY_ABS_FLOOR` guard (L45)
- `_classify_state(score)` pure classifier (L104)
- `_heuristic_anomaly_severity(anomaly_count, total)` deterministic severity (L117)
- statistical collection-relative outlier detection wired into the scan
- LLM grade `_ai_freshness_anomaly_severity` via router key
  `dic_freshness_anomaly_severity` (L216/L259)

Tests `tests/test_dic_freshness_anomaly.py` — **24/24 pass**.

### Disposition

No new code. Opp 6049's intent — replace a hardcoded date/temporal threshold
with statistical + AI anomaly detection — is already fulfilled by opp 6042 on
the DIC freshness engine. Card moved to `done` with `bypass_verification:true`
and a `bypass_reason` naming this determination (external file, dup of 6042).

Pattern siblings on DIC for `hardcoded_threshold → anomaly_detection`:
6090 (analytics severity), 6034 (match-confidence gate), 6042/6041
(freshness state cutoffs + outliers) — this 6049 joins 6041 as a freshness dup.

# Phase 2 — AI-ify determination: aiify-rm-06d89-phase-6061

**Opportunity:** 6061 (scan_id 43, roadmap `rm-06d89040cf`)
**Pattern:** `hardcoded_threshold` → `anomaly_detection`
**External module:** `…/aiify_git_zwu66zfu/src/documents/serialisers.py` (paperless-ngx)

## Determination: duplicate of opp 6042 — no new code

The `module_path` points into a temporary `aiify_git_*` shallow clone of the
external paperless-ngx repo that the AI-ify engine clones, scans, then deletes
(`engine.py:_clone_git_url` → `shutil.rmtree`). The file is third-party and
unmodifiable (the clone was still present at run time but external); the
AI-ification lands in the **analogous ICDEV internal subsystem**.

`src/documents/serialisers.py` is the same paperless file that emitted the
prior `hardcoded_threshold → anomaly_detection` siblings **6056** and **6060**,
both of which were closed as duplicates of **opp 6042**. The right internal
analog for a document state/recency `hardcoded_threshold → anomaly_detection`
opp is the **Document Intelligence Canvas freshness engine**
(`tools/document_intelligence/freshness_engine.py`) — the same analog the
sibling `src/documents/models.py` opps 6041/6042 landed on (state cutoffs +
statistical collection-relative outlier anomaly detection). 6061 is an exact
re-emission of that cluster (6034/6041/6042/6056/6060/6090 → one DIC impl).

### Verified at HEAD (`11ceec6ff`, irad/feature)

The 6042 commit `0e08a3d07` (`feat(aiify-opp-6042): AI-ify DIC freshness
thresholds -> anomaly detection`) is in the tree;
`tools/document_intelligence/freshness_engine.py` carries the full
config-driven anomaly detection:
- `_FRESH_THRESHOLD` / `_STALE_THRESHOLD` named constants replacing the inline
  `0.35` / `0.70` cutoffs (L32–33); `_ANOMALY_ABS_FLOOR` guard (L45)
- `_classify_state(score)` pure classifier (L104)
- `_heuristic_anomaly_severity(anomaly_count, total)` deterministic severity (L117)
- statistical collection-relative outlier detection wired into the scan
- LLM grade `_ai_freshness_anomaly_severity` via router key
  `dic_freshness_anomaly_severity` (L216)

Tests `tests/test_dic_freshness_anomaly.py` — **24/24 pass**.

### Disposition

No competing copy authored. Card moved to **done** with `bypass_verification:
true` + `bypass_reason` (a no-code dup closure has no `kanban_verifications`
row). Per CLAUDE.md no-magic-constant rule, no synthetic `scan_id`/threshold
constants were introduced against the deleted external file.

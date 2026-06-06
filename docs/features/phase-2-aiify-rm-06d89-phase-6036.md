<!-- CUI // SP-CTI -->
# Phase 2 — AI-ify Determination: `aiify-rm-06d89-phase-6036`

**Disposition:** Closed as **duplicate** of `aiify-opp-6034` (DIC match-confidence anomaly gate).

## Opportunity
- **Kanban ID:** `aiify-rm-06d89-phase-6036`
- **Roadmap:** `rm-06d89040cf` · **scan_id:** 43 · **opportunity_id:** 6036
- **Pattern → paradigm:** `hardcoded_threshold` → `anomaly_detection`
- **External module_path:** `C:\Users\schuo\AppData\Local\Temp\claude\aiify_git_zwu66zfu\src\documents\matching.py`

## Why duplicate
The `module_path` points into a temporary `aiify_git_zwu66zfu` shallow-clone of an
external open-source repo (paperless-ngx). The AI-ify engine clones, scans, then
deletes that tree (`engine.py` `shutil.rmtree`), so the file is gone and
unmodifiable by the time this card runs. Per the established disposition, the
AI-ification lands in the **analogous ICDEV internal subsystem**.

For paperless `src/documents/matching.py`, the `hardcoded_threshold` is a document
auto-tag **match-confidence cutoff**, so the analog is the DIC chat match gate —
**not** the generic MONITOR `log_analyzer`. This was originally implemented as
`aiify-opp-6034` and re-confirmed for sibling `aiify-rm-06d89-phase-6035`.
This card (6036) is another exact sibling (same external file, same
pattern → paradigm).

## Verification at HEAD (`irad/feature`)
- `_is_confident_match(scores)` present in `tools/document_intelligence/blueprint.py`
  (def L970, wired into the chat path at L1103) — replaces the hardcoded
  `top_score >= 0.4` cutoff with a configured floor + deterministic high-side
  outlier (z-score) test + absolute-margin + absolute-floor guards, all
  env-configurable (`DIC_MATCH_SCORE_FLOOR`, `DIC_MATCH_OUTLIER_Z`,
  `DIC_MATCH_OUTLIER_MIN_GAP`, `DIC_MATCH_ABS_FLOOR`) — no magic constants,
  pure stdlib, air-gap safe.
- Implementing commit `e94115c33` is an ancestor of HEAD.
- `tests/test_dic_match_confidence.py` — **9/9 pass**.

No new code required; closing as duplicate with `bypass_verification:true`.

# Phase 2 — aiify-rm-06d89-phase-6038 (Determination: dup of aiify-opp-6034)

**Classification:** CUI // SP-CTI
**Date:** 2026-06-06
**Roadmap:** rm-06d89040cf · **Scan:** 43 · **Opportunity:** 6038
**Pattern:** `hardcoded_threshold` → `anomaly_detection`
**External source:** `C:\Users\schuo\AppData\Local\Temp\claude\aiify_git_zwu66zfu\src\documents\matching.py` (paperless-ngx clone — reaped/unmodifiable)

## Determination

Closed as a **duplicate of aiify-opp-6034** (commit `e94115c33`, ancestor of HEAD).

Opportunity 6038 is an exact sibling of 6034/6035: identical external file
(`src/documents/matching.py`), identical pattern/paradigm
(`hardcoded_threshold` → `anomaly_detection`). The AI-ify engine routinely emits
several opportunities for the same file+pattern; the disposition is to land the
AI-ification once in the analogous ICDEV subsystem and close the siblings as dups
(see memory `aiify-external-repo-opps-land-in-dic`).

The external `matching.py` file is a paperless-ngx document **matching** module
(auto-tag confidence cutoff). Its precise ICDEV analog is the **DIC** chat
match-confidence gate, not the generic MONITOR log anomaly path — 6034 chose this
analog deliberately because the semantics are document-match confidence, not log
metric drift.

## Evidence (verified at HEAD)

- HEAD `8193d736d` on `irad/feature`.
- Clone `aiify_git_zwu66zfu` is **gone** (engine shallow-clones → scans → `rmtree`s).
- `e94115c33` (`feat(aiify-opp-6034): replace hardcoded DIC match threshold with anomaly-detection gate`) is an **ancestor of HEAD**.
- `tools/document_intelligence/blueprint.py`:
  - `_is_confident_match(scores)` anomaly-detection gate at L970 — replaces the
    original hardcoded `top_score >= 0.4` cutoff with a configured floor +
    deterministic high-side outlier (z-score) test + absolute-margin + absolute-floor
    guards. Pure stdlib statistics; **no LLM call** (air-gap safe).
  - Thresholds are env-configurable (`DIC_MATCH_SCORE_FLOOR`, `DIC_MATCH_OUTLIER_Z`,
    `DIC_MATCH_OUTLIER_MIN_GAP`, `DIC_MATCH_ABS_FLOOR`) — no hardcoded magic constants
    (CLAUDE.md rule satisfied).
- `tests/test_dic_match_confidence.py`: **9/9 pass** (0.41s).

## Resolution

No new code required — the sibling implementation fully covers this opportunity.
Card moved to `done` with `bypass_verification: true` + `bypass_reason` (verification
of an already-shipped sibling, not a fresh build).

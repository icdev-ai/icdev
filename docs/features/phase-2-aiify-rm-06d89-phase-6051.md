<!-- CUI // SP-CTI -->
# AI-ify Determination — aiify-rm-06d89-phase-6051 (opportunity 6051)

- **Roadmap:** `rm-06d89040cf`  **Scan:** 43  **Opportunity:** 6051
- **Phase:** Phase 2 — Core Modernization
- **Pattern:** `hardcoded_threshold` → `anomaly_detection`
- **External target:** `src/documents/search/_backend.py` (paperless-ngx shallow clone
  `C:\Users\schuo\AppData\Local\Temp\claude\aiify_git_zwu66zfu\...`)

## Disposition: CLOSED AS DUPLICATE of opp 6034

The `module_path` points at a temporary `aiify_git_*` clone of an **external**
open-source repo (paperless-ngx) that the AI-ify engine shallow-clones, scans, then
deletes. The clone was already reaped at execution time (`GONE`), so the file is
external and unmodifiable. Per the established disposition for these opps, the
AI-ification is landed in the **analogous ICDEV internal subsystem** and the card is
closed as a duplicate when that work already exists.

`src/documents/search/_backend.py` is a **document search backend** whose
`hardcoded_threshold` is a relevance/match-confidence score cutoff. The matching
ICDEV analog is the **DIC search-results confidence gate** `_is_confident_match`
(opportunity **6034**), which replaces a hardcoded `score >= 0.4` cutoff with a
configured floor + deterministic high-side outlier (z-score) test + absolute-margin
and absolute-floor guards over the score distribution — exactly the anomaly-detection
upgrade this opportunity describes, applied to the DIC search path.

### Verification (at HEAD `b4fdbe303`, branch `irad/feature`)
- `_is_confident_match` present in `tools/document_intelligence/blueprint.py`
  (def L970), used in the search-results confidence path (L1103).
- Env-configurable, no magic constants: `DIC_MATCH_SCORE_FLOOR` (L955),
  `DIC_MATCH_OUTLIER_Z` (L958), plus `DIC_MATCH_OUTLIER_MIN_GAP` / `DIC_MATCH_ABS_FLOOR`.
- Implementing commit `e94115c33` is an ancestor of HEAD.
- `tests/test_dic_match_confidence.py` — **9/9 pass**.

No new code required. The opportunity's intent is fulfilled by 6034. Card moved to
`done` with `bypass_verification: true` + `bypass_reason` naming commit `e94115c33`.

<!-- CUI // SP-CTI -->

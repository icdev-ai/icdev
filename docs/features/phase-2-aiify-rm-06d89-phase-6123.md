<!-- CUI -->
# Phase 2 — DIC chat-grounding anomaly assessment (aiify-opp-6123)

**Opportunity:** 6123 — `paperless_ai/chat.py` `hardcoded_threshold` → `anomaly_detection`
**Roadmap:** rm-06d89040cf · **Scan:** 43 · **Phase:** Phase 2 — Core Modernization

## Determination

The opportunity's `module_path` points at a temp clone
(`...\Temp\claude\aiify_git_zwu66zfu\src\paperless_ai\chat.py`) of the external
**paperless** repo that the AI-ify engine clones and deletes per scan (the path
is gone). Per established practice, the analogous **ICDEV** subsystem is
AI-ified instead: paperless `chat.py` (RAG/Q&A over documents) maps to the
**Document Intelligence Canvas** `DICSearchEngine.answer()` grounded-answer path.

This is **not** a duplicate of the search-relevance anomaly detector (opp 6052).
That detector grades the *search* result set; the **chat/answer** path consumed
the top-k excerpts **unconditionally** and never looked at the relevance signal.
The genuine gap was that a grounded answer could be synthesized over a relevance
cliff (weak best hit / noise tail) with no indication to the caller.

## Change (reuse-first — no duplicated anomaly logic)

`tools/document_intelligence/search_engine.py`:

- `DICAnswer` gains `grounding_quality: dict` (compact deterministic summary:
  `severity`, `low_confidence`, `weak_count`, `anomaly_count`, `mean`,
  `top_score`) and `weak_grounding: bool` (the actionable flag). Both surfaced
  in `to_dict()`.
- `DICSearchEngine.answer()` now calls a new `_assess_grounding(query, used)`
  helper that runs the **existing** `detect_search_anomalies(...)` (built for
  opp 6052) with `use_llm=False` over the **actual excerpts used**, replacing the
  implicit "top-k is good enough" assumption with a statistical assessment.
  `weak_grounding` is True when the deterministic severity is medium/high or the
  best hit is itself weak. The signal is **advisory** — it never changes whether
  the LLM answers, never adds model cost, is air-gap safe, and never raises into
  the answer path. Reported on every evidence-bearing return (grounded answer,
  insufficient-evidence refusal, llm-unavailable degradation).

## Tests

`tests/test_dic_search_answer.py` (+4, `to_dict` shape updated): strong grounding
not flagged; weak grounding flagged (low-confidence best hit); grounding still
reported on model refusal; no-evidence path has empty grounding. Full file 15/15
pass; `test_dic_search_anomaly.py` 34/34 pass (reused detector unregressed);
ruff clean.
<!-- CUI -->

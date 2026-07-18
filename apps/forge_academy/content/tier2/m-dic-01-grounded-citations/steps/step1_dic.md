---
ontology_id: icdev:mission:m-dic-01-grounded-citations:step:1
step_class: icdev:Lab
---

# Document Intelligence — Grounded Citations

The Document Intelligence Canvas (**DIC**) turns a pile of documents into answers you
can defend. Its pipeline is:

```
dic_ingest  →  dic_search  →  dic_generate / dic_chat  →  HITL review
```

- `dic_ingest` — chunk + embed a document into DIC's own RAG + knowledge graph.
- `dic_search` — retrieve the evidence chunks relevant to a question.
- `dic_generate` / `dic_chat` — draft an answer **with inline `[source: <id>]` markers**.
- **HITL review** — a human approves before the artifact is promoted or exported.

## Why citations are load-bearing

Every LLM-generated artifact in ICDEV must carry inline `[source: …]` citations that
resolve to real evidence, plus a persisted provenance record — this is a TRUST
invariant enforced across the platform. The shared implementation lives in
`tools/quality/citation_grounding.py` (`parse_citations`, `validate_citations`,
`citation_gate`), used by the DIC engine in `tools/document_intelligence/`. The
**`citation_gate`** (the "citation guard") blocks promote/export on citation defects,
exactly mirroring the `placeholder`/`content_grounding` gate. Confidence bands drive
review: ≥0.70 include, 0.40–0.69 include **but flag for HITL**, <0.40 abstain (make no
claim). A reviewer can force past a defect, but that requires an explicit HITL override
**and** an audit record — the default gate **fails closed**.

An "ungrounded" citation — a `[source: X]` whose `X` was never ingested — is how
hallucinations sneak into a deliverable. The guard's whole job is to catch them.

## What you'll build

A miniature citation guard, the same shape as the real one, using the stdlib `re`:

1. `extract_citations()` — parse every `[source: id]` marker out of generated text.
2. `validate_citations()` — check each cited id against the evidence set; report which
   are grounded and which are not.
3. `citation_guard()` — the gate: return `passed=False` with human-readable defects
   when any claim is uncited or any citation is ungrounded (fail closed).

Open `step1_starter.py` and implement the three `TODO`s. Do not re-implement citation
parsing in real code — build on `tools/quality/citation_grounding.py`; this lab just
teaches the contract it enforces.

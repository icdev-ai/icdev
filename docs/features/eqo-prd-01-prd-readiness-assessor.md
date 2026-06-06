<!-- CUI // SP-CTI -->
# EQO-PRD-01 — PRD AI-Readiness Assessor

**Module:** `tools/aiify/prd_readiness_assessor.py`
**Tests:** `tests/test_aiify_prd_readiness.py` (4 passing)
**Task:** eqo-prd-01 (EQO — Ecosystem Quality & Observability)

## What it does

Shift-left companion to the AI-ify code scanner. Instead of detecting
AI-augmentable patterns in *code* after the fact, it scores how AI-ready a
**PRD/requirements session** is and infers which AI-augmentation opportunities
the requirements text implies — *before* a single line is built.

```python
from tools.aiify.prd_readiness_assessor import assess_prd_for_ai_readiness
result = assess_prd_for_ai_readiness(session_id, conn, use_llm=False)
# -> {score, components, gaps, opportunities, overall_ai_readiness, recommendations}
```

Reads `intake_sessions` (impact level, context summary) + `intake_requirements`
(raw/refined text, acceptance criteria) for the session.

## Six deterministic components (weights sum to 1.0)

| Component | Weight | Signal |
|-----------|--------|--------|
| `ai_mention_density` | 0.20 | Does the PRD actually ask for AI? (keyword density / # requirements) |
| `data_requirement_specificity` | 0.20 | Is training/grounding data described (source, volume, labels, schema)? |
| `integration_clarity` | 0.15 | Are the systems/APIs the AI plugs into named? |
| `acceptance_criteria_quantifiability` | 0.20 | Fraction of requirements with measurable success criteria |
| `governance_declarations` | 0.15 | HITL / audit / CUI / bias declared (reuses `ai_governance_scorer`) |
| `il_model_appropriateness` | 0.10 | Is an IL-appropriate model reachable? (IL6/air-gap needs a local model) |

## Opportunity inference

Requirement prose is matched against a phrase → `pattern_type` rule table, then
each match flows through the **shared** `opportunity_scorer.score_and_assess`, so
PRD-stage opportunities are scored on the exact same value/feasibility/risk model
as code-stage ones. Examples:

- "classify …" → `manual_classification_ui` (ml_classifier)
- "route … documents" → `document_routing` (decision_agent)
- "extract X from …" → `regex_user_input` (nlp_extractor)
- "generate report/email" → `string_template_rendering` (llm_generation)
- "threshold / alert when" → `hardcoded_threshold` (anomaly_detection)
- "search by keyword" → `keyword_list_search` (embedding_search)

A pure CRUD PRD matches no AI phrases → `overall_ai_readiness = "not_suitable"`
("don't AI-ify for the sake of it").

## Air-gap safety / LLM

The deterministic path is **always** the result. `use_llm=True` adds an LLM
Router pass (same pattern as `pattern_classifier.py`) that appends `[AI-suggested]`
recommendations; any failure (no provider, network, parse) degrades silently to
the deterministic output.

## CLI

```bash
python tools/aiify/prd_readiness_assessor.py --session-id sess-abc --json
python tools/aiify/prd_readiness_assessor.py --session-id sess-abc --use-llm
```
<!-- CUI // SP-CTI -->

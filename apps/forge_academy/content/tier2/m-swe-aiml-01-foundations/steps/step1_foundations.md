---
ontology_id: icdev:mission:m-swe-aiml-01-foundations:step:1
step_class: icdev:Lesson
---

# Foundation Model Selection — AIMC Fundamentals

## What you'll learn

The AI/ML Model Canvas (AIMC) is your design surface for the full foundation model lifecycle — from model selection through deployment and governance. Before you build, you need to understand the catalog.

## The Model Taxonomy

Every catalog entry (`FOUNDATION_MODELS` in `tools/aiml_canvas/constants.py`) carries a `type`. The catalog currently uses four:

| Type | Use Case | Example in the catalog |
|------|----------|---------|
| **llm** | Text generation, instruction following, Q&A | Qwen3 (Local), GPT-4o (Azure OpenAI), Gemini 1.5 Pro (Vertex AI) |
| **vlm** | Vision + language (charts, maps, docs) | LLaVA-Phi3 Vision (Local) |
| **embedding** | Semantic search, RAG vector store | Nomic Embed Text, text-embedding-3-large (Azure OpenAI) |
| **code** | Code generation, review, completion | Granite 20B Code (watsonx.ai), CodeLlama 34B Instruct |

Filter by type with `GET /ai-ml/api/models?type=embedding`. Classifiers for intent routing or PII detection are usually small distilled models you bring yourself; the catalog has no `classifier` entries.

Each entry also lists `il_suitability` (the IL levels it may run at, as integers, e.g. `[2, 4, 5, 6]`) and `air_gap_ready`.

## IL Selection Matrix

| IL Level | Allowed Providers | Air-Gap Required |
|----------|-------------------|-----------------|
| IL2 | All CSPs + HuggingFace + Local | No |
| IL4 | AWS Bedrock, Azure Gov, OCI GovCloud, IBM GovCloud, Local | No |
| IL5 | Local (Ollama) only | Yes |
| IL6 | Local (Ollama) only | Yes — NSA Type 1 |

## Your Mission

Call the AIMC model catalog API and answer the 5 questions below.

> **Where this runs.** This is a reading step: run the script from your own terminal
> against your own ICDEV instance (the Academy sandbox has no network). The AIMC canvas
> is mounted at `/ai-ml` (`tools/aiml_canvas/blueprint.py`). If auth is on, send a
> dashboard API key as `Authorization: Bearer icdev_dash_...`; the POST routes also
> require a role allowed to edit AIMC designs.


```python
import requests

BASE = "http://localhost:5050"

# 1. Get all models
r = requests.get(f"{BASE}/ai-ml/api/models")
models = r.json()

# 2. Get models ranked for IL4 (only models whose il_suitability includes 4, best first)
r4 = requests.get(f"{BASE}/ai-ml/api/models/rank?il_level=IL4")
ranked_il4 = r4.json()

# 3. Get models ranked for IL6
r6 = requests.get(f"{BASE}/ai-ml/api/models/rank?il_level=IL6")
ranked_il6 = r6.json()
```

## Questions (work them out from the API output; this step has no answer box)

1. How many total models are in the catalog?
2. How many models support IL6? (count entries whose `il_suitability` contains `6`)
3. Which provider has the most models?
4. What is the top-ranked model for IL4 and why?
5. What is the top-ranked model for IL6 and why?

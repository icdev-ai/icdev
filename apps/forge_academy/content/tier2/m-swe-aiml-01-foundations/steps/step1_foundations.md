# Foundation Model Selection — AIMC Fundamentals

## What you'll learn

The AI/ML Model Canvas (AIMC) is your design surface for the full foundation model lifecycle — from model selection through deployment and governance. Before you build, you need to understand the catalog.

## The Model Taxonomy

AIMC organizes models into 5 types:

| Type | Use Case | Example |
|------|----------|---------|
| **LLM** | Text generation, instruction following, Q&A | Qwen3, GPT-4o, Gemini 1.5 Pro |
| **VLM** | Vision + language (charts, maps, docs) | LLaVA-Phi3, GPT-4o (vision) |
| **Embedding** | Semantic search, RAG vector store | Nomic Embed, text-embedding-3-large |
| **Code** | Code generation, review, completion | Granite 20B Code, CodeLlama |
| **Classifier** | Intent routing, PII detection | Small distilled models |

## IL Selection Matrix

| IL Level | Allowed Providers | Air-Gap Required |
|----------|-------------------|-----------------|
| IL2 | All CSPs + HuggingFace + Local | No |
| IL4 | AWS Bedrock, Azure Gov, OCI GovCloud, IBM GovCloud, Local | No |
| IL5 | Local (Ollama) only | Yes |
| IL6 | Local (Ollama) only | Yes — NSA Type 1 |

## Your Mission

Call the AIMC model catalog API and answer the 5 questions below.

```python
import requests

BASE = "http://localhost:5050"

# 1. Get all models
r = requests.get(f"{BASE}/ai-ml/api/models")
models = r.json()

# 2. Get models ranked for IL4
r4 = requests.get(f"{BASE}/ai-ml/api/models/rank?il_level=IL4")
ranked_il4 = r4.json()

# 3. Get models ranked for IL6
r6 = requests.get(f"{BASE}/ai-ml/api/models/rank?il_level=IL6")
ranked_il6 = r6.json()
```

## Questions (answer in your submission)

1. How many total models are in the catalog?
2. How many models support IL6?
3. Which provider has the most models?
4. What is the top-ranked model for IL4 and why?
5. What is the top-ranked model for IL6 and why?

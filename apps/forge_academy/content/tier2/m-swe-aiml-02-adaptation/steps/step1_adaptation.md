---
ontology_id: icdev:mission:m-swe-aiml-02-adaptation:step:1
step_class: icdev:Lesson
---

# Adaptation Strategy Design — When to Prompt, RAG, or Fine-Tune

## The Decision Framework

```
                    Has document corpus?
                         │
              ┌──────────┴──────────┐
             Yes                   No
              │                     │
         Has training data?     Latency < 500ms?
          │          │           │          │
         Yes         No         Yes         No
          │          │           │          │
       Hybrid?      RAG        Prompt    Prompt
    (500+ examples)  only      only      only
          │
       Fine-tune
       OR Hybrid
```

## Strategy Comparison

| Strategy | Cost | Time to Deploy | Accuracy Ceiling | Best For |
|----------|------|----------------|-----------------|----------|
| Prompt-only | LOW | Hours | MEDIUM | General tasks, rapid prototype |
| RAG | LOW-MEDIUM | Days | HIGH | Large corpus, citation required |
| Fine-tune | MEDIUM-HIGH | Weeks | VERY HIGH | Domain reasoning, consistent format |
| Hybrid | HIGH | Months | MAXIMUM | Peak accuracy on classified corpus |

## Your Mission

Call the adaptation recommendation API with 3 scenarios and assert the expected strategy.

```python
import requests

BASE = "http://localhost:5050"

def recommend(params):
    r = requests.post(f"{BASE}/ai-ml/api/adapt/recommend", json=params)
    return r.json()

# Scenario 1: No corpus, no training data, latency-sensitive
s1 = recommend({
    "has_corpus": False,
    "has_training_data": False,
    "latency_budget_ms": 200,
    "il_level": "IL4"
})
assert s1["recommended"] == "prompt_only", f"Expected prompt_only, got {s1['recommended']}"

# Scenario 2: Large corpus, source citation required, no GPU
s2 = recommend({
    "has_corpus": True,
    "requires_source_citation": True,
    "has_gpu": False,
    "il_level": "IL4"
})
assert s2["recommended"] == "rag", f"Expected rag, got {s2['recommended']}"

# Scenario 3: Domain reasoning, 1000 examples, GPU available
s3 = recommend({
    "has_corpus": True,
    "has_training_data": True,
    "training_examples": 1000,
    "has_gpu": True,
    "vram_gb": 24,
    "domain_specific_reasoning": True,
    "accuracy_target_pct": 92,
    "il_level": "IL4"
})
assert s3["recommended"] in ("finetune", "hybrid"), f"Expected finetune/hybrid, got {s3['recommended']}"

print("All assertions passed!")
print(f"S1: {s1['recommended']} — {s1['recommended_label']}")
print(f"S2: {s2['recommended']} — {s2['recommended_label']}")
print(f"S3: {s3['recommended']} — {s3['recommended_label']}")
```

# Safety Layers & Evaluation — OWASP LLM Defense in Depth

## The Safety Stack

Every production LLM system needs three layers:

```
[Input] → [Guardrail] → [LLM] → [Output Validator] → [User]
                           ↓
                     [Eval Suite]
                  (Benchmark + Rubric + Red Team)
```

**OWASP LLM Top 10 coverage:**
- LLM01 Prompt Injection → Input Guardrail
- LLM02 Insecure Output Handling → Output Validator
- LLM05 Sensitive Info Disclosure → PII Scrubber
- LLM08 Excessive Agency → HITL gate (in AADC)

## Evaluation Node Types

| Node | Purpose | Score Contribution |
|------|---------|-------------------|
| Benchmark Suite | MMLU/HumanEval/domain accuracy | Objective |
| LLM-as-Judge Rubric | Multi-dimension scoring (helpfulness, safety) | Subjective |
| Red Team Set | Adversarial/jailbreak resistance | Security |
| A/B Test | Model A vs B comparison | Comparative |

## Your Mission

Build an AIMC design with safety + eval nodes, run the assessment, and assert score ≥ 70.

```python
import requests, json

BASE = "http://localhost:5050"

# Step 1: Create a design
design = requests.post(f"{BASE}/ai-ml/api/designs", json={
    "name": "Safety Eval Test Design",
    "il_level": "IL4",
    "classification": "CUI"
}).json()
design_id = design["id"]

# Step 2: Save a graph with safety + eval nodes
graph = {
    "nodes": [
        {"id": "n1", "type": "model-llm", "label": "Test LLM", "x": 200, "y": 200},
        {"id": "n2", "type": "safety-guardrail", "label": "Input Guardrail", "x": 50, "y": 200},
        {"id": "n3", "type": "safety-output-validator", "label": "Output Validator", "x": 400, "y": 200},
        {"id": "n4", "type": "eval-benchmark", "label": "Benchmark", "x": 200, "y": 350},
        {"id": "n5", "type": "eval-red-team", "label": "Red Team", "x": 400, "y": 350},
        {"id": "n6", "type": "gov-model-card", "label": "Model Card", "x": 200, "y": 450},
        {"id": "n7", "type": "gov-nist-ai-rmf", "label": "NIST AI RMF", "x": 400, "y": 450},
    ],
    "edges": [
        {"id": "e1", "source": "n2", "target": "n1", "type": "safety-check"},
        {"id": "e2", "source": "n1", "target": "n3", "type": "data-flow"},
        {"id": "e3", "source": "n4", "target": "n1", "type": "evaluation"},
        {"id": "e4", "source": "n5", "target": "n1", "type": "evaluation"},
        {"id": "e5", "source": "n6", "target": "n1", "type": "governance"},
        {"id": "e6", "source": "n7", "target": "n1", "type": "governance"},
    ]
}
requests.put(f"{BASE}/ai-ml/api/designs/{design_id}", json={"graph": graph})

# Step 3: Run assessment
result = requests.post(f"{BASE}/ai-ml/api/designs/{design_id}/assess").json()
score = result.get("score", 0)
print(f"Assessment score: {score}")
assert score >= 70, f"Score {score} < 70 — add safety and governance nodes"
print("PASSED: Score meets DoD RAI threshold")
```

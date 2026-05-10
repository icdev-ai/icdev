# Model Governance — DoD RAI 5 Principles + OMB M-25-21

## Why Governance Matters

Without governance nodes, your AI design is invisible to regulators and auditors. The DoD AI Strategy requires every AI capability to pass 5 principles before deployment authorization.

## DoD RAI 5 Principles

| Principle | Canvas Node Required | NIST Controls |
|-----------|---------------------|---------------|
| **Responsible** | Output Validator or Guardrail | CA-2, SA-11 |
| **Equitable** | Eval Red Team or Benchmark | CA-7, RA-3 |
| **Traceable** | Model Card + AI BOM | AU-2, SA-8 |
| **Reliable** | Benchmark Suite + Guardrail | SI-2, RA-5 |
| **Governable** | NIST AI RMF + DoD RAI | CA-7, PL-8 |

## OMB M-25-21 Readiness Checklist

- [ ] System Card (§3.a)
- [ ] Model Card (§3.b)
- [ ] AI BOM (EO 14110 §4.2)
- [ ] NIST AI RMF assessment node
- [ ] Evaluation suite (performance testing)
- [ ] Safety controls (§5)

## Your Mission

Add governance nodes to an AIMC design and achieve ≥ 75% governance score.

```python
import requests

BASE = "http://localhost:5050"

# Create design
d = requests.post(f"{BASE}/ai-ml/api/designs", json={
    "name": "Governance Mission Design",
    "il_level": "IL4"
}).json()
did = d["id"]

# Build governance-complete graph
graph = {
    "nodes": [
        {"id": "n1", "type": "model-llm", "label": "Mission LLM", "x": 400, "y": 200},
        {"id": "n2", "type": "safety-guardrail", "label": "Input Guardrail", "x": 200, "y": 200},
        {"id": "n3", "type": "safety-output-validator", "label": "Output Validator", "x": 600, "y": 200},
        {"id": "n4", "type": "eval-benchmark", "label": "Benchmark", "x": 200, "y": 350},
        {"id": "n5", "type": "eval-red-team", "label": "Red Team", "x": 400, "y": 350},
        {"id": "n6", "type": "gov-model-card", "label": "Model Card", "x": 200, "y": 500},
        {"id": "n7", "type": "gov-system-card", "label": "System Card", "x": 400, "y": 500},
        {"id": "n8", "type": "gov-ai-bom", "label": "AI BOM", "x": 600, "y": 500},
        {"id": "n9", "type": "gov-nist-ai-rmf", "label": "NIST AI RMF", "x": 200, "y": 650},
        {"id": "n10","type": "gov-dod-rai", "label": "DoD RAI", "x": 400, "y": 650},
    ],
    "edges": [
        {"id": "e1", "source": "n2", "target": "n1", "type": "safety-check"},
        {"id": "e2", "source": "n1", "target": "n3", "type": "data-flow"},
        {"id": "e3", "source": "n4", "target": "n1", "type": "evaluation"},
        {"id": "e4", "source": "n5", "target": "n1", "type": "evaluation"},
        {"id": "e5", "source": "n6", "target": "n1", "type": "governance"},
        {"id": "e6", "source": "n7", "target": "n1", "type": "governance"},
        {"id": "e7", "source": "n8", "target": "n1", "type": "governance"},
        {"id": "e8", "source": "n9", "target": "n1", "type": "governance"},
        {"id": "e9", "source": "n10", "target": "n1", "type": "governance"},
    ]
}
requests.put(f"{BASE}/ai-ml/api/designs/{did}", json={"graph": graph})

# Run governance assessment
gov = requests.post(f"{BASE}/ai-ml/api/designs/{did}/assess-gov").json()
overall = gov["overall_score"]
dod_score = gov["dod_rai"]["score"]
omb_score = gov["omm_m25_21"]["score"]

print(f"Overall: {overall}% | DoD RAI: {dod_score}% | OMB M-25-21: {omb_score}%")
assert overall >= 75, f"Overall {overall}% < 75% — check CAT1 findings"
print("PASSED: Governance score meets DoD authorization threshold")

# Print any CAT1 findings that remain
for finding in gov["dod_rai"]["findings"]:
    if finding["severity"] == "CAT1":
        print(f"[CAT1] {finding['principle']}: {finding['score']}%")
```

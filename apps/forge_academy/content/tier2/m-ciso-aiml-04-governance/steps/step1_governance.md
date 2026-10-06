---
ontology_id: icdev:mission:m-ciso-aiml-04-governance:step:1
step_class: icdev:Lesson
---

# Model Governance — DoD RAI 5 Principles + OMB M-25-21

## Why Governance Matters

Without governance nodes, your AI design is invisible to regulators and auditors. The DoD's five
AI Ethical Principles (Responsible, Equitable, Traceable, Reliable, Governable) are the yardstick a
reviewer will hold an AI capability to. The AI/ML Model Canvas (**AIMC**, at `/ai-ml`) scores a
design against them directly.

## DoD RAI 5 Principles: what the AIMC checks

Each principle has three checks in `tools/aiml_canvas/governance_assessor.py::assess_dod_rai`.
A principle below 50% is reported as **CAT1** and one below 80% as **CAT2**.

| Principle | What the canvas looks for | NIST controls |
|-----------|---------------------------|---------------|
| **Responsible** | Output Validator or Guardrail; Model Card or NIST AI RMF node; a `deploy-*` node | CA-2, PL-8, SA-11, SI-7 |
| **Equitable** | Any `eval-*` node; an `eval-rubric` (LLM-as-judge); an `eval-red-team` | CA-7, SI-10, RA-3 |
| **Traceable** | Model Card; AI BOM; an edge of type `audit-trail` | AU-2, AU-3, AU-12, SA-8 |
| **Reliable** | `eval-benchmark`; `safety-guardrail`; `safety-output-validator` | CA-2, SA-11, SI-2, RA-5 |
| **Governable** | NIST AI RMF node; DoD RAI node; System Card | CA-7, IR-4, PL-8, SA-15 |

## OMB M-25-21 Readiness Checklist

- [ ] System Card (§3.a)
- [ ] Model Card (§3.b)
- [ ] AI BOM (model supply-chain provenance. The AIMC check label still cites EO 14110 §4.2, which was rescinded in January 2025)
- [ ] NIST AI RMF assessment node
- [ ] Evaluation suite (performance testing)
- [ ] Safety controls (§5)

## Your Mission

Add governance nodes to an AIMC design and reach an overall governance score of at least 75%.

The overall score is the mean of three assessments: DoD RAI, **IL suitability** and OMB M-25-21.
The IL-suitability part scores every `model-*` node against the foundation-model catalog. A model
node that does not name a catalog model in `properties_json.model_id` scores 0 there, and drags
the overall score down to about 60 even when every governance node is present.

You can do this in the canvas UI at `/ai-ml` or through its JSON API. The API's write routes
require a signed-in dashboard session with an operator role (admin, pm, developer or isso). An
anonymous `requests` script gets `401`. Run the script below from a session that is already
authenticated. It will not run inside the Academy sandbox, which has no network.

```python
import requests

BASE = "http://localhost:5050"
s = requests.Session()   # must carry your signed-in dashboard session cookie

# Create design
d = s.post(f"{BASE}/ai-ml/api/designs", json={
    "name": "Governance Mission Design",
    "il_level": "IL4"
}).json()
did = d["id"]

# Build a governance-complete graph
graph = {
    "nodes": [
        {"id": "n1", "type": "model-llm", "label": "Mission LLM", "x": 400, "y": 200,
         "properties_json": {"model_id": "qwen3-local"}},   # a catalog model that supports IL4
        {"id": "n2", "type": "safety-guardrail", "label": "Input Guardrail", "x": 200, "y": 200},
        {"id": "n3", "type": "safety-output-validator", "label": "Output Validator", "x": 600, "y": 200},
        {"id": "n4", "type": "eval-benchmark", "label": "Benchmark", "x": 200, "y": 350},
        {"id": "n5", "type": "eval-red-team", "label": "Red Team", "x": 400, "y": 350},
        {"id": "n6", "type": "eval-rubric", "label": "LLM-as-Judge Rubric", "x": 600, "y": 350},
        {"id": "n7", "type": "gov-model-card", "label": "Model Card", "x": 200, "y": 500},
        {"id": "n8", "type": "gov-system-card", "label": "System Card", "x": 400, "y": 500},
        {"id": "n9", "type": "gov-ai-bom", "label": "AI BOM", "x": 600, "y": 500},
        {"id": "n10", "type": "gov-nist-ai-rmf", "label": "NIST AI RMF", "x": 200, "y": 650},
        {"id": "n11", "type": "gov-dod-rai", "label": "DoD RAI", "x": 400, "y": 650},
        {"id": "n12", "type": "deploy-ollama", "label": "On-prem Serving", "x": 800, "y": 200},
    ],
    "edges": [
        {"id": "e1", "source": "n2", "target": "n1", "type": "safety-check"},
        {"id": "e2", "source": "n1", "target": "n3", "type": "data-flow"},
        {"id": "e3", "source": "n4", "target": "n1", "type": "evaluation"},
        {"id": "e4", "source": "n5", "target": "n1", "type": "evaluation"},
        {"id": "e5", "source": "n6", "target": "n1", "type": "evaluation"},
        {"id": "e6", "source": "n7", "target": "n1", "type": "governance"},
        {"id": "e7", "source": "n8", "target": "n1", "type": "governance"},
        {"id": "e8", "source": "n9", "target": "n1", "type": "governance"},
        {"id": "e9", "source": "n10", "target": "n1", "type": "governance"},
        {"id": "e10", "source": "n11", "target": "n1", "type": "governance"},
        {"id": "e11", "source": "n1", "target": "n12", "type": "data-flow"},
        {"id": "e12", "source": "n1", "target": "n7", "type": "audit-trail"},  # Traceable
    ]
}
s.put(f"{BASE}/ai-ml/api/designs/{did}", json={"graph": graph})

# Run governance assessment
gov = s.post(f"{BASE}/ai-ml/api/designs/{did}/assess-gov").json()
overall = gov["overall_score"]            # None if no framework could be assessed
dod_score = gov["dod_rai"]["score"]
omb_score = gov["omm_m25_21"]["score"]    # sic: this is the key the API returns
il_score = gov["il_suitability"]["score"]

print(f"Overall: {overall}% | DoD RAI: {dod_score}% | IL: {il_score}% | OMB M-25-21: {omb_score}%")
assert overall is not None and overall >= 75, f"Overall {overall}% < 75% — check CAT1 findings"
print("PASSED: Governance score meets the 75% threshold")

# Print any principle still below threshold
for finding in gov["dod_rai"]["findings"]:
    if finding["severity"] != "PASS":
        print(f"[{finding['severity']}] {finding['principle']}: {finding['score']}%")
```

With this graph all three assessments score 100%. Delete the `eval-rubric` node, the deploy node,
or the `audit-trail` edge and watch the matching principle drop to CAT2.

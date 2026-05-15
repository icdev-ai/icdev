---
ontology_id: icdev:mission:m-swe-aadc-08-safety-redundancy:step:1
step_class: icdev:Lesson
---

# Safety Redundancy Design — Defense in Depth for Agentic Systems

## Safety Node Coverage Requirements

AADC compliance requires ≥ 3 distinct safety node types for a passing safety score:

| Safety Node | Function | OWASP LLM Control |
|-------------|----------|-------------------|
| Input Guardrail | Prompt injection defense | LLM01 |
| Output Validator | Output handling safety | LLM02 |
| PII Scrubber | Sensitive data protection | LLM06 |
| Circuit Breaker | Runaway agent termination | LLM08 |
| HITL Gate | Human oversight enforcement | LLM08 |
| Trusted Monitor | Independent agent oversight | LLM09 |
| Rate Limiter | Excessive resource protection | LLM10 |

## The Triangle of Trust

```
          [Trusted Monitor]
               /     \
              /       \
[Input Guard]---[Agent]---[Output Validator]
              \       /
               \     /
          [Circuit Breaker]
```

## Your Mission

Build an AADC design with 3+ safety nodes and assert coverage ≥ 80%.

```python
import requests

BASE = "http://localhost:5050"

# Create design
d = requests.post(f"{BASE}/agentic-ai/api/designs", json={
    "name": "Safety Redundancy Mission",
    "il_level": "IL4",
    "primary_objective": "Demonstrate defense-in-depth safety architecture"
}).json()
did = d["id"]

# Build safety-redundant graph
graph = {
    "nodes": [
        {"id": "n1", "type": "orchestrator", "label": "Primary Agent", "x": 400, "y": 200},
        # Safety triangle
        {"id": "n2", "type": "input-guardrail", "label": "Input Guardrail (OWASP LLM01)", "x": 150, "y": 200},
        {"id": "n3", "type": "output-validator", "label": "Output Validator (OWASP LLM02)", "x": 650, "y": 200},
        {"id": "n4", "type": "trusted-monitor", "label": "Trusted Monitor", "x": 400, "y": 50},
        {"id": "n5", "type": "circuit-breaker", "label": "Circuit Breaker (confidence ≥ 0.75)", "x": 400, "y": 380},
        {"id": "n6", "type": "hitl-gate", "label": "HITL Gate (safety-impacting)", "x": 400, "y": 520},
        {"id": "n7", "type": "pii-scrubber", "label": "PII Scrubber", "x": 150, "y": 380},
        {"id": "n8", "type": "audit-logger", "label": "Audit Logger", "x": 650, "y": 380},
    ],
    "edges": [
        {"id": "e1", "source": "n2", "target": "n1", "type": "safety-check"},
        {"id": "e2", "source": "n7", "target": "n2", "type": "data-flow"},
        {"id": "e3", "source": "n1", "target": "n3", "type": "data-flow"},
        {"id": "e4", "source": "n4", "target": "n1", "type": "monitoring"},
        {"id": "e5", "source": "n5", "target": "n1", "type": "safety-check"},
        {"id": "e6", "source": "n1", "target": "n6", "type": "data-flow"},
        {"id": "e7", "source": "n1", "target": "n8", "type": "audit-trail"},
    ]
}
requests.put(f"{BASE}/agentic-ai/api/designs/{did}/graph", json={"graph_json": graph})

# Run assessment and check safety coverage
result = requests.post(f"{BASE}/agentic-ai/api/designs/{did}/assess").json()
score = result.get("score", 0)
findings = result.get("findings", [])
safety_findings = [f for f in findings if "safety" in f.get("framework", "").lower()
                   or "guardrail" in f.get("title", "").lower()
                   or "OWASP" in f.get("framework", "")]

print(f"Assessment score: {score}")
print(f"Safety-related findings: {len(safety_findings)}")
for f in safety_findings[:3]:
    print(f"  [{f.get('severity')}] {f.get('title')}")

assert score >= 60, f"Safety-redundant design should score ≥ 60: got {score}"
print("PASSED: Safety redundancy architecture validated")
```

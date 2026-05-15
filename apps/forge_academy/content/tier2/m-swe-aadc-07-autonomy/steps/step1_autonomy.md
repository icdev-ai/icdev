---
ontology_id: icdev:mission:m-swe-aadc-07-autonomy:step:1
step_class: icdev:Lesson
---

# Autonomy Level Design — L0 to L5 Safe Deployment

## Autonomy Spectrum

| Level | Name | Description | HITL Required |
|-------|------|-------------|--------------|
| L0 | Human-Operated | Human executes every step | N/A |
| L1 | Decision Support | Agent recommends, human decides | Every action |
| L2 | Supervised Automation | Agent acts, human monitors | Threshold-based |
| L3 | Conditional Automation | Agent acts autonomously within policy | Exception-based |
| L4 | High Automation | Agent self-corrects, minimal supervision | Audit trail only |
| L5 | Full Automation | No human involvement | **BLOCKED — CAT1 finding** |

**Rule:** L5 agents ALWAYS generate a CRITICAL finding in AADC assessment. You must add circuit breakers, confidence thresholds, or HITL gates to reduce autonomy.

## Autonomy Reduction Techniques

- **Circuit Breaker** → Stops agent if confidence drops below threshold
- **Confidence Threshold** → Forces human review for low-confidence decisions
- **Audit Logger** → Captures all agent actions for retrospective review
- **HITL Gate** → Hard pause for human approval on high-stakes actions
- **Trusted Monitor** → Separate monitoring agent watches the primary agent

## Your Mission

Design two AADC systems — one L5 (will fail assessment) and one L2 (will pass) — and compare findings.

```python
import requests

BASE = "http://localhost:5050"

def create_design(name, nodes, edges):
    d = requests.post(f"{BASE}/agentic-ai/api/designs", json={
        "name": name, "il_level": "IL4"
    }).json()
    did = d["id"]
    requests.put(f"{BASE}/agentic-ai/api/designs/{did}/graph",
                 json={"graph_json": {"nodes": nodes, "edges": edges}})
    result = requests.post(f"{BASE}/agentic-ai/api/designs/{did}/assess").json()
    return did, result

# Design 1: L5 unconstrained (no circuit breaker, no HITL)
_, r1 = create_design("L5 Unconstrained Agent", nodes=[
    {"id": "n1", "type": "orchestrator", "label": "Autonomous Orchestrator", "x": 200, "y": 200},
    {"id": "n2", "type": "action-executor", "label": "Action Executor", "x": 400, "y": 200},
], edges=[{"id": "e1", "source": "n1", "target": "n2", "type": "delegation"}])

# Design 2: L2 with HITL + circuit breaker
_, r2 = create_design("L2 Supervised Agent", nodes=[
    {"id": "n1", "type": "orchestrator", "label": "Supervised Orchestrator", "x": 200, "y": 100},
    {"id": "n2", "type": "hitl-gate", "label": "HITL Approval Gate", "x": 400, "y": 100},
    {"id": "n3", "type": "circuit-breaker", "label": "Circuit Breaker", "x": 400, "y": 250},
    {"id": "n4", "type": "trusted-monitor", "label": "Trusted Monitor", "x": 600, "y": 175},
    {"id": "n5", "type": "audit-logger", "label": "Audit Logger", "x": 200, "y": 300},
], edges=[
    {"id": "e1", "source": "n1", "target": "n2", "type": "data-flow"},
    {"id": "e2", "source": "n2", "target": "n3", "type": "data-flow"},
    {"id": "e3", "source": "n3", "target": "n4", "type": "data-flow"},
    {"id": "e4", "source": "n1", "target": "n5", "type": "audit-trail"},
])

# L5 should have CRITICAL findings
critical_l5 = [f for f in r1.get("findings", []) if f.get("severity") == "CRITICAL"]
print(f"L5 design: score={r1.get('score')}, CRITICAL findings={len(critical_l5)}")
assert len(critical_l5) > 0, "L5 agent should generate CRITICAL findings"

# L2 should score higher
print(f"L2 design: score={r2.get('score')}, autonomy_max=L{r2.get('autonomy_max')}")
assert r2.get("score", 0) >= r1.get("score", 100), "L2 should score >= L5"
print("PASSED: Autonomy level analysis complete")
```

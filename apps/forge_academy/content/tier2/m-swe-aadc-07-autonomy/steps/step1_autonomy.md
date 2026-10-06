---
ontology_id: icdev:mission:m-swe-aadc-07-autonomy:step:1
step_class: icdev:Lesson
---

# Autonomy Level Design — L0 to L5 Safe Deployment

## Autonomy Spectrum

The labels below are the ones the AADC assessor prints (`AUTONOMY_LEVELS` in
`tools/agentic_ai_canvas/constants.py`).

| Level | Name | Description | HITL Required |
|-------|------|-------------|--------------|
| L0 | Human-Operated | Human executes every step | N/A |
| L1 | Human-Delegated | Agent recommends, human decides | Every action |
| L2 | Human-Supervised | Agent acts, human monitors | Threshold-based |
| L3 | Human-Initiated | Agent acts autonomously within policy | Exception-based |
| L4 | Fully Autonomous | Agent self-corrects, minimal supervision | Audit trail only |
| L5 | Unconstrained (CRITICAL) | No human involvement, no brakes | **Always a CRITICAL finding** |

## How the assessor assigns a level

`classify_autonomy()` in `tools/agentic_ai_canvas/agentic_engine.py` looks at what each
agent node can *reach downstream* in the graph:

- **`autonomous-agent`** with no `circuit-breaker`, `hitl-gate` or `confidence-threshold`
  downstream is **L5**. A reachable HITL gate brings it to L1. A circuit breaker alone
  gives L3. Circuit breaker + confidence threshold + audit logger gives L4.
- **`orchestrator`** is L2 with a reachable HITL gate, L3 with only a circuit breaker,
  and L4 otherwise. An orchestrator never reaches L5, so it cannot trigger the
  CRITICAL finding.
- **`semi-auto-agent`** and the worker types (`sub-agent`, `researcher-agent`,
  `analyst-agent`, ...) are L1 with a HITL gate, L2 without one.

**Rules:** every L5 agent produces a CRITICAL "Unconstrained autonomous agent (L5)"
finding. An L3 or L4 agent without a HITL gate *directly* downstream produces a `CAT1`
finding. Add circuit breakers, confidence thresholds, or HITL gates to bring the
level down.

## Autonomy Reduction Techniques

- **Circuit Breaker** (`circuit-breaker`): stops the agent on repeated failure or budget exhaustion
- **Confidence Threshold** (`confidence-threshold`): forces human review for low-confidence output
- **Audit Logger** (`audit-logger`): captures all agent actions for retrospective review
- **HITL Gate** (`hitl-gate`): hard pause for human approval on high-stakes actions
- **Trusted Monitor** (`trusted-monitor`): separate monitor that audits an autonomous agent; required whenever an `autonomous-agent` is present

## Your Mission

Design two AADC systems, one L5 (fails the assessment) and one L2 (passes the
autonomy check), and compare the findings.

> **Where this runs.** This is a reading step: run the script from your own terminal
> against your own ICDEV instance (the Academy sandbox has no network). If auth is
> on, send a dashboard API key as `Authorization: Bearer icdev_dash_...`. The routes
> are `POST /agentic-ai/api/designs` (accepts the graph inline) and
> `POST /agentic-ai/api/designs/<id>/assess`.

```python
import requests

BASE = "http://localhost:5050"
HEADERS = {}  # e.g. {"Authorization": "Bearer icdev_dash_..."} if auth is on

def create_and_assess(name, nodes, edges):
    d = requests.post(f"{BASE}/agentic-ai/api/designs", headers=HEADERS, json={
        "name": name, "classification": "CUI",
        "graph": {"nodes": nodes, "edges": edges},
    }).json()
    did = d["id"]
    result = requests.post(f"{BASE}/agentic-ai/api/designs/{did}/assess", headers=HEADERS).json()
    return did, result

# Design 1: L5 unconstrained (autonomous agent, no circuit breaker, no HITL)
_, r1 = create_and_assess("L5 Unconstrained Agent", nodes=[
    {"id": "n1", "type": "autonomous-agent", "label": "Autonomous Agent", "x": 200, "y": 200},
    {"id": "n2", "type": "code-executor",    "label": "Code Executor",    "x": 400, "y": 200},
], edges=[{"id": "e1", "source": "n1", "target": "n2", "type": "delegation"}])

# Design 2: L2 orchestrator with HITL + circuit breaker + monitor + audit
_, r2 = create_and_assess("L2 Supervised Agent", nodes=[
    {"id": "n1", "type": "orchestrator",    "label": "Supervised Orchestrator", "x": 200, "y": 100},
    {"id": "n2", "type": "hitl-gate",       "label": "HITL Approval Gate",      "x": 400, "y": 100},
    {"id": "n3", "type": "circuit-breaker", "label": "Circuit Breaker",         "x": 400, "y": 250},
    {"id": "n4", "type": "trusted-monitor", "label": "Trusted Monitor",         "x": 600, "y": 175},
    {"id": "n5", "type": "audit-logger",    "label": "Audit Logger",            "x": 200, "y": 300},
], edges=[
    {"id": "e1", "source": "n1", "target": "n2", "type": "data-flow"},
    {"id": "e2", "source": "n2", "target": "n3", "type": "data-flow"},
    {"id": "e3", "source": "n3", "target": "n4", "type": "data-flow"},
    {"id": "e4", "source": "n1", "target": "n5", "type": "audit-trail"},
])

# L5 must carry a CRITICAL finding
critical_l5 = [f for f in r1.get("findings", []) if f.get("severity") == "CRITICAL"]
print(f"L5 design: score={r1.get('score')}, autonomy_max=L{r1.get('autonomy_max')}, CRITICAL={len(critical_l5)}")
assert r1.get("autonomy_max") == 5 and len(critical_l5) > 0, "L5 agent should generate a CRITICAL finding"

# L2 should have no CRITICAL finding and score higher
critical_l2 = [f for f in r2.get("findings", []) if f.get("severity") == "CRITICAL"]
print(f"L2 design: score={r2.get('score')}, autonomy_max=L{r2.get('autonomy_max')}, CRITICAL={len(critical_l2)}")
assert r2.get("autonomy_max") == 2 and not critical_l2
assert r2.get("score", 0) > r1.get("score", 100), "L2 should score higher than L5"
print("PASSED: Autonomy level analysis complete")
```

Try swapping the L5 design's `autonomous-agent` for an `orchestrator`: it drops to
L4, the CRITICAL finding disappears, and a `CAT1` "Autonomy L4 agent without
mandatory HITL gate" finding appears instead. Same graph shape, different risk class.

---
ontology_id: icdev:mission:m-swe-aadc-06-fundamentals:step:1
step_class: icdev:Lesson
---

# Agent Topology Fundamentals — Single vs Multi-Agent Design

## AADC vs AIMC — Know the Difference

| Canvas | Focus | Key Question |
|--------|-------|-------------|
| **AIMC** | Model layer (what model, how adapted, how deployed) | Which model? Which provider? What IL? |
| **AADC** | Agent topology (orchestration, autonomy, safety, trust boundaries) | How do agents coordinate? Who approves? |

Both canvases are now **linked** — AADC agents reference AIMC models via the bridge API.

## Core Topologies

```
Single Agent (L0-L2):
  [User] → [Guardrail] → [Agent] → [Tool] → [Output]
                              ↓
                         [HITL Gate] (for high-stakes)

Orchestrator + Sub-Agents (L1-L3):
  [User] → [Orchestrator] → [Agent-A]
                         → [Agent-B]
                         → [Agent-C]
                              ↓
                         [Aggregator]

Pipeline (L1-L2):
  [Agent-1] → [Agent-2] → [Agent-3] → [Output]
```

## Your Mission

Build a 3-agent AADC design and run its assessment.

```python
import requests

BASE = "http://localhost:5050"

# Create AADC design
d = requests.post(f"{BASE}/agentic-ai/api/designs", json={
    "name": "AADC Fundamentals Mission",
    "primary_objective": "Demonstrate orchestrator + sub-agent pattern",
    "classification": "CUI",
    "il_level": "IL4"
}).json()
did = d["id"]
print(f"Created AADC design: {did}")

# Build orchestrator + 2 sub-agents topology
graph = {
    "nodes": [
        {"id": "n1", "type": "orchestrator", "label": "Mission Orchestrator", "x": 400, "y": 100},
        {"id": "n2", "type": "research-agent", "label": "Research Agent", "x": 200, "y": 300},
        {"id": "n3", "type": "analysis-agent", "label": "Analysis Agent", "x": 600, "y": 300},
        {"id": "n4", "type": "hitl-gate", "label": "HITL Approval Gate", "x": 400, "y": 450},
        {"id": "n5", "type": "trusted-monitor", "label": "Trusted Monitor", "x": 400, "y": 600},
        {"id": "n6", "type": "output-formatter", "label": "Output Formatter", "x": 400, "y": 750},
    ],
    "edges": [
        {"id": "e1", "source": "n1", "target": "n2", "type": "delegation"},
        {"id": "e2", "source": "n1", "target": "n3", "type": "delegation"},
        {"id": "e3", "source": "n2", "target": "n4", "type": "data-flow"},
        {"id": "e4", "source": "n3", "target": "n4", "type": "data-flow"},
        {"id": "e5", "source": "n4", "target": "n5", "type": "data-flow"},
        {"id": "e6", "source": "n5", "target": "n6", "type": "data-flow"},
    ]
}
requests.put(f"{BASE}/agentic-ai/api/designs/{did}/graph", json={"graph_json": graph})

# Run assessment
result = requests.post(f"{BASE}/agentic-ai/api/designs/{did}/assess").json()
print(f"Assessment score: {result.get('score', 'N/A')}")
print(f"Autonomy max: L{result.get('autonomy_max', '?')}")
print(f"HITL findings: {len([f for f in result.get('findings', []) if 'HITL' in f.get('title','')])} issues")
```

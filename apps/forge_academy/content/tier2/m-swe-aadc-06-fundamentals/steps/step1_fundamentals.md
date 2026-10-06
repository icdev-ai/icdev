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

> **Where this runs.** This is a reading step: the script below talks to a running
> ICDEV dashboard over HTTP, so run it from your own terminal against your own
> instance (not in the Academy code sandbox, which has no network). If your instance
> has authentication on, create a dashboard API key and send it as
> `Authorization: Bearer icdev_dash_...`; bearer-token calls are exempt from the
> browser CSRF check.
>
> The routes are real (`tools/agentic_ai_canvas/blueprint.py`, mounted at
> `/agentic-ai`): `POST /agentic-ai/api/designs` creates a design and accepts the
> graph inline, and `POST /agentic-ai/api/designs/<id>/assess` scores it. Node `type`
> values must come from the canvas palette in `tools/agentic_ai_canvas/constants.py`.
> Agent types are `orchestrator`, `researcher-agent`, `analyst-agent`, `sub-agent`,
> `autonomous-agent`, and similar; an unknown type is not counted as an agent.

```python
import requests

BASE = "http://localhost:5050"
HEADERS = {}  # e.g. {"Authorization": "Bearer icdev_dash_..."} if auth is on

# Orchestrator + 2 sub-agents, with a HITL gate, trusted monitor and audit logger
graph = {
    "nodes": [
        {"id": "n1", "type": "orchestrator",     "label": "Mission Orchestrator", "x": 400, "y": 100},
        {"id": "n2", "type": "researcher-agent", "label": "Research Agent",       "x": 200, "y": 300},
        {"id": "n3", "type": "analyst-agent",    "label": "Analysis Agent",       "x": 600, "y": 300},
        {"id": "n4", "type": "hitl-gate",        "label": "HITL Approval Gate",   "x": 400, "y": 450},
        {"id": "n5", "type": "trusted-monitor",  "label": "Trusted Monitor",      "x": 400, "y": 600},
        {"id": "n6", "type": "audit-logger",     "label": "Audit Logger",         "x": 400, "y": 750},
    ],
    "edges": [
        {"id": "e1", "source": "n1", "target": "n2", "type": "delegation"},
        {"id": "e2", "source": "n1", "target": "n3", "type": "delegation"},
        {"id": "e3", "source": "n2", "target": "n4", "type": "data-flow"},
        {"id": "e4", "source": "n3", "target": "n4", "type": "data-flow"},
        {"id": "e5", "source": "n4", "target": "n5", "type": "data-flow"},
        {"id": "e6", "source": "n5", "target": "n6", "type": "data-flow"},
    ],
}

# Create the AADC design with its graph in one call
d = requests.post(f"{BASE}/agentic-ai/api/designs", headers=HEADERS, json={
    "name": "AADC Fundamentals Mission",
    "description": "Demonstrate orchestrator + sub-agent pattern",
    "classification": "CUI",
    "graph": graph,
}).json()
did = d["id"]          # e.g. "aadc-1a2b3c4d"
print(f"Created AADC design: {did}")

# Run the assessment
result = requests.post(f"{BASE}/agentic-ai/api/designs/{did}/assess", headers=HEADERS).json()
print(f"Assessment score: {result.get('score', 'N/A')}")
print(f"Autonomy max: L{result.get('autonomy_max', '?')}")
for f in result.get("findings", []):
    print(f"  [{f['severity']}] {f['framework']}: {f['title']}")
```

Every agent here reaches the `hitl-gate` downstream, so the orchestrator classifies
as **L2** and the sub-agents as **L1** (`classify_autonomy` in
`tools/agentic_ai_canvas/agentic_engine.py`). The remaining findings are the gaps you
would close next: no `confidence-threshold` (hallucination bounding), no
`alert-manager` (incident response path), no `pii-detector` + `redaction-engine`, and
no `token-budget` / `rate-limiter`. Missions 07 and 08 add them.

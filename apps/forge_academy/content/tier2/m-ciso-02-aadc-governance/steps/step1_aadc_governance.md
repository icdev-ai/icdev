---
ontology_id: icdev:mission:m-ciso-02-aadc-governance:step:1
step_class: icdev:Lesson
---

# Design an AI Governance Architecture

You've inventoried your AI systems. Now you need to govern them.

Governance means accountability — every AI action must have an owner, an approval path,
and an audit trail. In this mission you'll design an AI governance layer on the **Agentic AI Design Canvas
(AADC)** and get it to pass the governance checks the canvas runs.

## What you'll build

Open a new AADC canvas at **`/agentic-ai/canvas`**. It starts empty. Drag these four nodes
from the palette and connect them in order. This is your starting system:

```
inference-input → autonomous-agent → external-api → output-validator
```

This system has **zero governance**. Your task is to add the governance layer.

## The governance checks you must pass

These are the real check IDs from `tools/agentic_ai_canvas/constants.py`:

| Check ID | What it requires | How to satisfy it |
|---|---|---|
| `gov-1` | Oversight plan present | Add an `approval-workflow` or `hitl-gate` node |
| `gov-2` | AI use case classified | Set the design's classification (toolbar drop-down: CUI / UNCLASSIFIED / SECRET) |
| `p4-trusted-monitor` | Autonomous agents monitored | A `trusted-monitor` node is present whenever an `autonomous-agent` is |
| `p4-a2a-audit` | A2A bridge audited | Only applies if you use an `a2a-bridge`: put an `audit-logger` downstream of it |

## Step-by-step

1. **Build the starting chain** above on `/agentic-ai/canvas`
2. **Add an `approval-workflow` node** and connect it between the autonomous-agent and the external-api
3. **Add an `audit-logger` node** and connect it downstream of the approval-workflow
4. **Add a `trusted-monitor` node** and connect it as a parallel branch off the autonomous-agent
5. **Add a `circuit-breaker` node** downstream of the autonomous-agent (an emergency stop), and a
   **`token-budget` node** anywhere in the design (caps runaway spend)
6. **Check the classification** drop-down in the toolbar. It defaults to CUI. Set it to your system's level
7. **Save** the design, then click **Assess**. `gov-1`, `gov-2` and `p4-trusted-monitor` must turn green
8. Open **Assessments** from the toolbar to see the full report. Note your design ID (it is in the URL, `/agentic-ai/canvas/<design_id>`)

## Hint

Governance nodes sit *alongside* the data flow, not in it. Think of them as oversight layers.
The agent still runs, but the approval-workflow is notified, the audit-logger records what happened,
and the trusted-monitor watches for drift. Without the circuit-breaker and token-budget the design
passes the three governance checks but scores about 60. Those two nodes take it to about 74.
The report will still list open findings (drift monitoring, incident response path, and `gov-3`,
which needs a `use_case_id` in the design metadata). Those are next-iteration work, not blockers
for this mission.

## Success criteria

- Checks `gov-1`, `gov-2` and `p4-trusted-monitor` all pass
- Overall design score ≥ 70
- Design saved

When your assessment is green, come back and click **Understood → Continue**.

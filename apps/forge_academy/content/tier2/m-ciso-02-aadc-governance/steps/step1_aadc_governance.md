# Design an AI Governance Architecture

You've inventoried your AI systems. Now you need to govern them.

Governance means accountability — every AI action must have an owner, an approval path,
and an audit trail. In this mission you'll design an AI governance architecture on the
AADC canvas that satisfies all four NIST AI RMF GOVERN checks.

## What you'll build

Open the AADC canvas. You'll start with a pre-loaded agentic system:

```
inference-input → autonomous-agent → external-api → output-validator
```

This system has **zero governance**. Your task: add the governance layer.

## The 4 NIST AI RMF GOVERN checks you must pass

| Check ID | What It Requires | Node to Add |
|---|---|---|
| GOV-1 | Oversight plan present | `approval-workflow` or `hitl-gate` |
| GOV-2 | AI use case classified | Set `classification` in design metadata |
| GOV-3 (P4) | A2A bridge audited | `audit-logger` downstream of any `a2a-bridge` |
| P4-trusted-monitor | Autonomous agents monitored | `trusted-monitor` node present |

## Step-by-step

1. **Open the AADC canvas** — your pre-seeded design will load automatically
2. **Add an `approval-workflow` node** — connect it between the autonomous-agent and external-api
3. **Add an `audit-logger` node** — connect it downstream of the approval-workflow
4. **Add a `trusted-monitor` node** — connect it as a parallel branch off the autonomous-agent
5. **Set the metadata classification** — in the canvas sidebar, set Impact Classification to your system's level
6. **Run the assessment** — click Assess. All 4 GOVERN checks must turn green
7. **Save your design** — click Save. Your design ID will appear; paste it in the submission box

## Hint

Governance nodes go *alongside* the data flow, not in it. Think of them as oversight layers:
the agent still runs, but the approval-workflow gets notified, the audit-logger records it,
and the trusted-monitor watches for drift.

## Success criteria

- NIST AI RMF checks `gov-1`, `gov-2`, `p4-trusted-monitor` all pass
- Overall design score ≥ 70
- Design saved with your username

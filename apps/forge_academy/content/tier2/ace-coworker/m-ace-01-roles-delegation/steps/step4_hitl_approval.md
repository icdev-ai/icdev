---
ontology_id: icdev:mission:m-ace-01-roles-delegation:step:4
step_class: icdev:verify
---
# HITL Gates: Keeping Humans in the Loop

HITL (Human-in-the-Loop) gates are checkpoints where a co-worker stops and waits for a human before it continues. They keep an autonomous team from acting without oversight.

## When ACE trips a HITL gate

A co-worker enters `hitl_pending` when:

- **Low trust**: the role's learned trust score is below `0.6` (the "supervised" band in `tools/ace/trust_calibrator.py`). Operators can pre-authorize a role or seed its trust in `args/ace/trust.yaml` (`auto_approve_roles`, `initial_trust`)
- **A required step fails**, for example a tool call the role is not permitted to make
- **The behavioral compliance check** (run every `monitor_interval` steps, 10 by default in `args/ace/ace_config.yaml`) flags the co-worker's output

Every gate writes a `hitl_pending` row to the append-only `ace_audit_log`, and the request also appears in the unified approval inbox.

## Resolving a gate via the API

```bash
# What is waiting?
GET /api/ace/<instance_id>/hitl/pending
# -> {"items": [{"coworker_id": "…", "detail": "…", …}], "count": 1}

# Approve: the paused co-worker resumes
POST /api/ace/<instance_id>/hitl
{"coworker_id": "…", "detail": "<detail from the pending item>", "approved": true}

# Reject: the co-worker stops after its next poll
POST /api/ace/<instance_id>/hitl
{"coworker_id": "…", "detail": "<detail from the pending item>", "approved": false}
```

`coworker_id` and `detail` are required and must match the pending item exactly. Both decisions are written to the audit log.

## Your task

A `compliance_manager` co-worker reviewing `tools/auth/` for NIST AC-2 is sitting in `hitl_pending`. Write the two calls that show you what it is waiting on and approve it. Then name which of the three triggers above is most likely for a role that has never run before, and what an operator could change to stop that gate firing every time (and why they might choose not to).

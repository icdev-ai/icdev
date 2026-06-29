---
ontology_id: icdev:mission:m-ace-01-roles-delegation:step:4
step_class: icdev:verify
---
# HITL Gates: Keeping Humans in the Loop

HITL (Human-in-the-Loop) gates are mandatory checkpoints where a human must approve a co-worker's output before it is used. They prevent autonomous agents from taking irreversible actions without oversight.

## When HITL triggers

ACE triggers HITL when:
- The task confidence is below `hitl_confidence_threshold` (default 0.75)
- The task is classified as high-risk (security changes, PII access, code deployment)
- The role YAML specifies `hitl_required: always`

## Approving via API

```bash
POST /api/ace/coworker/{id}/approve
{"approved": true, "reviewer_note": "Output verified — no PII in scope"}

# Or reject and request iteration:
POST /api/ace/coworker/{id}/approve
{"approved": false, "feedback": "Missing null-safety check on line 12"}
```

## Your task

Launch a `compliance_officer` co-worker with `hitl_required: true` to: "Review the tools/auth/ directory and produce a NIST AC-2 compliance gap report." Approve the result and note any findings the co-worker flagged.

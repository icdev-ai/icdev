---
ontology_id: icdev:mission:m-docgen-01-session-lifecycle:step:3
step_class: icdev:verify
---
# Review and Finalize

After sections merge, the session enters `review` state. You inspect the merged document and either approve it or request section rewrites.

## Review API

```bash
# Get the merged document
GET /api/docgen/sessions/{session_id}/document

# Approve
POST /api/docgen/sessions/{session_id}/approve
{"reviewer_note": "Approved — all sections complete and accurate"}

# Request rewrite of a specific section
POST /api/docgen/sessions/{session_id}/rewrite
{"section": "risk_assessment", "feedback": "Add residual risk table with impact ratings"}
```

## Your task

Review the merged SSP from your session. Identify one section that could be improved. Request a rewrite with specific feedback. Then approve the final version and note the `artifact_id` of the completed document.

---
ontology_id: icdev:mission:m-gov-02-accountability:step:2
step_class: icdev:configure
---
# Build an Oversight Plan

Use the AI Accountability canvas to produce a formal oversight plan.

## Oversight plan API

```json
POST /ai-accountability/api/plan
{
  "system_id": "your-system-id",
  "plan_type": "oversight",
  "caio_designee": "Platform CAIO",
  "oversight_board": ["CIO", "CISO", "System Owner"],
  "review_frequency": "quarterly",
  "shutdown_authority": {
    "primary": "System Owner",
    "escalation": "CAIO",
    "sla_minutes": 60,
    "conditions": ["fairness_drop", "security_incident", "caio_directive"]
  },
  "appeals_process": {
    "allowed": true,
    "appeal_to": "AI Governance Board",
    "response_days": 30
  }
}
```

## Your task

Submit an oversight plan for one of the AI systems in your inventory. Include all fields. Pay special attention to `appeals_process` — this is required by OMB M-25-21 §6 for automated decision systems that affect individuals.

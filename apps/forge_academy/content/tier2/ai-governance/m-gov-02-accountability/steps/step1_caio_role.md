---
ontology_id: icdev:mission:m-gov-02-accountability:step:1
step_class: icdev:Lesson
---
# AI Accountability: CAIO and Oversight Plans

OMB M-25-21 requires every federal agency to designate a Chief AI Officer (CAIO). The CAIO is accountable for the agency's AI governance posture.

## CAIO responsibilities

- Maintain the AI inventory
- Review and approve high-impact AI deployments
- Produce annual AI strategy update
- Chair the AI Governance Board
- Accept or reject AI risk assessments

## Oversight plan components

An oversight plan answers three questions:
1. **Who** can shut down an AI system (and under what conditions)?
2. **How fast** can it be shut down (in minutes, hours, or days)?
3. **Who** signs off on the shutdown authority?

```json
{
  "system_id": "sys-abc",
  "shutdown_authority": "CAIO + System Owner",
  "shutdown_conditions": [
    "Fairness metric drops below 0.75",
    "Confabulation rate exceeds 15%",
    "Security incident affecting AI system"
  ],
  "shutdown_sla_minutes": 60,
  "approval_chain": ["System Owner", "CAIO", "CIO (for production systems)"]
}
```

## Your task

Identify which ICDEV AI system poses the highest risk if it malfunctions. Write a shutdown authority and conditions for it using the structure above.

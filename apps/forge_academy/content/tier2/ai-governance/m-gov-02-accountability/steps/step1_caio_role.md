---
ontology_id: icdev:mission:m-gov-02-accountability:step:1
step_class: icdev:Lesson
---
# AI Accountability: CAIO and Oversight Plans

OMB M-25-21 requires every federal agency to designate a Chief AI Officer (CAIO) who is accountable for the agency's AI governance posture.

## CAIO responsibilities (typical)

- Own the AI use case inventory
- Review and approve high-impact AI deployments, and waivers of the minimum practices
- Drive the agency's AI strategy and compliance planning
- Chair or co-chair the AI governance board
- Accept or reject AI risk assessments

In ICDEV the designation is a record: `python tools/compliance/accountability_manager.py --project-id <project-id> --designate-caio --name "<official>" --role CAIO --organization "<org>" --json` (MCP tool: `ai_caio_designate`).

## What an oversight plan must answer

An oversight plan answers three questions:

1. **Who** can shut down an AI system, and under what conditions?
2. **How fast** can it be shut down (minutes, hours or days)?
3. **Who** signs off on that shutdown authority?

One way to structure the answer (an example structure, not an ICDEV schema):

```json
{
  "system": "ICDEV Knowledge RAG",
  "shutdown_authority": "CAIO + System Owner",
  "shutdown_conditions": [
    "Fairness metric drops below 0.75",
    "Confabulation rate exceeds 15%",
    "Security incident affecting the AI system"
  ],
  "shutdown_sla_minutes": 60,
  "approval_chain": ["System Owner", "CAIO", "CIO (for production systems)"]
}
```

## Your task

Identify which ICDEV AI system poses the highest risk if it malfunctions. Write a shutdown authority and conditions for it using the structure above. Make every condition measurable.

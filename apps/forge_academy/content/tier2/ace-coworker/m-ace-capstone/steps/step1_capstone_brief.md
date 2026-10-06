---
ontology_id: icdev:mission:m-ace-capstone:step:1
step_class: icdev:design
---
# ACE Capstone: Full Co-Worker Pipeline

Your capstone: design a complete ACE co-worker run that solves a real engineering problem, using only the ACE surfaces that exist on the platform.

## The challenge

Design a run that:

1. Takes a repository as input (use the ICDEV repository itself)
2. Has an `agent_developer` co-worker analyze the repo structure
3. Has a `security_analyst` co-worker run a security review
4. Has a `compliance_manager` co-worker produce a NIST 800-53 control gap assessment
5. Puts a human decision in front of the compliance assessment
6. Returns a structured report with findings from all 3 co-workers

## The real surfaces you have

| Need | Surface |
|------|---------|
| Launch the team | `POST /api/ace/launch` with `problem_text` and `role_ids: ["agent_developer", "security_analyst", "compliance_manager"]` |
| Watch progress | `GET /api/ace/<instance_id>/status` (or `/coworker/live/<instance_id>`) |
| Collect findings | `GET /api/ace/<instance_id>/artifacts` |
| Human decision | `GET /api/ace/<instance_id>/hitl/pending`, then `POST /api/ace/<instance_id>/hitl` |
| Prove what ran | `GET /api/ace/<instance_id>/audit` |

Remember from m-ace-01 that you cannot *request* a HITL gate per role: the gates fire on low trust score, a failed required step, or a behavioral compliance flag. If your design needs a guaranteed human sign-off, say where it happens: for example a reviewer approves the compliance artifact before it is published, rather than relying on a gate that may not fire.

## Success criteria

- The run reaches `done` (or a `hitl_pending` you then resolve) within 5 minutes
- All 3 co-workers produce non-empty artifacts
- The compliance assessment cites at least 3 specific NIST 800-53 controls
- Your design states exactly where the human decision sits and what enforces it

## Your task

Write the launch request and the polling plan (which calls, in what order, and what you check at each one). If your instructor has enabled ACE on your platform you can run it; launching spends LLM tokens, so a reviewed design is a complete answer.

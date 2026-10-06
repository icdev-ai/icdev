---
ontology_id: icdev:mission:m-ace-03-multi-role-pipeline:step:2
step_class: icdev:coding
---
# Run and Monitor a Pipeline

A multi-role pipeline is a list of stages, each handed to one co-worker role, run in order. Each stage can require human-in-the-loop (HITL) approval before the pipeline moves on.

ACE itself has no pipeline endpoint (see Step 1), and the Academy sandbox has **no network access** anyway. So in this exercise you write the pipeline *specification* as data and simulate how it would progress. The roles must be real ACE role ids from `args/ace/roles/`.

## Your task

1. Fill in `PIPELINE_REQUEST["pipeline"]` with **at least 3 stages** covering agent design, security review and compliance check. Each stage needs a `role`, a `task` and `hitl_required`. Use roles from this list (the grader rejects anything else):
   `ai_developer`, `agent_developer`, `security_analyst`, `data_analyst`, `devops_engineer`, `compliance_manager`
2. Implement `run_pipeline()` so that it:
   - prints the specification (for example `json.dumps(PIPELINE_REQUEST, indent=2)`)
   - simulates each stage in order, printing `stage <n> <role>: <status>`: `pending_hitl` if the stage has `hitl_required=True`, otherwise `done`
   - prints a final line saying whether the pipeline finished or is waiting on HITL

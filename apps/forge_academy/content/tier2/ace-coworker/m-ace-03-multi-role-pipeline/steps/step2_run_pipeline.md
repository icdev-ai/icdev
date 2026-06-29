---
ontology_id: icdev:mission:m-ace-03-multi-role-pipeline:step:2
step_class: icdev:coding
---
# Run and Monitor a Pipeline

Use the `/api/ace/pipeline` endpoint to launch and monitor a multi-role pipeline.

## Your task

Write a Python script that:
1. POSTs your pipeline JSON to `/api/ace/pipeline`
2. Polls `/api/ace/pipeline/{id}/status` every 5 seconds
3. Prints each stage's status as it completes
4. Prints the final artifact when the pipeline finishes or hits HITL

The pipeline should have at least 3 stages covering: agent design, security review, and compliance check.

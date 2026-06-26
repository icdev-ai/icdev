---
ontology_id: icdev:mission:m-ace-02-creator-verifier:step:2
step_class: icdev:configure
---
# Wire a Creator-Verifier Pair

To wire two co-workers, launch them in sequence: creator first, then verifier listening on the creator's output topic.

## Pair configuration

```json
// Step 1: Launch creator
POST /api/ace/coworker/delegate
{
  "role": "ai_developer",
  "task": "Write a Python FastAPI endpoint that returns paginated user records from PostgreSQL",
  "output_topic": "ace.artifact.draft.api-endpoint",
  "hitl_required": false
}
// → returns {"coworker_id": "creator-abc"}

// Step 2: Launch verifier (subscribes to creator's output)
POST /api/ace/coworker/delegate
{
  "role": "security_analyst",
  "task": "Review the API endpoint for OWASP Top 10 vulnerabilities and SQL injection risks",
  "input_topic": "ace.artifact.draft.api-endpoint",
  "depends_on": "creator-abc",
  "hitl_required": true
}
```

## Your task

Wire an `agent_developer` creator + `compliance_officer` verifier pair for this task: "Design an agent that reads daily CVE feeds and posts critical findings to a Slack channel." Creator builds the design; verifier checks it against NIST AI 600-1 autonomous system requirements.

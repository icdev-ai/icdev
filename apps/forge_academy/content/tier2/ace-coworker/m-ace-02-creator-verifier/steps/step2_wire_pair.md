---
ontology_id: icdev:mission:m-ace-02-creator-verifier:step:2
step_class: icdev:configure
---
# Wire a Creator-Verifier Pair

You don't launch the creator and the verifier as two separate calls. An ACE **run** (an *instance*) assembles a team of co-workers for one problem, and the roles in that team coordinate through the topics you saw in Step 1. To get a creator-verifier pair, you launch one run and name both roles.

## Launching the pair

The Co-Worker canvas is at **`/coworker`**. Its JSON API is `POST /api/ace/launch`:

```json
POST /api/ace/launch
{
  "problem_text": "Write a Python FastAPI endpoint that returns paginated user records from PostgreSQL. Acceptance criteria: parameterised SQL only, page size capped at 100, OWASP Top 10 review passes.",
  "role_ids": ["ai_developer", "adversarial_verifier"]
}
// → 202 {"instance_id": "..."}
```

- `role_ids` skips the automatic team classifier and uses exactly the roles you name. Leave it out and ACE picks a team from the problem text.
- Put the **acceptance criteria in the problem text**. The verifier checks every criterion it is given (*"Never approve a task without checking every acceptance criterion listed in the original task spec"*), so if you leave a criterion out, nothing checks it.
- Follow the run with `GET /api/ace/<instance_id>/status`, `/messages` and `/artifacts`, or watch it live at `/coworker/live/<instance_id>`.

## Your task

Design an `agent_developer` creator + `compliance_manager` verifier pair for this problem: *"Design an agent that reads daily CVE feeds and posts critical findings to a Slack channel."* Write down the `problem_text` you would send, including at least three acceptance criteria the verifier can check against NIST AI 600-1 (for example: human approval before posting, a source citation on every finding, a rate limit on outbound posts).

This step has no form fields. Click **Configure →** once you have written your request; that marks the step complete. Launching a real run is optional and needs a configured LLM provider.

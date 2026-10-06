---
ontology_id: icdev:mission:m-ace-02-creator-verifier:step:1
step_class: icdev:Lesson
---
# The Creator-Verifier Pattern

Two co-workers checking each other is more reliable than one co-worker unchecked.

The creator-verifier pattern:
1. A **creator** role (`ai_developer` or `agent_developer`) produces an artifact
2. A **verifier** role (`adversarial_verifier`, or a domain reviewer such as `security_analyst` or `compliance_manager`) critiques it against the task's acceptance criteria
3. If the verifier rejects, its feedback goes back to the creator for the next iteration
4. The loop stops when the verifier approves, or when the iteration budget runs out and a human takes over

```
Creator ──► draft ──► Verifier ──► rejected + feedback ──► Creator ──► revised ──► Verifier ──► approved
```

## Why this matters

If a single model gets a step right 90% of the time, an independent check catches a large share of the remaining 10%: the verifier only has to *recognise* a defect, not produce the fix. ICDEV's own rules make the independence explicit — `args/ace/roles/agent_developer.yaml` says *"Never use the same LLM to both execute and grade its own work — cross-grader enforcement is mandatory."*

## Communication via topics

Each ACE role declares the event topics it listens to and emits in its role YAML under `communication:`. The verifier that ships with ICDEV is `args/ace/roles/adversarial_verifier.yaml`:

```yaml
communication:
  protocol: a2a
  listen_topics:
    - task.draft_complete
  emit_topics:
    - task.approved
    - task.rejected
```

So a creator hands work to the verifier by emitting `task.draft_complete`, and the verifier answers with `task.approved` or `task.rejected` (the rejection carries the feedback for the next creator iteration). Compare that with the creator side — `ai_developer.yaml` emits `task.completed`, `task.blocked` and `pr.ready`.

## Your task

Open `args/ace/roles/adversarial_verifier.yaml` and `args/ace/roles/ai_developer.yaml`. Which topic connects the two, and which two topics can the verifier emit in response?

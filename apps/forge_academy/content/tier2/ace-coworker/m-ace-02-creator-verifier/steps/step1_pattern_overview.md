---
ontology_id: icdev:mission:m-ace-02-creator-verifier:step:1
step_class: icdev:Lesson
---
# The Creator-Verifier Pattern

Two co-workers checking each other is more reliable than one co-worker unchecked.

The creator-verifier pattern:
1. **Creator** (ai_developer or agent_developer) produces an artifact
2. **Verifier** (security_analyst or compliance_officer) critiques it
3. The creator revises based on critique
4. Iteration stops when verifier confidence ≥ 0.85

```
Creator ──► artifact ──► Verifier ──► critique ──► Creator ──► revised ──► Verifier ──► ACCEPT
```

## Why this matters

A single LLM has a ~90% accuracy rate per step. A creator-verifier pair where both must agree reaches ~99% on the intersection — catching the 10% the creator missed.

## Communication via topics

Creator and verifier communicate through ACE's topic system. The creator publishes to `ace.artifact.draft`, the verifier subscribes and publishes to `ace.critique`, and the creator subscribes to critiques.

## Your task

Read how `listen_topics` works in the ACE role YAML. What topic would a `security_analyst` verifier need to subscribe to in order to receive artifacts from an `ai_developer` creator?

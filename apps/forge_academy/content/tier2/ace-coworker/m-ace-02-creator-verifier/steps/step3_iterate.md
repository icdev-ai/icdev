---
ontology_id: icdev:mission:m-ace-02-creator-verifier:step:3
step_class: icdev:verify
---
# Iteration and Convergence

A creator-verifier pair that never converges is a waste. Two settings bound it.

## The bounds that exist today

- **Iteration budget per role**: each role YAML caps its own work. `agent_developer.yaml` sets `max_iterations: 12`. The verifier is kept short on purpose: `adversarial_verifier.yaml` sets `max_turns: 10` and `timeout_seconds: 180`, because it checks work and does not build it.
- **Human in the loop**: when a co-worker reaches a checkpoint that needs approval, it pauses. Pending gates are listed by `GET /api/ace/<instance_id>/hitl/pending`, and a human approves or rejects with `POST /api/ace/<instance_id>/hitl` (`{"coworker_id": ..., "detail": ..., "approved": true}`). Approval resumes the paused co-worker. Rejection stops it.

## Reading convergence

ACE has no single "converged" flag. You read convergence from the run itself:

- `GET /api/ace/<instance_id>/messages`: the creator's drafts and the verifier's `task.approved` / `task.rejected` verdicts, in order
- `GET /api/ace/<instance_id>/artifacts`: the artifacts the run produced
- `GET /api/ace/<instance_id>/audit`: the append-only audit trail, including HITL decisions

If you see several rejections in a row that make the same complaint, the run is not converging. The usual fix is upstream. Rewrite the problem text so the creator knows the criterion the verifier keeps enforcing.

## Your task

Look at the pair you designed in Step 2. Write down which criterion you expect the verifier to reject most often, and how you would rewrite the `problem_text` so the creator meets it on the first draft. Click **Configure →** to record completion.

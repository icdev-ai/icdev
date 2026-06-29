---
ontology_id: icdev:mission:m-ace-02-creator-verifier:step:3
step_class: icdev:verify
---
# Iteration and Convergence

A creator-verifier pair that never converges is a waste. Configure iteration limits and acceptance criteria to ensure convergence.

## Iteration config

```json
{
  "max_iterations": 3,
  "acceptance_threshold": 0.85,
  "on_max_iterations": "escalate_hitl"
}
```

- After 3 iterations without reaching 0.85: escalate to HITL
- The human reviewer sees all 3 draft versions + critique notes

## Reading convergence metrics

GET /api/ace/pair/{pair_id}/status returns:

```json
{
  "iterations": 2,
  "creator_confidence": 0.91,
  "verifier_confidence": 0.88,
  "converged": true,
  "final_artifact": "..."
}
```

## Your task

Check your pair from Step 2. How many iterations did it take to converge? If it didn't converge, identify what the verifier's critique said and improve the creator's task description to pre-empt the critique.

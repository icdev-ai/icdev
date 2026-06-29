---
ontology_id: icdev:mission:m-readiness-02-remediation:step:3
step_class: icdev:configure
---
# Wiring Readiness as a CI Gate

Once your repo scores ≥ 0.7, wire the readiness check as a CI gate so regressions are caught automatically.

## GitHub Actions example

```yaml
- name: Agent Readiness Check
  run: |
    python -c "
    from tools.ai_augmentation.agent_readiness.checker import run_readiness_check
    result = run_readiness_check('.')
    score = result['overall_readiness_score']
    print(f'Readiness: {score:.1%}')
    if score < 0.7:
        raise SystemExit(f'Readiness gate failed: {score:.1%} < 70%')
    "
```

## Your task

Write the GitHub Actions step YAML for a readiness gate. Include: the check command, a threshold of 0.7, and a step that prints a summary of failing pillars to the Actions log.

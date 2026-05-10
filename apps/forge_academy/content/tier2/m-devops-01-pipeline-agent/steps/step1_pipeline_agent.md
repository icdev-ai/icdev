# Build a CI/CD Pipeline Agent

In this mission you'll build an agent that monitors a CI/CD pipeline, detects failures, diagnoses root causes, and generates remediation recommendations.

## The problem

A GitLab pipeline fails. The error is buried in 2,000 lines of logs. A developer manually scrolls through to find the root cause — 15 minutes later, they've identified a missing environment variable. The fix: 30 seconds. The diagnosis: 15 minutes.

Your pipeline agent diagnoses in under 5 seconds.

## What you'll build

```
Pipeline Event (stage failed)
        │
        ▼
analyze_failure(stage, logs) → { root_cause, category }
        │
        ▼
generate_fix(root_cause, category) → { fix_command, explanation }
        │
        ▼
PipelineAgent.run(event) → full remediation report
```

## The agent's decision logic

| Log pattern | Category | Fix |
|-------------|----------|-----|
| `ImportError`, `ModuleNotFoundError` | `missing_dependency` | `pip install <package>` |
| `Permission denied`, `EACCES` | `permission_error` | `chmod` or service account fix |
| `Connection refused`, `timeout` | `network_error` | Check service health, retry |
| `No such file`, `FileNotFoundError` | `missing_file` | Check path, artifact retention |
| `Exit code 1` (test failure) | `test_failure` | Run failing tests locally |
| `Out of memory`, `OOM` | `resource_error` | Increase runner memory |

## Success criteria

- `analyze_failure()` correctly categorizes all 6 failure types
- `generate_fix()` returns a non-empty fix command for each category
- `PipelineAgent.run()` processes a multi-stage pipeline event and returns a complete report
- The report includes: failed stage, root cause, fix command, and estimated resolution time

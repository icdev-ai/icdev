---
ontology_id: icdev:mission:m-readiness-01-eleven-pillars:step:1
step_class: icdev:Lesson
---
# The 11-Pillar Agent Readiness Framework

The ICDEV Agent Readiness Checker evaluates a repository across 11 pillars, producing a 0.0–1.0 readiness score. Pillars 1–7 are ported from the kodustech/agent-readiness framework; pillars 8–11 are ICDEV extensions for government/defense deployments.

## The 11 Pillars

| # | Pillar | What it checks |
|---|--------|---------------|
| 1 | Code Quality | Cyclomatic complexity, dead code, smell detectors |
| 2 | Documentation | Docstrings, README, API docs, CUI markings |
| 3 | Testing | Test coverage %, test types present, CI gate |
| 4 | Structure | Module organization, import conventions, file naming |
| 5 | Dependencies | Known CVEs, pinned versions, SBOM presence |
| 6 | Configuration | Env var usage, secrets not in code, config isolation |
| 7 | Security | SAST findings, OWASP checks, bandit severity |
| 8 | IL Classification | CUI headers, classification markers, markings accuracy |
| 9 | NIST Controls | Control references in code comments, cross-walk coverage |
| 10 | STIG Compliance | STIG V-IDs in relevant files, CAT1 violations absent |
| 11 | Append-Only Audit | Audit tables immutable, no UPDATE/DELETE on audit rows |

## Scoring

- Each pillar reports: `passed`, `total`, `percentage`
- Overall score = weighted average across pillars; the weights live in `args/agent_readiness.yaml` (`pillar_weights`, e.g. `il-classification: 1.5`, `structure: 0.8`)
- The critical pillars (security, IL classification, NIST controls, STIG compliance, append-only audit) raise a `critical` finding in the result's `anomalies` list when they score below 50%. The checker itself reports; turning that into a deployment block is your job (m-readiness-02 wires it into CI)

## Your task

Run the readiness checker from the root of a Python repository (the ICDEV checkout works). Use:

```bash
python -c "from tools.ai_augmentation.agent_readiness.checker import run_readiness_check; import json; print(json.dumps(run_readiness_check('.'), indent=2))"
```

Read the output and identify the 3 lowest-scoring pillars.

# Requirements (Additional)

> Shard of `tools/manifest.md`. See index at `tools/manifest.md`.

## Requirements (Additional)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Complexity Scorer | tools/requirements/complexity_scorer.py | Scale-adaptive complexity scoring | --session-id, --json | Complexity score |
| Elicitation Techniques | tools/requirements/elicitation_techniques.py | BMAD-inspired elicitation technique engine | --list, --activate, --json | Technique prompts |
| PRD Generator | tools/requirements/prd_generator.py | Product Requirements Document generation | --session-id, --json | PRD markdown |
| PRD Validator | tools/requirements/prd_validator.py | PRD quality validation (6 checks) | --session-id, --validate, --json | Validation results |
| Use Case Validator | tools/requirements/use_case_validator.py | Validates args/use_cases.yaml template_requirements structure against DB CHECK constraints | --json, --fix, --yaml-path | Violations + warnings |


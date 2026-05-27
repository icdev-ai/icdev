# Spec-Kit Patterns (D156–D161)

> Shard of `tools/manifest.md`. See index at `tools/manifest.md`.

## Spec-Kit Patterns (D156–D161)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Spec Quality Checker [DEPRECATED] | tools/requirements/spec_quality_checker.py | "Unit tests for English" — validates spec markdown against configurable checklist (D156), annotates with [NEEDS CLARIFICATION] markers (D160) | --spec-file, --spec-dir, --annotate, --strip-markers, --json | Quality score + check results |
| Consistency Analyzer | tools/requirements/consistency_analyzer.py | Cross-artifact consistency validation — acceptance vs testing, phases vs tasks, NIST vs ATO, file existence (D157) | --spec-file, --spec-dir, --fix-suggestions, --json | Consistency score + results |
| Constitution Manager | tools/requirements/constitution_manager.py | Per-project immutable principles management with DoD defaults — add, list, remove, validate specs against principles (D158) | --project-id, --add, --list, --validate, --load-defaults, --json | Principles + validation |
| Clarification Engine | tools/requirements/clarification_engine.py | Impact × Uncertainty prioritized clarification questions for specs and intake sessions (D159) | --spec-file, --session-id, --max-questions, --json | Prioritized questions + clarity score |
| Spec Organizer | tools/requirements/spec_organizer.py | Per-feature spec directories with [P] parallel task markers — init, migrate, register, status (D160, D161) | --init, --migrate, --migrate-all, --status, --list, --register, --json | Spec directories + status |


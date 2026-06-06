# DoD MOSA — Modular Open Systems Approach (Phase 26)

> Shard of `tools/manifest.md`. See index at `tools/manifest.md`.

## DoD MOSA — Modular Open Systems Approach (Phase 26)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| MOSA Assessor | tools/compliance/mosa_assessor.py | MOSA compliance assessment (25 requirements, 6 families, BaseAssessor pattern) | --project-id, --gate, --json | Assessment + gate |
| ICD Generator | tools/mosa/icd_generator.py | Interface Control Document generation per external interface | --project-id, --interface-id, --all, --json | ICD markdown + DB |
| TSP Generator | tools/mosa/tsp_generator.py | Technical Standard Profile generation (auto-detect standards) | --project-id, --json | TSP markdown + DB |
| Modular Design Analyzer | tools/mosa/modular_design_analyzer.py | Static analysis: coupling, cohesion, interface coverage, circular deps | --project-dir, --project-id, --store, --json | Metrics + score |
| MOSA Code Enforcer | tools/mosa/mosa_code_enforcer.py | MOSA violation scanner (coupling, boundary, missing specs) | --project-dir, --fix-suggestions, --json | Violations list |


# Code Intelligence (Phase 52 — D331-D337)

> Shard of `tools/manifest.md`. See index at `tools/manifest.md`.

## Code Intelligence (Phase 52 — D331-D337)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Code Analyzer | tools/analysis/code_analyzer.py | AST self-analysis: per-function cyclomatic/cognitive complexity, nesting, params, LOC, smell detection, maintainability scoring (D331, D333, D337) | --project-dir, --file, --project-id, --store, --trend, --json, --human | Metrics JSON |
| Runtime Feedback | tools/analysis/runtime_feedback.py | Test-to-source correlation: JUnit XML parsing, stdout fallback, per-function health scoring (D332, D334) | --xml, --stdout, --project-id, --health, --function, --json | Feedback JSON |
| Code Quality API | tools/dashboard/api/code_quality.py | Flask Blueprint: summary stats, top-complex functions, smell distribution, trend data, runtime feedback, scan trigger | /api/code-quality/* | REST endpoints |
| Code Quality Page | tools/dashboard/templates/code_quality.html | Dashboard: stat grid (7 metrics), SVG trend chart, smell bar chart, complex functions table, runtime feedback table | (template) | HTML page |
| Code Quality Config | args/code_quality_config.yaml | Smell thresholds, maintainability weights (D337), audit thresholds, scan exclusion dirs | (config) | YAML config |
| Architecture Audit | tools/analysis/architecture_audit.py | OPT-54 — Ousterhout deep-module analysis + import-coupling cluster detection. Computes depth_ratio (public_symbols / impl_lines) across all .py files, classifies deep/balanced/shallow, finds tightly-coupled package pairs | --path, --format markdown\|json, --top, --min-edges, --out, --json | RFC-style markdown or JSON report |
| CodeLens | tools/code_intelligence/codelens.py | Phase D exit validation gate (Phase 52 / D331-D337). Thin aggregator that delegates to code_analyzer.py for AST self-analysis; returns combined JSON report with pass/fail gate status | --all, --project-dir, --file, --json | Gate JSON {gate, status, reason, target, analysis} |


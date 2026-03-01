# CUI // SP-CTI
# Code Intelligence & Adaptive Learning

> Phase 52 — AST self-analysis, code fitness dashboard, runtime feedback, guided refactoring via Innovation Engine.

---

## Overview

ICDEV analyzes its own codebase to provide read-only, advisory-only code quality intelligence. All 4 components are deterministic, append-only, and never modify source files. They make ICDEV **smarter about its own code** without making it **autonomous over its own code**.

## Architecture Decisions

- **D331:** Code quality metrics are read-only, advisory-only (D110 pattern). Never modifies source files.
- **D332:** `code_quality_metrics` and `runtime_feedback` tables are append-only time-series (D6, D131 pattern).
- **D333:** Python uses `ast.NodeVisitor` (D13); other languages use regex branch-counting (same dispatch as `modular_design_analyzer.py`).
- **D334:** Runtime feedback maps test→source via naming convention. Advisory correlation only.
- **D335:** Code quality signals feed into existing Innovation Engine pipeline (D199-D208). No new pipeline. No autonomous modification.
- **D336:** Pattern learning uses existing +0.1/-0.2 model from `pattern_detector.py`.
- **D337:** Maintainability score = deterministic weighted average: complexity(0.30) + smell_density(0.20) + test_health(0.20) + coupling(0.15) + coverage(0.15).

## Prerequisites

- Phase 35 (Innovation Engine) — signal pipeline, introspective analysis, pattern detector
- Phase 44 (Innovation Adaptation) — extension hooks, dashboard infrastructure
- Phase 51 (Dashboard) — base template, chart.js, table.js

## Component 1: AST Self-Analysis Tool

**File:** `tools/analysis/code_analyzer.py`

### Capabilities
1. **Python AST analysis** — Per-function metrics via `ast.NodeVisitor`:
   - Cyclomatic complexity (branch counting)
   - Cognitive complexity (nesting-aware)
   - Nesting depth
   - Parameter count
   - Lines of code (total, code, comment)
2. **Non-Python analysis** — Regex-based file-level metrics for Java, Go, Rust, C#, TypeScript
3. **5 smell detectors:**
   - `long_function` — >50 LOC
   - `deep_nesting` — >4 nesting depth
   - `high_complexity` — >10 cyclomatic complexity
   - `too_many_params` — >5 parameters
   - `god_class` — >10 methods in file
4. **Maintainability score** — Deterministic weighted average (D337)
5. **Trend tracking** — Time-series from `code_quality_metrics` table

### CLI
```bash
python tools/analysis/code_analyzer.py --project-dir tools/ --json
python tools/analysis/code_analyzer.py --project-dir tools/ --store --json
python tools/analysis/code_analyzer.py --file path/to/file.py --json
python tools/analysis/code_analyzer.py --project-dir tools/ --trend --json
python tools/analysis/code_analyzer.py --project-dir tools/ --human
```

## Component 2: Code Fitness Dashboard

### Production Audit Checks (5 new)
| Check | ID | Category | Severity | Condition |
|-------|----|----------|----------|-----------|
| Code analyzer syntax | CODE-001 | code_quality | warning | AST parse of code_analyzer.py |
| Avg complexity | CODE-002 | code_quality | blocking | Avg CC > 25 |
| High complexity % | CODE-003 | code_quality | warning | >5% functions with CC > 15 |
| Smell density | CODE-004 | code_quality | warning | >10/KLOC warn, >20 fail |
| Maintainability trend | CODE-005 | code_quality | warning | Latest < previous - 0.05 |

### Dashboard Page: `/code-quality`
- 7-metric stat grid (files, functions, LOC, avg CC, maintainability, smells, high CC)
- Run Scan button (triggers `POST /api/code-quality/scan`)
- SVG maintainability trend chart
- Smell distribution horizontal bar chart
- Top 20 most complex functions table
- Runtime feedback (test pass rates) table

### API Blueprint: `/api/code-quality/*`
| Route | Method | Description |
|-------|--------|-------------|
| `/summary` | GET | Aggregate stats from latest scan |
| `/top-complex` | GET | Top N functions by cyclomatic complexity |
| `/smells` | GET | Smell type distribution |
| `/trend` | GET | Maintainability time-series |
| `/feedback` | GET | Runtime feedback (test pass rates) |
| `/scan` | POST | Trigger new code quality scan |

## Component 3: Runtime Feedback Collector

**File:** `tools/analysis/runtime_feedback.py`

### Capabilities
1. **JUnit XML parsing** — Parse pytest `--junitxml` output via `xml.etree.ElementTree` (D7, stdlib, air-gap safe)
2. **Stdout fallback** — Regex parser for `pytest -v` output
3. **Test-to-source mapping** — Convention: strip `test_` prefix from function/file names (D334)
4. **DB storage** — Append-only `runtime_feedback` table (D332)
5. **Function health score** — Joins `code_quality_metrics` + `runtime_feedback`:
   - Health = 0.40 × complexity_factor + 0.35 × pass_rate + 0.25 × maintainability

### CLI
```bash
python tools/analysis/runtime_feedback.py --xml .tmp/results.xml --project-id proj-123 --json
python tools/analysis/runtime_feedback.py --stdout "PASSED tests/test_foo.py::test_bar" --json
python tools/analysis/runtime_feedback.py --health --function analyze_code --json
```

## Component 4: Guided Refactoring & Reinforcement Learning

### Introspective Analysis Extension
**File:** `tools/innovation/introspective_analyzer.py` (extended)

New analysis type `code_quality` added to `ANALYSIS_TYPES`. Cross-references:
- `code_quality_metrics` — Functions with high cyclomatic complexity
- `runtime_feedback` — Functions with low test pass rates or no tests

Generates innovation signals (`source_type: code_quality_introspective`) for functions with:
- CC > 15 AND test pass rate < 0.8 → `refactor_complex_undertested`
- CC > 15 AND no tests → `refactor_complex_untested`

Signals flow through existing pipeline: DISCOVER → SCORE → TRIAGE → GENERATE → BUILD (HITL review).

### Pattern Detector Extension
**File:** `tools/knowledge/pattern_detector.py` (extended)

4 initial code quality patterns seeded via `seed_code_quality_patterns()`:
1. `high_complexity_and_failures` — Functions with CC>15 often have test failures
2. `deep_nesting_and_failures` — Deeply nested code correlates with test failures
3. `too_many_params` — Functions with >5 params increase maintenance burden
4. `god_class` — Files with >10 methods indicate decomposition needed

TDD learning via `learn_from_tdd_outcome()`:
- After TDD cycle completes, checks if function matches any code quality pattern
- Adjusts pattern confidence: +0.1 on success, -0.2 on failure (existing D336 model)

## Database Tables

### `code_quality_metrics` (append-only, D332)
Per-function/per-file code quality snapshots. Grouped by `scan_id` for trend tracking.

### `runtime_feedback` (append-only, D332)
Per-test-case results correlated to source functions via naming convention.

## Security Gate

```yaml
code_quality:
  blocking:
    - avg_cyclomatic_complexity_exceeds_critical
  warning:
    - maintainability_score_declining
    - high_smell_density
    - dead_code_exceeds_threshold
  thresholds:
    max_avg_complexity: 25
    min_maintainability_score: 0.40
    max_smell_density_per_kloc: 20
    max_dead_code_pct: 10
```

## MCP Tools (Unified Gateway)

| Tool | Handler | Description |
|------|---------|-------------|
| `code_analyze` | `handle_code_analyze` | Scan directory/file, optionally store metrics |
| `code_quality_report` | `handle_code_quality_report` | Get trend data for project |
| `runtime_feedback_collect` | `handle_runtime_feedback_collect` | Parse JUnit XML, store feedback |

## Tests

```bash
pytest tests/test_code_analyzer.py -v        # 29 tests — AST analysis, smells, scoring, CLI
pytest tests/test_runtime_feedback.py -v     # 22 tests — XML parsing, mapping, storage, health
pytest tests/test_production_audit.py -v     # 42 tests — includes 5 CODE check tests
```

## Verification

```bash
# Core tool works
python tools/analysis/code_analyzer.py --project-dir tools/ --json
python tools/analysis/code_analyzer.py --project-dir tools/ --store --json

# Runtime feedback works
pytest tests/ -v --junitxml=.tmp/results.xml
python tools/analysis/runtime_feedback.py --xml .tmp/results.xml --project-id proj-test --json

# Production audit recognizes new category
python tools/testing/production_audit.py --category code_quality --json

# Innovation engine picks up signals
python tools/innovation/introspective_analyzer.py --analyze --type code_quality --json

# Tests pass
pytest tests/test_code_analyzer.py tests/test_runtime_feedback.py -v

# Dashboard page loads
# Navigate to http://localhost:5000/code-quality
```

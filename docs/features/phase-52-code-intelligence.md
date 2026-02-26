# Phase 52 — Code Intelligence & Adaptive Learning

**CUI // SP-CTI**

| Field | Value |
|-------|-------|
| Phase | 52 |
| Title | Code Intelligence & Adaptive Learning |
| Status | Implemented |
| Priority | P2 |
| Dependencies | Phase 35 (Innovation Engine), Phase 44 (Innovation Adaptation), Phase 51 (Unified Chat Dashboard) |
| Author | ICDEV Architect Agent |
| Date | 2026-02-24 |

---

## 1. Problem Statement

ICDEV builds applications with TDD, security scanning, and compliance validation — but it has no systematic visibility into the quality of its own codebase. When a developer asks "which functions are the most complex?" or "is our maintainability improving?", there is no tool to answer. Code smells accumulate silently. Functions grow in complexity without triggering any alert. Test failures correlate to source functions only by manual inspection. The Innovation Engine identifies external improvement opportunities but lacks internal code quality signals.

Without code self-analysis, ICDEV cannot:
- Identify its own technical debt quantitatively
- Correlate test failures to source complexity
- Track maintainability trends over time
- Feed code quality insights into the Innovation Engine for guided refactoring

Phase 52 closes this gap with 4 read-only, advisory-only components that analyze ICDEV's own code without modifying it.

---

## 2. Goals

1. Provide per-function code quality metrics via AST analysis (Python) and regex analysis (Java, Go, Rust, C#, TypeScript)
2. Detect 5 code smell types: long functions, deep nesting, high complexity, too many parameters, god classes
3. Compute deterministic maintainability scores using a weighted formula (D337)
4. Parse JUnit XML test results and correlate them to source functions via naming convention
5. Compute per-function health scores by joining code quality metrics with test reliability
6. Extend the production audit with 5 code quality checks (CODE-001 through CODE-005)
7. Provide a `/code-quality` dashboard page with stats, charts, and drill-down tables
8. Feed code quality signals into the Innovation Engine for guided refactoring recommendations
9. Enable pattern-based TDD learning via existing confidence adjustment model (+0.1/-0.2)

---

## 3. Architecture

```
                    Code Intelligence Pipeline
          ┌──────────────────────────────────────┐
          │        code_quality_config.yaml       │
          │   (thresholds, weights, exclusions)   │
          └──────────────┬───────────────────────┘
                         │
          ┌──────────────┼───────────────────┐
          ↓              ↓                    ↓
   AST Analysis    Runtime Feedback    Innovation Engine
   (code_analyzer)  (runtime_feedback)  (introspective)
          │              │                    │
          ↓              ↓                    ↓
   code_quality_     runtime_          Innovation signals
   metrics table     feedback table    (code_quality_
   (append-only)     (append-only)      introspective)
          │              │                    │
          └──────┬───────┘                    ↓
                 ↓                     Pattern Detector
          Dashboard API                (TDD learning)
          /api/code-quality/*
          /code-quality page
          /prod-audit (CODE-001..005)
```

### Key Design Principles

- **Read-only, advisory-only** — No component modifies source files (D331)
- **Append-only storage** — Both tables are time-series, never updated/deleted (D332)
- **Deterministic scoring** — Maintainability formula uses fixed weights, no probabilistic element (D337)
- **Air-gap safe** — Python `ast` module and `xml.etree.ElementTree` only, zero external dependencies (D333, D7)

---

## 4. Components

### Component 1: AST Self-Analysis (`tools/analysis/code_analyzer.py`)

**Class `CodeAnalyzer`** with 3 AST visitors for Python:
- `_PythonComplexityVisitor` — Cyclomatic complexity (branch counting)
- `_CognitiveComplexityVisitor` — Nesting-aware cognitive complexity
- `_NestingDepthVisitor` — Maximum nesting depth

Non-Python languages use regex-based branch counting (same pattern as `modular_design_analyzer.py`).

**5 Smell Detectors:**
| Smell | Threshold | Description |
|-------|-----------|-------------|
| `long_function` | >50 LOC | Function body exceeds 50 lines |
| `deep_nesting` | >4 levels | Control flow nesting exceeds 4 |
| `high_complexity` | >10 CC | Cyclomatic complexity exceeds 10 |
| `too_many_params` | >5 params | Function has more than 5 parameters |
| `god_class` | >10 methods | File contains more than 10 functions |

**Maintainability Score (D337):**
```
score = (complexity_weight * complexity_factor
       + smell_weight * smell_factor
       + test_weight * test_factor
       + coupling_weight * coupling_factor
       + coverage_weight * coverage_factor)

Weights: complexity=0.30, smell_density=0.20, test_health=0.20,
         coupling=0.15, coverage=0.15
```

### Component 2: Code Fitness Dashboard

5 production audit checks added to `tools/testing/production_audit.py`:

| ID | Check | Severity | Condition |
|----|-------|----------|-----------|
| CODE-001 | Code analyzer syntax | warning | AST parse of code_analyzer.py |
| CODE-002 | Avg complexity | blocking | Avg CC > 25 |
| CODE-003 | High complexity % | warning | >5% functions with CC > 15 |
| CODE-004 | Smell density | warning | >10/KLOC warn, >20 fail |
| CODE-005 | Maintainability trend | warning | Latest < previous - 0.05 |

Dashboard page at `/code-quality` with:
- 7-metric stat grid
- SVG maintainability trend chart
- Smell distribution horizontal bar chart
- Top 20 complex functions table
- Runtime feedback table

### Component 3: Runtime Feedback (`tools/analysis/runtime_feedback.py`)

**Class `RuntimeFeedbackCollector`:**
- Parses JUnit XML via `xml.etree.ElementTree` (D7)
- Falls back to pytest stdout regex parsing
- Maps `test_foo` → `foo` via naming convention (D334)
- Computes per-function health: `0.40 × complexity_factor + 0.35 × pass_rate + 0.25 × maintainability`

### Component 4: Guided Refactoring & Pattern Learning

**Introspective Analyzer** extended with `analyze_code_quality()`:
- Cross-references high-complexity functions with low test pass rates
- Generates innovation signals for guided refactoring

**Pattern Detector** extended with:
- `seed_code_quality_patterns()` — 4 initial patterns
- `learn_from_tdd_outcome()` — After TDD cycle, adjusts pattern confidence using existing +0.1/-0.2 model (D336)

---

## 5. Database Tables

### `code_quality_metrics` (append-only)
Per-function/per-file snapshots: complexity, nesting, LOC, params, smells, maintainability score. Grouped by `scan_id` for trend tracking.

### `runtime_feedback` (append-only)
Per-test-case results correlated to source functions. Includes pass/fail, duration, error type/message.

---

## 6. Security Gate

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

---

## 7. MCP Tools

3 tools registered in unified gateway:
- `code_analyze` — Scan directory/file, optionally store metrics
- `code_quality_report` — Get trend data for project
- `runtime_feedback_collect` — Parse JUnit XML, store feedback

---

## 8. Architecture Decisions

| ID | Decision | Rationale |
|----|----------|-----------|
| D331 | Read-only, advisory-only | Never modifies source files (D110 pattern) |
| D332 | Append-only time-series tables | D6 audit trail pattern, D131 modularity metrics pattern |
| D333 | Python AST + regex for others | D13 pattern, same dispatch as modular_design_analyzer.py |
| D334 | Naming convention test→source mapping | Advisory correlation only, no AST cross-reference needed |
| D335 | Signals feed existing Innovation Engine | No new pipeline, no autonomous modification |
| D336 | +0.1/-0.2 confidence model | Reuses existing pattern_detector.py learning model |
| D337 | Deterministic weighted maintainability | Fixed weights, reproducible, not probabilistic (D21 pattern) |

---

## 9. Testing

```bash
pytest tests/test_code_analyzer.py -v        # 29 tests
pytest tests/test_runtime_feedback.py -v     # 22 tests
pytest tests/test_production_audit.py -v     # 42 tests (includes CODE-001..005)
```

**Total: 93 tests covering all Phase 52 functionality.**

---

## 10. Files

### New Files (9)
| File | LOC | Purpose |
|------|-----|---------|
| `tools/analysis/__init__.py` | 2 | Package |
| `tools/analysis/code_analyzer.py` | ~420 | AST self-analysis |
| `tools/analysis/runtime_feedback.py` | ~260 | Runtime feedback collector |
| `tools/dashboard/api/code_quality.py` | ~180 | Dashboard API Blueprint |
| `tools/dashboard/templates/code_quality.html` | ~215 | Dashboard page |
| `tests/test_code_analyzer.py` | ~320 | 29 tests |
| `tests/test_runtime_feedback.py` | ~200 | 22 tests |
| `goals/code_intelligence.md` | ~150 | Goal document |
| `args/code_quality_config.yaml` | ~60 | Configuration |

### Modified Files (11)
| File | Change |
|------|--------|
| `tools/db/init_icdev_db.py` | +2 CREATE TABLE statements |
| `tools/testing/production_audit.py` | +5 check functions, 7th category |
| `tools/innovation/introspective_analyzer.py` | +analyze_code_quality() |
| `tools/knowledge/pattern_detector.py` | +seed_code_quality_patterns(), +learn_from_tdd_outcome() |
| `tools/dashboard/app.py` | +/code-quality route, +Blueprint registration |
| `tools/mcp/tool_registry.py` | +3 tool entries |
| `tools/mcp/gap_handlers.py` | +3 handler functions |
| `args/security_gates.yaml` | +code_quality gate |
| `CLAUDE.md` | +D331-D337, +tables, +commands, +config |
| `tools/manifest.md` | +Code Intelligence section |
| `goals/manifest.md` | +Code Intelligence entry |

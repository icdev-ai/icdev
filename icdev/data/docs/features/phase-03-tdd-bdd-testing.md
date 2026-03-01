# Phase 3 — TDD/BDD Testing Framework

**CUI // SP-CTI**

| Field | Value |
|-------|-------|
| Phase | 3 |
| Title | TDD/BDD Testing Framework |
| Status | Implemented |
| Priority | P0 |
| Dependencies | Phase 1 (GOTCHA Framework Foundation), Phase 2 (ATLAS Build Workflow) |
| Author | ICDEV Architect Agent |
| Date | 2026-02-23 |

---

## 1. Problem Statement

Code without tests is a liability. In government and DoD environments, untested code does not ship. The cost of defects increases exponentially the later they are discovered in the development lifecycle -- a bug caught during testing costs 10x less than one caught in production, and 100x less than one discovered during an Authority to Operate (ATO) assessment or post-deployment incident.

Most AI-assisted development workflows generate code first and tests as an afterthought (if at all). This produces tests that merely confirm the implementation rather than defining the contract. The resulting code is brittle, untestable by design, and fails to meet the coverage requirements mandated by DoD quality standards.

ICDEV enforces strict Test-Driven Development (TDD) with the iron rule: **no code is written until a failing test demands it**. This is implemented through a RED-GREEN-REFACTOR cycle where Gherkin/BDD feature files define behavior in business language, pytest test cases provide technical verification, and implementation code is generated as the minimum needed to satisfy the tests. The cycle is logged to an immutable audit trail for compliance traceability.

---

## 2. Goals

1. Enforce strict TDD discipline: write failing tests first (RED), write minimum code to pass (GREEN), then clean up (REFACTOR)
2. Generate Gherkin/BDD feature files from requirements with minimum 3 scenarios per feature (happy path, error case, edge case)
3. Generate pytest test cases following the Arrange-Act-Assert pattern with independent, behavior-named tests
4. Require 0 test failures and minimum 80% code coverage (target 90%+) before proceeding
5. Integrate linting (flake8/ruff) and formatting (black/prettier) in the REFACTOR phase with mandatory test re-run after cleanup
6. Log every TDD cycle to the append-only audit trail for compliance traceability (NIST AU controls)
7. Support 6 first-class languages: Python, Java, JavaScript/TypeScript, Go, Rust, C#

---

## 3. Architecture

### 3.1 TDD Cycle

```
+-------------------------------------------------------------------+
|                        TDD CYCLE                                   |
|                                                                    |
|  +-------------------+                                             |
|  |    RED            |  1. Write Gherkin feature file              |
|  |    (Write Tests)  |  2. Generate pytest test cases              |
|  |                   |  3. Run tests -- ALL MUST FAIL              |
|  +--------+----------+                                             |
|           |                                                        |
|           v                                                        |
|  +-------------------+                                             |
|  |    GREEN          |  4. Generate minimum implementation code    |
|  |    (Make Pass)    |  5. Run tests -- ALL MUST PASS              |
|  |                   |     Coverage >= 80%                         |
|  +--------+----------+                                             |
|           |                                                        |
|           v                                                        |
|  +-------------------+                                             |
|  |    REFACTOR       |  6. Lint (flake8/ruff) + Format (black)     |
|  |    (Clean Up)     |     Re-run tests -- STILL MUST PASS         |
|  |                   |  7. Log to audit trail                      |
|  +--------+----------+                                             |
|           |                                                        |
|           v                                                        |
|     Next requirement                                               |
|     or Feature complete                                            |
+-------------------------------------------------------------------+
```

### 3.2 Tool Pipeline

```
Orchestrator
    |
    +--> test_writer.py (Gherkin + pytest generation)
    |       |
    |       +--> Feature file: features/<name>.feature
    |       +--> Unit tests: tests/unit/test_<name>.py
    |       +--> Integration tests: tests/integration/test_<name>_integration.py
    |       +--> Step definitions: tests/features/steps/<name>_steps.py
    |
    +--> pytest (RED: verify all tests FAIL)
    |
    +--> code_generator.py (minimum implementation)
    |       |
    |       +--> src/<module>.py
    |
    +--> pytest + coverage (GREEN: verify all tests PASS, >= 80%)
    |
    +--> linter.py (flake8/ruff with --fix)
    |
    +--> formatter.py (black/prettier)
    |
    +--> pytest (REFACTOR: verify tests STILL pass)
    |
    +--> audit_logger.py (log TDD cycle to audit trail)
```

### 3.3 Multi-Language Support

| Language | Test Framework | BDD Framework | Linter | Formatter |
|----------|---------------|---------------|--------|-----------|
| Python | pytest | behave | flake8/ruff | black+isort |
| Java | JUnit | Cucumber-JVM | checkstyle/PMD | google-java-format |
| JavaScript/TS | Jest/Mocha | cucumber-js | eslint+tsc | prettier |
| Go | go test | godog | golangci-lint | gofmt |
| Rust | cargo test | cucumber-rs | clippy | rustfmt |
| C# | NUnit/xUnit | SpecFlow | dotnet analyzers | dotnet format |

---

## 4. Requirements

### 4.1 RED Phase (Test Writing)

#### REQ-03-001: Gherkin Feature Generation
The system SHALL generate Gherkin feature files from requirements with minimum 3 scenarios: happy path, error case, and edge case.

#### REQ-03-002: Scenario Structure
Each Gherkin scenario SHALL follow Given-When-Then structure with specific preconditions, actions, and expected results.

#### REQ-03-003: Test Case Generation
The system SHALL generate pytest test cases following the Arrange-Act-Assert pattern with independent tests (no shared mutable state).

#### REQ-03-004: Test Naming Convention
Test names SHALL describe the behavior being tested, not the implementation details.

#### REQ-03-005: RED Verification
All generated tests MUST fail when first run. If any test passes unexpectedly, the system SHALL stop and diagnose why before proceeding.

### 4.2 GREEN Phase (Implementation)

#### REQ-03-006: Minimum Implementation
The code generator SHALL produce the minimum code required to make all tests pass (YAGNI principle -- You Ain't Gonna Need It).

#### REQ-03-007: Test Pass Gate
ALL tests MUST pass with 0 failures before proceeding to the REFACTOR phase.

#### REQ-03-008: Coverage Gate
Code coverage SHALL be at least 80% (target 90%+). Coverage below 80% blocks progression.

#### REQ-03-009: Code Conventions
Generated code SHALL follow project language conventions, include type hints (Python) or JSDoc (JavaScript), and use configuration from `args/` rather than hardcoded values.

### 4.3 REFACTOR Phase

#### REQ-03-010: Lint Clean
Linting SHALL produce 0 remaining issues after the `--fix` pass.

#### REQ-03-011: Post-Refactor Verification
Tests MUST still pass after refactoring. If refactoring breaks tests, the refactoring SHALL be reverted.

#### REQ-03-012: Behavior Preservation
Refactoring SHALL be behavior-preserving only: extract functions, rename variables, reduce complexity, remove dead code, add docstrings.

### 4.4 Audit and Logging

#### REQ-03-013: Audit Trail Entry
Every completed TDD cycle SHALL be logged to the append-only audit trail with event type, actor, project, test count, and coverage percentage.

#### REQ-03-014: Memory Entry
Every completed TDD cycle SHALL be recorded in the memory system for session continuity.

---

## 5. Database Schema

### Tables

| Table | Purpose |
|-------|---------|
| `audit_trail` | Append-only log of TDD cycle completions with test counts and coverage |
| `memory_entries` | Memory records of TDD events for session continuity |
| `projects` | Project metadata tracking test status and coverage history |

---

## 6. Tools

| Tool | Purpose |
|------|---------|
| `tools/builder/test_writer.py` | Generate Gherkin feature files and pytest test cases from requirements |
| `tools/builder/code_generator.py` | Generate minimum implementation code from test specifications |
| `tools/builder/linter.py` | Run language-appropriate linting with auto-fix (flake8/ruff, eslint, checkstyle) |
| `tools/builder/formatter.py` | Run language-appropriate code formatting (black, prettier, gofmt) |
| `tools/builder/language_support.py` | Detect project languages and list supported language profiles |
| `tools/audit/audit_logger.py` | Log TDD cycle completions to append-only audit trail |
| `tools/memory/memory_write.py` | Record TDD events in memory system |
| `tools/testing/test_orchestrator.py` | Full test pipeline: unit + BDD + E2E + gates |

---

## 7. Architecture Decisions

| ID | Decision | Rationale |
|----|----------|-----------|
| D13 | Python `ast` module for Python analysis; regex-based parsing for Java/C# | Air-gap safe, zero external dependencies for code analysis |
| D155 | Shared conftest.py with fixtures centralizes test DB setup | Prevents fixture duplication across 60+ test files; security-critical paths tested first |

---

## 8. Security Gate

**Merge Gate (enforced during TDD):**
- All tests pass (0 failures)
- Coverage >= 80%
- 0 CAT1 STIG findings
- 0 critical vulnerabilities
- SBOM current

**Testing Architecture Pipeline (9 steps):**
1. `py_compile` -- Python syntax validation
2. Ruff -- Ultra-fast linter (replaces flake8+isort+black)
3. pytest -- Unit/integration tests with coverage
4. behave/Gherkin -- BDD scenario tests
5. Bandit -- SAST security scan
6. Playwright MCP -- Browser E2E tests
7. Vision validation -- LLM-based screenshot analysis (optional)
8. Acceptance validation -- Deterministic V&V criteria check
9. Security + Compliance gates -- CUI, STIG, secrets

---

## 9. Commands

```bash
# TDD Workflow
python tools/builder/test_writer.py --feature "user auth" --project-dir "/path" --language python
python tools/builder/code_generator.py --test-file "/path/to/test.py" --project-dir "/path" --language python

# Run tests
pytest tests/ -v --tb=short                          # Run all tests
pytest tests/ -v --tb=short --cov=src --cov-report=term-missing  # With coverage

# BDD tests
behave features/ --dry-run                            # Validate Gherkin syntax
behave features/                                       # Run BDD scenarios

# Lint and format
python tools/builder/linter.py --project-dir "/path" --fix
python tools/builder/formatter.py --project-dir "/path"

# Full test orchestrator (unit + BDD + E2E + gates)
python tools/testing/test_orchestrator.py --project-dir /path/to/project
python tools/testing/test_orchestrator.py --project-dir /path --skip-e2e --project-id "proj-123"

# Audit logging
python tools/audit/audit_logger.py --event-type "tdd_cycle_complete" --actor "builder-agent" \
  --action "Completed TDD cycle" --project-id "proj-123"

# Language support
python tools/builder/language_support.py --detect "/path/to/project"
python tools/builder/language_support.py --list

# Skills
/icdev-test     # Run full test suite (pytest + behave BDD)
/icdev-build    # Build code using true TDD (RED->GREEN->REFACTOR) via M-ATLAS
```

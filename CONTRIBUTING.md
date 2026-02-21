# CUI // SP-CTI

# Contributing to ICDEV

Thank you for contributing to ICDEV. This document covers the development setup, coding standards, testing patterns, and contribution workflow.

## Development Setup

### Prerequisites

- Python 3.9 or later
- Git
- Claude Code CLI (for slash commands and MCP server access)

### Installation

```bash
git clone <repository-url>
cd ICDev
pip install -r requirements.txt
```

### First Run

If `memory/MEMORY.md` does not exist, this is a fresh environment. Run `/initialize` in Claude Code to set up all directories, manifests, memory files, and databases.

### Verify Installation

```bash
# Run the test suite
pytest tests/ -v --tb=short

# Check platform compatibility
python tools/testing/platform_check.py

# Full system health check
python tools/testing/health_check.py
```

## Adding a New Tool

1. **Check the manifest first.** Read `tools/manifest.md` to confirm a similar tool does not already exist.
2. **Create the tool** in the appropriate domain directory under `tools/<domain>/`.
3. **Add a CUI header** as the first line of the file:
   ```python
   # CUI // SP-CTI
   ```
4. **Add the tool to `tools/manifest.md`** with its name, path, purpose, and CLI usage.
5. **Write tests** in `tests/test_<name>.py`.
6. **Update `CLAUDE.md`** — add the tool's CLI commands to the appropriate section.
7. **Support `--json` output** for programmatic usage and `--human` for colored terminal output.

### Tool Design Principles

- **One job per tool.** Tools are deterministic Python scripts. They do not think; they execute.
- **No side effects.** Tools should not modify state outside their declared scope.
- **Audit trail.** Any state-changing operation must log to the append-only audit trail.
- **Air-gap safe.** Use only Python stdlib or approved dependencies. No internet-dependent features in core logic.
- **CUI markings.** All generated artifacts must include classification markings via `classification_manager.py`.

## Test Patterns

ICDEV uses a 9-step testing pipeline:

1. **py_compile** — Python syntax validation (catches missing colons, bad indentation before tests run)
2. **Ruff** (`ruff>=0.12`) — Ultra-fast Python linter (replaces flake8+isort+black)
3. **pytest** (`tests/`) — Unit and integration tests with coverage
4. **behave/Gherkin** (`features/`) — BDD scenario tests for business requirements
5. **Bandit** — SAST security scan (SQL injection, XSS, hardcoded secrets)
6. **Playwright MCP** (`.claude/commands/e2e/*.md`) — Browser automation E2E tests
7. **Vision validation** (optional) — LLM-based screenshot analysis
8. **Acceptance validation** (V&V) — Deterministic acceptance criteria verification
9. **Security + Compliance gates** — CUI markings, STIG (0 CAT1), secret detection

### Writing a Test File

```python
# CUI // SP-CTI
"""Tests for <module_name>."""

import os
import sys
import pytest

# Import guard for optional dependencies
try:
    from tools.some_module import SomeClass
except ImportError:
    pytestmark = pytest.mark.skip(reason="Missing dependency")


class TestSomeFeature:
    """Tests for the SomeFeature behavior group."""

    def test_basic_operation(self, tmp_path):
        """Verify basic operation succeeds."""
        result = SomeClass().do_something(tmp_path)
        assert result["status"] == "success"

    def test_error_handling(self, tmp_path):
        """Verify errors are handled gracefully."""
        result = SomeClass().do_something_invalid(tmp_path)
        assert result["status"] == "error"
        assert "message" in result


# CUI // SP-CTI
```

**Key conventions:**

- **Header:** `# CUI // SP-CTI` as first and last line.
- **Import guard:** Use `try/except ImportError` with `pytestmark = pytest.mark.skip()` for optional dependencies.
- **One class per behavior group.** Group related tests in a class.
- **Plain assert statements.** Do not use `unittest`-style `self.assert*` methods.
- **Use `tmp_path`** for filesystem operations. Use the `icdev_db` fixture for database tests.
- **Use the shared fixtures** from `conftest.py`: `icdev_db`, `platform_db`, `api_gateway_app`, `dashboard_app`, `auth_headers`.

### Running Tests

```bash
# All tests
pytest tests/ -v --tb=short

# Single test file
pytest tests/test_rest_api.py -v

# Single test
pytest tests/test_rest_api.py::TestSomeClass::test_method -v

# With coverage
pytest tests/ --cov=tools --cov-report=term-missing

# BDD tests
behave features/

# E2E tests
python tools/testing/e2e_runner.py --run-all
```

## Adding BDD Scenarios

1. **Add a scenario** to `features/<name>.feature` using Gherkin syntax:
   ```gherkin
   # CUI // SP-CTI
   Feature: User authentication
     Scenario: Valid API key grants access
       Given a valid API key exists
       When the user authenticates with the key
       Then the response status is 200
   # CUI // SP-CTI
   ```

2. **Create or update step definitions** in `features/steps/<name>_steps.py`.

3. **Use `subprocess.run`** for tool invocation in step definitions to keep BDD tests isolated from internal imports.

4. **Run BDD tests:**
   ```bash
   behave features/
   behave features/<name>.feature
   ```

## Adding a Compliance Framework

1. **Create the catalog** as a JSON file in `context/compliance/`.
2. **Create an assessor** that inherits from `BaseAssessor` (see `tools/compliance/base_assessor.py`).
3. **Add crosswalk mappings** to the dual-hub model (NIST 800-53 US hub or ISO 27001 international hub).
4. **Register in `args/framework_registry.yaml`**.
5. **Add data type triggers** in `args/classification_config.yaml` if applicable.
6. **Write tests** in `tests/test_<framework>_assessor.py`.
7. **Update `CLAUDE.md`** with the new framework's commands and gate conditions.

The `BaseAssessor` pattern (D116) provides crosswalk integration, gate evaluation, and CLI support in approximately 60 lines of code per new framework.

## Commit Message Format

```
<agent>: <type>: <message>
```

**Agents:** `icdev_builder`, `icdev_compliance`, `icdev_security`, `icdev_infra`, or your name for manual commits.

**Types:**
- `feat` — A new feature
- `fix` — A bug fix
- `chore` — Maintenance, refactoring, documentation

**Examples:**
```
icdev_builder: feat: Phase 29-30 — Dashboard auth, activity feed, BYOK
icdev_builder: fix: Fix wizard buttons + add V&V auto-issue workflow
icdev_builder: chore: Add CUI markings to all Python files
```

## CUI Requirements

- **All `.py` files** must have `# CUI // SP-CTI` as the first and last line (as a comment).
- **All `.html` files** must have `<!-- CUI // SP-CTI -->` as the first and last line (as an HTML comment).
- **All `.feature` files** must have `# CUI // SP-CTI` as the first and last line.
- **All `.md` files** must have `# CUI // SP-CTI` as the first and last line.
- Classification markings are auto-applied via `tools/compliance/classification_manager.py`.
- Never hard-code CUI banners directly; always use the classification manager.

## Security Guidelines

- **Never store secrets** in code or config files. Use AWS Secrets Manager or K8s secrets.
- **Audit trail is append-only.** Never add UPDATE or DELETE operations to audit tables.
- **All containers** must run as non-root with read-only root filesystem.
- **SBOM** must be regenerated on every build.
- **Security gates block on:** CAT1 STIG findings, critical/high vulnerabilities, failed tests, missing CUI markings, detected secrets.

## Architecture Decision Records

Key decisions are documented as numbered records (D1, D2, ..., D178) in `CLAUDE.md`. When making architectural choices, reference existing ADRs or propose new ones. Important patterns to follow:

- **D6:** Audit trail is append-only/immutable (no UPDATE/DELETE)
- **D26:** Declarative JSON/YAML for configuration (add rules without code changes)
- **D58:** SaaS layer wraps existing tools, does not rewrite them
- **D66:** Provider abstraction pattern (ABC + implementations)
- **D116:** BaseAssessor ABC pattern for compliance frameworks

## Getting Help

- Read `CLAUDE.md` for the complete architecture documentation and command reference.
- Read `goals/manifest.md` for the index of all workflow goals.
- Read `tools/manifest.md` for the master list of all tools.
- Check `memory/MEMORY.md` for session context and long-term facts.

# CUI // SP-CTI

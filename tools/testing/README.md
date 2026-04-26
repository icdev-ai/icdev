# ICDEV™ E2E Test Runner

End-to-end browser tests for the ICDEV™ dashboard. Tests run via Selenium headless Chrome.

## PYTHONPATH Setup

**All E2E test scripts require the repository root on `PYTHONPATH`** before importing any `tools.*` or `icdev.*` modules. Without this, imports like `from tools.db.storage import get_connection` will fail with `ModuleNotFoundError`.

### Linux / macOS

```bash
# From the repository root:
export PYTHONPATH="$(pwd):$PYTHONPATH"
python tools/testing/e2e_runner.py --run-all
```

Or inline for a single run:

```bash
PYTHONPATH="$(pwd)" python tools/testing/e2e_runner.py --run-all
```

### Windows (PowerShell)

```powershell
$env:PYTHONPATH = "$PWD;$env:PYTHONPATH"
python tools/testing/e2e_runner.py --run-all
```

### Windows (Command Prompt)

```cmd
set PYTHONPATH=%CD%;%PYTHONPATH%
python tools\testing\e2e_runner.py --run-all
```

### Why this is required

The repo ships as a flat namespace package — `tools/`, `icdev/`, `tests/`, etc. live at the root level with no `src/` layout. Python's import system will not discover them unless the root directory is on `sys.path`. The `e2e_runner.py` script inserts the root at runtime when called directly, but scripts that are imported or called as modules in a subprocess (e.g. Behave step files, Kanban-launched E2E tasks) rely on `PYTHONPATH` being set externally.

## Running Tests

```bash
# Full suite
python tools/testing/e2e_runner.py --run-all

# Single spec
python tools/testing/e2e_runner.py --spec e2e_full_dashboard

# Health check first (recommended)
python tools/testing/health_check.py --json
python tools/testing/e2e_runner.py --run-all
```

## Available E2E Modules

| Module | Description |
|--------|-------------|
| `e2e_full_dashboard.py` | Full dashboard lifecycle (login → all pages) |
| `e2e_new_canvases.py` | Studio canvas pages (NDC/SDC/PDC/BDC/DDC/ODC/IDC) |
| `e2e_devops_canvas.py` | DevOps Design Canvas |
| `e2e_qdc_canvas.py` | Query Design Canvas |
| `e2e_security_canvas.py` | Security Design Canvas |
| `e2e_diagram_validator.py` | Diagram rendering validation |

## CI / Kanban Integration

Kanban-launched E2E tasks set `PYTHONPATH` via the worktree environment. If running outside the Kanban scheduler, set it manually as shown above before invoking any test module.

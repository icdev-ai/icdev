# ICDEV™ Tools

Deterministic Python scripts that execute one job each. AI orchestrates; tools execute.

---

## Import Convention

**Rule:** All imports from the tools package **must** use the `icdev.tools.*` namespace.
Do not use `from tools.*` in new code.

```python
# WRONG — do not use in new code
from tools.llm.router import LLMRouter
from tools.db.storage import get_connection

# CORRECT — use this for all imports
from icdev.tools.llm.router import LLMRouter
from icdev.tools.db.storage import get_connection
```

### PYTHONPATH Requirement (Test Environments)

Tests must be able to resolve `icdev` as a top-level package. Ensure the repo
root is on `PYTHONPATH` before running pytest or behave:

```bash
# Option 1 — set inline
PYTHONPATH=$(pwd) pytest tests/ -v

# Option 2 — export in your shell profile or CI env
export PYTHONPATH=/path/to/ICDev

# Option 3 — use the project's installed editable package (preferred)
pip install -e .
# After this, both `icdev.tools.*` and the shim resolve correctly
# without any PYTHONPATH manipulation.
```

If you see `ModuleNotFoundError: No module named 'icdev'` in tests, the repo
root is not on `PYTHONPATH` or the package has not been installed in editable
mode.

### Import Path Migration (Background)

The `tools` package has been relocated to `icdev.tools` as part of the ICDEV™
package restructuring. A backward-compatibility shim in `tools/__init__.py`
transparently redirects `from tools.*` imports to `icdev.tools.*` so existing
scripts continue to work during the migration period without a big-bang rename.
When a `tools.<module>` lookup is requested, the shim first tries
`icdev.tools.<module>` and falls back to direct `tools.<module>` resolution if
the `icdev.tools` path does not yet exist. Existing code keeps running while
migration proceeds file-by-file.

### Deprecation Warning

A `DeprecationWarning` is wired up in `tools/__init__.py` but intentionally
suppressed until the migration reaches critical mass — the warning fires on
every import across hundreds of files, which would flood logs before most
callers have been updated. Once the bulk of internal imports are migrated, the
comment block in `tools/__init__.py` can be uncommented to re-enable the
warning and surface any remaining stragglers.

---

## Structure

Tools are organized by domain under `tools/<domain>/`. The canonical index is
`tools/manifest.md` (thin) with per-domain detail in `tools/manifest/<topic>.md`.
Before writing a new script, grep the manifest shards — the tool you need
probably already exists.

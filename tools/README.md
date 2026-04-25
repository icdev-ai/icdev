# ICDEV™ Tools

Deterministic Python scripts that execute one job each. AI orchestrates; tools execute.

---

## Import Path Migration

The `tools` package has been relocated to `icdev.tools` as part of the ICDEV™ package
restructuring. All new code should use the `icdev.tools` namespace, and existing
`from tools` imports should be updated incrementally to `from icdev.tools`.

```python
# Deprecated — avoid in new code
from tools.llm.router import LLMRouter
from tools.db.storage import get_connection

# Preferred — use this for all new imports
from icdev.tools.llm.router import LLMRouter
from icdev.tools.db.storage import get_connection
```

### Temporary Fallback (tools/__init__.py)

A backward-compatibility shim in `tools/__init__.py` transparently redirects
`from tools.*` imports to `icdev.tools.*` so existing scripts continue to work
during the migration period without requiring a big-bang rename. When a
`tools.<module>` lookup is requested, the shim first tries
`icdev.tools.<module>` and falls back to direct `tools.<module>` resolution if
the `icdev.tools` path does not yet exist. This means all existing code keeps
running while migration proceeds file-by-file.

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

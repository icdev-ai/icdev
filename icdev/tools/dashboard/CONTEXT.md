# CONTEXT.md — tools/dashboard/

Consumed by the `icdev-improve` skill and `/ask-icdev` RAG index.
NIST controls: SA-15 (development process), CM-7 (least functionality).

---

## 1. `_get_db()` — PostgreSQL connection rules

**Location:** `tools/dashboard/app.py:4320`

`_get_db()` is an inner function defined inside `create_app()`. It is the
single authoritative way for dashboard route handlers to obtain a DB connection.

```python
def _get_db():
    import os
    from flask import has_request_context
    if os.environ.get("ICDEV_STORAGE_BACKEND", "").lower() == "postgresql":
        conn = get_connection()           # PG: RLS-aware, pulls from pool
    else:
        conn = get_connection(db_path=str(DB_PATH))   # SQLite fallback
    try:
        if not has_request_context():
            conn.set_security_context(None)  # rls-bypass for CLI / background
    except Exception:
        pass
    return conn
```

**Rules that must not be broken:**

| Rule | Why |
|------|-----|
| Always call `_get_db()`, never `sqlite3.connect()` or bare `psycopg2.connect()` | `.env` may point to PostgreSQL; direct SQLite opens are invisible to the dashboard API |
| In a request context, do **not** call `conn.set_security_context()` manually | `_attach_flask_security_context()` (auth middleware) already wired `g.security_context` into the connection before the route runs |
| In a non-request context (CLI, background thread), `set_security_context(None)` is called to bypass RLS | No tenant/user context is available outside a request; without this the RLS predicate raises `UndefinedColumn` on canvas tables |
| Always close the connection in a `finally` block | PG connections come from a pool; leaks exhaust the pool under load |
| Canvas tables (`aac_*`, `dsoc_*`, `ccc_*`) have no `classification`/`tenant_id` columns | Use `get_canvas_connection()` instead of `_get_db()` for those tables or RLS will raise `UndefinedColumn` |

---

## 2. Rollback-on-caught-exception

**Locations:** `tools/dashboard/app.py:670, 4479, 4486, 4494, 4502, 4510, 4526, 4535`
and `tools/dashboard/platform_health.py:44` (`_rollback()` helper)

PostgreSQL aborts the **entire transaction** on the first error. Every
subsequent statement on that connection raises
`InFailedSqlTransaction: current transaction is aborted; commands ignored until
end of transaction block`.

The pattern throughout the dashboard is to call `conn.rollback()` immediately
inside every `except Exception:` block before issuing any further queries:

```python
try:
    row = conn.execute("SELECT COUNT(*) FROM ssp_controls WHERE ...").fetchone()
    result["controls_implemented"] = row["cnt"]
except Exception:
    conn.rollback()          # ← required; clears aborted transaction state
    try:                     # ← retry with fallback table
        row = conn.execute("SELECT COUNT(*) FROM project_controls").fetchone()
        ...
    except Exception:
        conn.rollback()      # ← clear again before the next independent query
```

`platform_health.py` encapsulates this in `_rollback(conn)`:

```python
def _rollback(conn) -> None:
    try:
        conn.rollback()
    except Exception:
        pass
```

**Key invariant:** any code that catches a DB exception and continues to use
the same connection MUST call `conn.rollback()` first. Omitting it causes all
subsequent queries on that connection to fail silently.

SQLite is forgiving (rollback is a no-op if nothing is in flight), so the
pattern is safe in both backends.

---

## 3. `_register_govcon_pages`

**Location:** `tools/dashboard/app.py:223`

```python
def _register_govcon_pages(app: "Flask", _get_db):
    """Register GovProposal/CPMP/GovCon SSR page routes on the Flask app.

    Called only when _HAS_GOVCON is True.  Extracted from create_app() so that
    child apps (and parent apps with ICDEV_GOVCON_ENABLED=false) never register
    these routes.
    """
```

Called at the end of `create_app()`:

```python
if _HAS_GOVCON:
    _register_govcon_pages(app, _get_db)
```

**Design decisions (D-CHILD-6: isolation):**

- Routes for `/cpmp`, `/proposals`, `/govcon` are isolated here rather than in
  a Blueprint so that child apps without GovCon can exclude them entirely
  without import side effects.
- The function receives `_get_db` as a parameter (not imported as a module
  global) so the routes share the same connection factory as the rest of
  `create_app()`.
- `_HAS_GOVCON` is set to `True` only when `tools/govcon/` imports succeed;
  if the module is absent the routes are never registered.
- Route handlers inside `_register_govcon_pages` use the standard
  `try / finally conn.close()` pattern and call `conn.rollback()` in every
  `except` block (see section 2).

**Adding a new GovCon route:** add it inside this function, not in the main
`create_app()` body, so the guarding `if _HAS_GOVCON:` remains the single
control point.

---

## 4. CimInstance process detection (Windows)

On **Windows with PowerShell 5.1**, `Get-Process` does **not** expose
`.CommandLine` — it is always `$null`. Filtering by command-line substring
(e.g. to detect a running dashboard or genesis daemon) therefore silently
returns nothing.

Use `Get-CimInstance Win32_Process` instead, which always populates
`.CommandLine`:

```powershell
# WRONG — .CommandLine is always $null on PS 5.1
Get-Process python | Where-Object { $_.CommandLine -like '*dashboard/app*' }

# CORRECT
Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
    Where-Object { $_.CommandLine -like '*dashboard/app*' } |
    Select-Object ProcessId, CommandLine
```

**Where this matters for the dashboard:**

- Detecting duplicate dashboard instances before starting a new one
- Killing stale processes on port 5050 (restart scripts)
- Checking whether the genesis daemon is alive from an operator script

**Canonical restart sequence (PowerShell):**

```powershell
# Kill all dashboard/app instances (leave daemon + scheduler alone)
Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
    Where-Object { $_.CommandLine -like '*dashboard/app*' } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
Start-Sleep -Seconds 3
# Confirm port is free
@(Get-NetTCPConnection -LocalPort 5050 -State Listen -ErrorAction SilentlyContinue).Count
```

**Why not `tasklist`?** `tasklist /FO CSV` does not expose the full command
line either; it only returns the executable name. `Get-CimInstance` is the
only reliable cross-version Windows method.

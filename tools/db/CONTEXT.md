# CUI // SP-CTI
# tools/db — Storage Abstraction Layer

NIST controls: AC-3 (Access Enforcement), SC-28 (Protection of Information at Rest)

## Purpose

Dual-backend storage layer (SQLite ↔ PostgreSQL). All database access in the project must go through this module. Backend is selected by `ICDEV_STORAGE_BACKEND` env var; default is `sqlite`.

**Entry point:** `tools/db/storage.py`
**Canonical import:** `from icdev.tools.db.storage import get_connection`

---

## Connection Functions

### `get_connection(db_path=None) → StorageConnection`

Standard connection for the main `icdev.db`. Use this for all tables that have `tenant_id` and `classification` columns — RLS is injected automatically when a Flask security context is present.

```python
conn = get_connection()
rows = conn.execute("SELECT * FROM projects WHERE id = ?", (pid,)).fetchall()
conn.commit()
conn.close()
```

**Special case:** If `db_path` is given and points to a `.db` file different from `ICDEV_DB_PATH`, the call bypasses the PG pool and returns a SQLite connection directly (canvas auxiliary DB pattern). RLS is skipped.

### `get_canvas_connection(canvas_env_var=None) → StorageConnection`

**REQUIRED for all canvas `db/init_db.py` files.** Canvas tables (e.g. `aac_*`, `dsoc_*`, `ccc_*`) lack `tenant_id`/`classification` columns. Using `get_connection()` injects an RLS predicate that references those columns, raising `UndefinedColumn` on every query.

`get_canvas_connection()` returns a connection with RLS disabled.

```python
# CORRECT — canvas db/init_db.py
from tools.db.storage import get_canvas_connection
conn = get_canvas_connection("MY_CANVAS_DB_PATH")

# WRONG — will raise UndefinedColumn on PG
from tools.db.storage import get_connection
conn = get_connection(db_path=canvas_path)
```

### `sqlite3.connect()` — NEVER USE

Direct `sqlite3.connect()` calls bypass RLS, bypass the PG pool, and are invisible to the dashboard API. The pre-commit hook blocks this pattern. Always use `get_connection()` or `get_canvas_connection()`.

---

## SQL Translation (`translate_sql`)

Write SQLite SQL. The wrapper transparently translates to PostgreSQL on the fly.

| SQLite | PostgreSQL |
|--------|-----------|
| `?` (placeholder) | `%s` |
| `datetime('now')` | `NOW()` |
| `datetime('now', '-N days')` | `NOW() - INTERVAL 'N days'` |
| `INSERT OR REPLACE INTO t (cols) VALUES (...)` | `INSERT INTO t (cols) VALUES (...) ON CONFLICT (pk) DO UPDATE SET ...` |
| `INSERT OR IGNORE` | `INSERT ... ON CONFLICT DO NOTHING` |
| `LIKE` | `ILIKE` |
| `GROUP_CONCAT(col, sep)` | `string_agg(col, sep)` |
| `INTEGER PRIMARY KEY AUTOINCREMENT` | `SERIAL PRIMARY KEY` |
| `last_insert_rowid()` | `lastval()` |

**Percent-sign escaping:** Bare `%` inside SQL string literals (e.g. in LIKE clauses: `'%value%'`) is automatically escaped to `%%` before `?` → `%s` substitution to prevent psycopg2 misinterpreting them as format specifiers. This happens transparently — do not pre-escape `%` manually or double-escaping will occur.

**Helper functions for backend-agnostic SQL:**
- `sql_placeholder(conn)` → `%s` or `?`
- `sql_now(conn)` → `NOW()` or `datetime('now')`
- `sql_date_sub(conn, days=N)` → backend-specific subtraction expression

---

## Row-Level Security (RLS)

RLS is injected automatically via `_inject_rls()` when a Flask security context is set. It calls `inject_row_predicate()` from `tools.security.row_security` and adds `tenant_id`, `classification`, LAC label, and COI tag predicates.

**Critical param ordering** — injected params must be in the right position:

| Statement type | Predicate position | Extra params position |
|---------------|-------------------|-----------------------|
| SELECT | prepended to WHERE | **prepended** before existing params |
| UPDATE / DELETE | appended to WHERE | **appended** after existing params |

```python
# From storage.py — the actual implementation:
is_write = bool(_RE_UPDATE.match(sql) or _RE_DELETE.match(sql))
if isinstance(params, (list, tuple)):
    params = (tuple(params) + tuple(extra)) if is_write else (tuple(extra) + tuple(params))
```

Wrong ordering causes bound-variable mismatches (wrong values silently bound to wrong slots).

**RLS is a no-op when:**
- No Flask request context is active (scripts, tests, cron)
- `classification=None` — skips the classification filter
- Using `get_canvas_connection()` — RLS disabled entirely for canvas tables

---

## APPEND_ONLY_TABLES

Certain tables are append-only (NIST AU). Any `UPDATE`, `DELETE`, or `TRUNCATE` against them is blocked by the pre-commit hook in `.claude/hooks/pre_tool_use.py`.

Examples: `audit_trail`, `hook_events`, `kanban_status_transitions`, `remediation_audit_log`, `tool_chain_events`, `otel_spans`, `prov_entities`.

**When adding a new append-only table:**
1. Add it to `APPEND_ONLY_TABLES` in `.claude/hooks/pre_tool_use.py`
2. Note it in the CLAUDE.md guardrail entry
3. Add it to `APPEND_ONLY_TABLES` in `tests/conftest.py` MINIMAL_ICDEV_SCHEMA

---

## Backend Detection

```python
from tools.db.storage import is_pg, get_backend

if is_pg():
    # PostgreSQL-specific path
    ...
else:
    # SQLite path
    ...
```

Use sparingly — prefer writing backend-agnostic SQL with the translation helpers.

---

## Connection Lifecycle

Always call `conn.close()` — use a `try/finally` pattern, not bare calls:

```python
conn = get_connection()
try:
    conn.execute(...)
    conn.commit()
finally:
    conn.close()
```

PostgreSQL connections are returned to the thread-safe pool on `close()`. Omitting `close()` leaks pool slots and causes lock storms under load.

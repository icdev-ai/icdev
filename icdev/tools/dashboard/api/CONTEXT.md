# CUI // SP-CTI
# tools/dashboard/api — Dashboard REST API Layer

NIST controls: AC-3 (Access Enforcement), SC-28 (Protection of Information at Rest)

## Purpose

Flask Blueprint REST APIs powering the ICDEV™ dashboard. Each file registers a blueprint mounted under `/api/<domain>`. All database access goes through `tools.db.storage.get_connection()` — never raw `sqlite3.connect()`.

---

## proposals.py — GovCon Proposal Lifecycle API

Blueprint: `proposals_api`, prefix `/api/proposals`

Full lifecycle: opportunities → volumes → sections → compliance matrix → color-team reviews → findings → HITL reviewer assignments.

### DB Connection Pattern

```python
def _get_db():
    conn = get_connection(db_path=str(DB_PATH))
    return conn
```

`DB_PATH` defaults to `ICDEV_DB_PATH` (the main `icdev.db`). Because it resolves to the same path as `ICDEV_DB_PATH`, `get_connection()` respects the configured backend — PostgreSQL when `ICDEV_STORAGE_BACKEND=postgresql`.

Standard route pattern:
```python
conn = _get_db()
try:
    rows = conn.execute("SELECT ... WHERE id = ?", (rid,)).fetchall()
    conn.commit()
    return jsonify(...)
finally:
    conn.close()
```

### `proposal_reviewer_assignments` — SQLite Only (PG Migration Pending)

**The `proposal_reviewer_assignments` table exists in the SQLite schema (`tools/db/init_icdev_db.py`) but has NOT been migrated to PostgreSQL.**

When `ICDEV_STORAGE_BACKEND=postgresql`, any query against this table raises:
```
psycopg2.errors.UndefinedTable: relation "proposal_reviewer_assignments" does not exist
```

PG does not auto-rollback on exception — the connection stays in a failed transaction state. Every subsequent query on that connection then fails with:
```
InFailedSqlTransaction: current transaction is aborted
```

**Required pattern for all `proposal_reviewer_assignments` queries under PG:**

```python
conn = _get_db()
try:
    rows = conn.execute(
        "SELECT * FROM proposal_reviewer_assignments WHERE review_id = ?", (rev_id,)
    ).fetchall()
    return jsonify({"assignments": [dict(r) for r in rows]})
except Exception:
    conn.rollback()
    return jsonify({"assignments": [], "warning": "reviewer assignments unavailable"}), 200
finally:
    conn.close()
```

The `conn.rollback()` in the `except` block is mandatory on PG to clear the aborted-transaction state before the connection is returned to the pool.

**Affected endpoints (prop-rev-09):**
- `GET /api/proposals/reviews/<rev_id>/assignments`
- `POST /api/proposals/reviews/<rev_id>/assign`
- `POST /api/proposals/assignments/<asgn_id>/accept`
- `POST /api/proposals/assignments/<asgn_id>/reject`
- `POST /api/proposals/assignments/<asgn_id>/reassign`

**Resolution:** Create a PG migration that adds `proposal_reviewer_assignments` to the PostgreSQL schema. Until then, every route touching this table must follow the `except Exception: conn.rollback()` pattern.

### HITL Assignment Lifecycle

```
assigned (pending) → accepted / rejected
         ↓
    in_progress → completed
         ↓
    reassigned → new assignment (pending)
```

Status transitions are recorded in `proposal_review_status_history` via `_record_status_change()`.

---

## General Patterns

### Authentication / Authorization

Routes use `@require_role(...)` decorator from `tools.dashboard.auth`. Canvas-specific routes use `@abac_protect(...)` from `tools.security.abac_engine`.

### RLS in Flask Context

`get_connection()` calls `_attach_flask_security_context(conn)` inside a Flask request, which reads `g.security_context` and injects `tenant_id`/`classification` predicates automatically. No manual setup required.

### SQL Placeholders

Always write `?` for query parameters. The storage layer translates to `%s` for PG automatically. Do not mix `%s` into SQL strings — this breaks SQLite and causes double-escaping on PG.

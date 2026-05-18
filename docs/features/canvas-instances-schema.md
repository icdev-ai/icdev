# Canvas Instances Table — Schema Design

**Classification:** CUI // SP-CTI
**Date:** 2026-05-18
**Author:** Sovanna Larry Chuon
**Status:** Design — pending implementation

---

## 1. Chosen Approach: Central `icdev.db`

Canvas instance rows will live in **central `icdev.db`** (or the PostgreSQL equivalent), not in
each per-canvas database.

### Rationale

**What per-canvas DBs already own:**  
Each canvas database (`security_canvas.db`, `pipeline_canvas.db`, etc.) stores *content* —
templates, snippets, SOPs, runbooks, and hardware/device profiles. These are reference catalogs
keyed by `name`, not runtime state. Spreading session/tenant bookkeeping across 9 separate files
would require 9 cross-database joins for any cross-canvas query and would fragment audit coverage.

**What `icdev.db` already owns:**  
Session-scoped and cross-canvas tables (`canvas_projects`, `canvas_kg_nodes`, `canvas_kg_edges`,
`canvas_ai_decisions`, `canvas_events`) are already in `icdev.db`. Instance rows are the same
class of data: they record *which* catalog artifact was activated for *which* session and tenant,
not the artifact's content.

**Decisive factors:**

| Factor | Per-canvas DB | Central icdev.db |
|--------|---------------|------------------|
| Cross-canvas queries (e.g., "all active instances for session X") | Requires 9 ATTACH/UNION | Single SELECT |
| NIST AU audit trail (append-only, unified) | Fragmented across files | Single table |
| `session_id` / `tenant_id` FK integrity | No shared FK target | `sessions`, `tenants` tables co-located |
| `get_connection()` RLS enforcement | Canvas DBs bypass RLS by design (seeding) | RLS applies natively |
| PostgreSQL compatibility | Canvas DBs are SQLite-only per catalog spec | Follows `ICDEV_STORAGE_BACKEND` |
| Migration management | 9 separate migration paths | Single numbered migration |

The per-canvas DBs use `set_security_context(None)` (RLS bypass) specifically because tenant
isolation is enforced at the API boundary, not inside those files. Placing `canvas_instances` in
`icdev.db` keeps tenant-scoped state where RLS actually runs.

---

## 2. Proposed `CREATE TABLE` SQL

```sql
-- Migration NNN_canvas_instances
-- Tracks which catalog artifacts were activated per session+tenant.
-- APPEND-ONLY: no UPDATE or DELETE (NIST AU-3 / AU-12).
CREATE TABLE IF NOT EXISTS canvas_instances (
    id              TEXT PRIMARY KEY,          -- UUID v4, client-generated or server-generated
    session_id      TEXT NOT NULL,             -- FK → chat sessions / use_case_chains.linked_session_id
    tenant_id       TEXT NOT NULL,             -- FK → tenants.id (RLS column)
    canvas          TEXT NOT NULL,             -- canvas key: security | infra | observability | boundary
                                               --   | data | network | pipeline | quality | migration
    artifact_type   TEXT NOT NULL              -- 'template' | 'snippet' | 'sop' | 'runbook'
        CHECK (artifact_type IN ('template','snippet','sop','runbook')),
    artifact_name   TEXT NOT NULL,             -- name value from the per-canvas catalog table
    use_case_id     TEXT,                      -- optional FK → use_cases.id that triggered seeding
    status          TEXT NOT NULL DEFAULT 'seeded'
        CHECK (status IN ('seeded','active','superseded')),
    classification  TEXT NOT NULL DEFAULT 'CUI',
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    metadata_json   TEXT NOT NULL DEFAULT '{}'  -- reserved for canvas-specific extra fields
);

CREATE INDEX IF NOT EXISTS idx_canvas_instances_session
    ON canvas_instances (session_id);

CREATE INDEX IF NOT EXISTS idx_canvas_instances_tenant_canvas
    ON canvas_instances (tenant_id, canvas);
```

**Why TEXT for timestamps:** Consistent with all other `icdev.db` tables
(`canvas_ai_decisions`, `canvas_events`, etc.) which use ISO-8601 strings.

**Why no FK constraints declared:** SQLite FK enforcement is opt-in per connection; PostgreSQL
mode declares constraints in the migration header. The application enforces referential integrity
at the service layer (consistent with existing pattern).

---

## 3. Session ID and Tenant ID Storage

**`session_id`** maps to the `linked_session_id` column in `use_case_chains` (set when a chain
is activated) or to the `context_id` passed into `_seed_canvas_artifacts()`. The chat API
already passes `session_id` as the first positional argument to `_seed_canvas_artifacts()`;
callers will write that value into `canvas_instances.session_id` unchanged.

**`tenant_id`** is resolved from `_get_tenant_id()` at the API boundary (same function used by
all chat endpoints). RLS in `get_connection()` will filter `canvas_instances` rows by
`tenant_id` automatically once the table is registered in the RLS column map — no additional
per-query filtering needed.

Both columns are `NOT NULL` with no default so missing values fail loudly at insert time rather
than silently creating orphaned rows.

---

## 4. Migration Plan

1. **Number the migration** — assign the next sequential number in `tools/db/migrations/`
   (currently at ~121; next available: `NNN_canvas_instances`).

2. **Create migration files:**
   ```
   tools/db/migrations/NNN_canvas_instances/
       up.sql          ← CREATE TABLE + indexes above
       down.sql        ← DROP TABLE canvas_instances (for rollback in dev only)
       meta.yaml       ← version, description, append_only: true
   ```

3. **Register as append-only** — add `"canvas_instances"` to `APPEND_ONLY_TABLES` in
   `.claude/hooks/pre_tool_use.py` to block any future UPDATE/DELETE attempts.

4. **Add to `tests/conftest.py`** — add the `canvas_instances` CREATE TABLE statement to
   `MINIMAL_ICDEV_SCHEMA` so unit tests that spin up in-memory SQLite get the table.

5. **Update `_seed_canvas_artifacts()`** — after the existing validation loop in
   `tools/dashboard/api/chat.py`, insert a row into `canvas_instances` for each artifact
   appended to `seeded`. Use the existing `_gc()` (main `icdev.db`) connection, not the
   per-canvas connection.

6. **Update companion sync** — run `python tools/dx/companion.py --sync --write --json` after
   the migration lands to propagate the new table to all AI platform configs.

7. **Coherence gate** — run `python tools/workflow/coherence_checker.py --all --fix --gate`
   before merging.

No data backfill is required: `canvas_instances` tracks future seeding events only. Existing
chat sessions that pre-date the migration simply have no rows in the table.

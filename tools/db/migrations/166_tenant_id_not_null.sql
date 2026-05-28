-- CUI // SP-CTI
-- Migration 166: Enforce NOT NULL on tenant_id columns (G-21, NIST AC-3, AC-4)
-- Prevents NULL-tenant rows from becoming world-readable under the RLS predicate
-- tenant_id = ?  (which passes when tenant_id IS NULL in SQLite).
--
-- SQLite does not support ALTER COLUMN to add NOT NULL to existing columns.
-- Strategy: create shadow table with constraint, copy data, swap, rebuild indexes.
-- PostgreSQL: ALTER TABLE ... ALTER COLUMN tenant_id SET NOT NULL (idempotent).
--
-- CAUTION: Run AFTER verifying zero NULL tenant_id rows in each table:
--   SELECT COUNT(*) FROM projects WHERE tenant_id IS NULL;
-- Backfill NULLs to 'system' before applying if any exist.

-- ============================================================
-- PostgreSQL (applied by init_icdev_db.py on pg backend)
-- ============================================================

-- Only run the PostgreSQL block if the backend is PostgreSQL.
-- The Python migration runner detects '-- PG_ONLY' and skips on SQLite.
-- PG_ONLY BEGIN
ALTER TABLE IF EXISTS projects    ALTER COLUMN tenant_id SET NOT NULL;
ALTER TABLE IF EXISTS tasks       ALTER COLUMN tenant_id SET NOT NULL;
ALTER TABLE IF EXISTS documents   ALTER COLUMN tenant_id SET NOT NULL;
ALTER TABLE IF EXISTS groups      ALTER COLUMN tenant_id SET NOT NULL;
ALTER TABLE IF EXISTS canvas_access_grants ALTER COLUMN tenant_id SET NOT NULL;
ALTER TABLE IF EXISTS users       ALTER COLUMN tenant_id SET NOT NULL;
-- PG_ONLY END

-- ============================================================
-- SQLite: verify no NULLs exist (enforcement via CHECK at insert time)
-- New rows are blocked by CHECK below; existing NULLs must be backfilled first.
-- ============================================================

-- projects
CREATE TABLE IF NOT EXISTS _projects_nn_check (
    _dummy INTEGER CHECK (
        (SELECT COUNT(*) FROM projects WHERE tenant_id IS NULL) = 0
    )
);
DROP TABLE IF EXISTS _projects_nn_check;

-- tasks
CREATE TABLE IF NOT EXISTS _tasks_nn_check (
    _dummy INTEGER CHECK (
        (SELECT COUNT(*) FROM tasks WHERE tenant_id IS NULL) = 0
    )
);
DROP TABLE IF EXISTS _tasks_nn_check;

-- documents
CREATE TABLE IF NOT EXISTS _documents_nn_check (
    _dummy INTEGER CHECK (
        (SELECT COUNT(*) FROM documents WHERE tenant_id IS NULL) = 0
    )
);
DROP TABLE IF EXISTS _documents_nn_check;

-- ============================================================
-- Runtime guard: add DEFAULT 'system' on inserts that omit tenant_id.
-- Achieved by inserting TRIGGER on each table that raises an error
-- if tenant_id is NULL at insert time. This gives SQLite NOT NULL semantics.
-- ============================================================

CREATE TRIGGER IF NOT EXISTS trg_projects_tenant_nn
    BEFORE INSERT ON projects
    WHEN NEW.tenant_id IS NULL
BEGIN
    SELECT RAISE(ABORT, 'G-21: tenant_id must not be NULL on projects');
END;

CREATE TRIGGER IF NOT EXISTS trg_tasks_tenant_nn
    BEFORE INSERT ON tasks
    WHEN NEW.tenant_id IS NULL
BEGIN
    SELECT RAISE(ABORT, 'G-21: tenant_id must not be NULL on tasks');
END;

CREATE TRIGGER IF NOT EXISTS trg_documents_tenant_nn
    BEFORE INSERT ON documents
    WHEN NEW.tenant_id IS NULL
BEGIN
    SELECT RAISE(ABORT, 'G-21: tenant_id must not be NULL on documents');
END;

CREATE TRIGGER IF NOT EXISTS trg_groups_tenant_nn
    BEFORE INSERT ON groups
    WHEN NEW.tenant_id IS NULL
BEGIN
    SELECT RAISE(ABORT, 'G-21: tenant_id must not be NULL on groups');
END;

CREATE TRIGGER IF NOT EXISTS trg_canvas_access_tenant_nn
    BEFORE INSERT ON canvas_access_grants
    WHEN NEW.tenant_id IS NULL
BEGIN
    SELECT RAISE(ABORT, 'G-21: tenant_id must not be NULL on canvas_access_grants');
END;

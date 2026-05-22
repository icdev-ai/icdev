-- CUI // SP-CTI
-- src/schema/tables.sql — DDL for src/ module tables

-- "order" is a reserved SQL keyword; must be double-quoted.
-- Columns mirror the schema queried in tools/bdc/boundary_scanner.py:
--   SELECT id, name, design_json FROM {table} ORDER BY updated_at DESC
CREATE TABLE IF NOT EXISTS "order" (
    id          TEXT        PRIMARY KEY,
    name        TEXT        NOT NULL DEFAULT '',
    design_json TEXT        NOT NULL DEFAULT '{}',
    created_at  TEXT        NOT NULL DEFAULT (datetime('now')),
    updated_at  TEXT        NOT NULL DEFAULT (datetime('now'))
);

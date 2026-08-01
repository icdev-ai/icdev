-- CUI // SP-CTI
-- src/schema/tables.sql — DDL for src/ module tables

-- "order" is a reserved SQL keyword; must be double-quoted.
-- RETIRED (bdr-sec-5): the tools/bdc/ package that originally queried this
-- schema has been removed; columns retained for the historical shape below.
--   SELECT id, name, design_json FROM {table} ORDER BY updated_at DESC
CREATE TABLE IF NOT EXISTS "order" (
    id          TEXT        PRIMARY KEY,
    name        TEXT        NOT NULL DEFAULT '',
    design_json TEXT        NOT NULL DEFAULT '{}',
    created_at  TEXT        NOT NULL DEFAULT (datetime('now')),
    updated_at  TEXT        NOT NULL DEFAULT (datetime('now'))
);

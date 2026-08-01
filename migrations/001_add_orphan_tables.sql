-- CUI // SP-CTI
-- migrations/001_add_orphan_tables.sql
-- Adds CREATE TABLE statements for tables referenced by application code
-- but previously missing from any migration (orphan tables).
--
-- "order" is a reserved SQL keyword and must be double-quoted.
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

-- innov_pilots: pilot records for the Innovation canvas.
-- Schema mirrors apps/innovation/db.py and the SELECT in tools/iqe/adapters/innovation.py.
-- innov_ideas must exist before this table is created (FK dependency).
CREATE TABLE IF NOT EXISTS innov_pilots (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    idea_id          INTEGER NOT NULL REFERENCES innov_ideas(id),
    pilot_name       TEXT    NOT NULL,
    hypothesis       TEXT,
    success_criteria TEXT,
    kanban_epic_id   TEXT,
    start_date       TEXT,
    end_date         TEXT,
    status           TEXT    NOT NULL DEFAULT 'planned',
    created_at       TEXT    NOT NULL DEFAULT (datetime('now'))
);

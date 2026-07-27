-- Migration 274: AI GameDay ontology tagging column parity (penta-gd-03).
-- CUI // SP-CTI
--
-- apps/ai_gameday/blueprint.py reads and writes ttx_sessions.ontology_tags_json
-- and ttx_injects.ontology_tags_json (create-session enrichment, the leaderboard
-- ontology filter, and the /api/gameday/session/<id>/ontology route), but the
-- column shipped in NO runtime DDL: apps/ai_gameday/db.py::_DDL lacked it and the
-- consolidated PostgreSQL snapshot's ttx_sessions / ttx_injects CREATE TABLE
-- blocks lacked it. On PostgreSQL, create-session 400'd and the ontology /
-- leaderboard routes 500'd. This migration brings a production PostgreSQL
-- instance (bootstrapped from the consolidated snapshot or migrated via the main
-- chain) up to parity WITHOUT first running the runtime initializer.
--
-- Parity source of truth: apps/ai_gameday/db.py::_DDL (which now also creates the
-- column for fresh installs and applies a guarded ALTER-ADD for pre-existing
-- tables at runtime). pg_consolidated.sql now carries the column inline as well.
--
-- Both statements are idempotent via ADD COLUMN IF NOT EXISTS so this migration
-- coexists with the runtime db.py::migrate() guarded add on existing installs —
-- running either first is safe. NO data seeding here (blueprint.py fills the
-- column from resolve_scenario_ontology() when a session is created).

ALTER TABLE ttx_sessions ADD COLUMN IF NOT EXISTS ontology_tags_json TEXT DEFAULT '{}';
ALTER TABLE ttx_injects  ADD COLUMN IF NOT EXISTS ontology_tags_json TEXT DEFAULT '{}';

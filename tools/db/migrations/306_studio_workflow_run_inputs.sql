-- CUI // SP-CTI
-- Migration 306: inputs_json on studio_workflow_runs (dwo-evt-04).
--
-- A triggered run (migration 304: studio_workflow_triggers.input_mapping_json)
-- computes a set of inputs from the incoming event, but the run row had nowhere
-- to keep them. Without this column the answer to "what was this run started
-- with" is only reconstructable by re-reading the trigger event and re-applying
-- the mapping — and not at all for a manual run.
--
-- Nullable with no default: NULL means "no inputs were recorded" and is
-- distinct from '{}' meaning "started with an empty input set".
--
-- JSON is stored as TEXT/jsonb and parsed in Python — never with SQLite JSON
-- functions (see the CLAUDE.md PG portability rule).

-- @pg-only
ALTER TABLE IF EXISTS studio_workflow_runs ADD COLUMN IF NOT EXISTS inputs_json TEXT;
-- @all

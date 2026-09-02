-- Migration: 20260902214729_oscal_artifacts_assessment_plan
-- CUI // SP-CTI
--
-- Widen oscal_artifacts.artifact_type to accept 'assessment_plan' (rmf-oscal-01).
--
-- WHY THIS IS NOT OPTIONAL, and why it would have been invisible.
-- generate_oscal_assessment_plan() records what it produced through
-- _store_oscal_artifact(), whose INSERT is wrapped in
-- `except Exception: print("Warning: ...", file=sys.stderr)`. Without this
-- migration the CHECK refuses the new value, the warning goes to stderr where
-- nothing reads it, and the generator returns a successful result with a valid
-- artifact on disk that /api/oscal/artifacts and the OSCAL dashboard page will
-- never list. That is the exact shape CLAUDE.md names -- "the feature reports
-- success while persisting nothing" -- and editing the CREATE TABLE in
-- init_icdev_db.py alone does not fix it, because CREATE TABLE IF NOT EXISTS
-- never alters an existing table. init_icdev_db.py is updated too, for fresh
-- databases; this migration is what reaches the ones already running.

-- @pg-only
-- Drop-then-add is what makes this re-runnable: a second run drops the wide
-- constraint it just created and puts it back identically.
ALTER TABLE oscal_artifacts DROP CONSTRAINT IF EXISTS oscal_artifacts_artifact_type_check;

ALTER TABLE oscal_artifacts ADD CONSTRAINT oscal_artifacts_artifact_type_check
    CHECK (artifact_type IN (
        'ssp', 'poam', 'assessment_results', 'assessment_plan',
        'component_definition', 'catalog', 'profile'));

-- @sqlite-only
-- Deliberately a no-op, following 20260803201015_proposal_reviews_white_team_review_type.
-- SQLite cannot ALTER a CHECK constraint; changing one means rebuilding the
-- table, which is too blunt to run unconditionally against a database this
-- migration cannot inspect. SQLite is the init-fallback backend and its fresh
-- schema comes from init_icdev_db.py, which now carries 'assessment_plan'.
-- A long-lived SQLite database therefore keeps the narrow constraint and its
-- assessment-plan artifact record is refused -- visibly, in the same warning
-- path as before, and never producing a WRONG row.
SELECT 1;

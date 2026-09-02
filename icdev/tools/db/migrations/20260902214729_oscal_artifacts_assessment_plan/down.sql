-- Rollback: 20260902214729_oscal_artifacts_assessment_plan
-- CUI // SP-CTI
--
-- Narrow oscal_artifacts.artifact_type back to the pre-assessment_plan set.
--
-- This will FAIL if any row already holds artifact_type='assessment_plan' —
-- PostgreSQL validates a new CHECK against existing rows. That is the correct
-- behaviour: silently deleting the record of a generated compliance artifact to
-- make a rollback succeed would drop the only pointer to a file still sitting on
-- disk. Remove those rows deliberately first.

-- @pg-only
ALTER TABLE oscal_artifacts DROP CONSTRAINT IF EXISTS oscal_artifacts_artifact_type_check;

ALTER TABLE oscal_artifacts ADD CONSTRAINT oscal_artifacts_artifact_type_check
    CHECK (artifact_type IN (
        'ssp', 'poam', 'assessment_results',
        'component_definition', 'catalog', 'profile'));

-- @sqlite-only
-- No-op, mirroring up.sql.
SELECT 1;

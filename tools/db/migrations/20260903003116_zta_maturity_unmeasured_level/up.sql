-- CUI // SP-CTI
-- Admit 'unmeasured' into zta_maturity_scores.maturity_level (rmf-zt-02).
--
-- The scorer used to average a posture_score of current/declared over
-- zta_posture_evidence rows whose evidence_data is NULL -- a ratio over a
-- checkbox list -- and, when the table held no rows at all, over a numerator
-- that is structurally 0. Either way it persisted a NUMBER and a maturity
-- BAND for a pillar nobody has ever assessed, which reads on every downstream
-- surface (cato_monitor's cato_contribution, the ZIG bridge, the MCP
-- zta_posture_check) exactly like a measured 'traditional'.
--
-- A pillar with no evidence-backed signal now persists score = NULL and
-- maturity_level = 'unmeasured'. `score` is already nullable and its CHECK
-- passes on NULL (NULL >= 0.0 is NULL, which is not FALSE), so only the
-- maturity_level CHECK has to widen.
--
-- Measured on the live PostgreSQL board 2026-09-02: zta_posture_evidence and
-- zta_maturity_scores both hold 0 rows, so this widens a constraint no
-- existing row can violate.

-- @pg-only
-- Drop-then-add is what makes this re-runnable: a second run drops the wide
-- constraint it just created and puts it back identically.
ALTER TABLE zta_maturity_scores DROP CONSTRAINT IF EXISTS zta_maturity_scores_maturity_level_check;

ALTER TABLE zta_maturity_scores ADD CONSTRAINT zta_maturity_scores_maturity_level_check
    CHECK (maturity_level IN ('traditional', 'advanced', 'optimal', 'unmeasured'));

-- @sqlite-only
-- Deliberately a no-op. SQLite cannot ALTER a CHECK constraint; changing one
-- means rebuilding the table, and SQLite is the init-fallback backend whose
-- fresh schema already comes from init_icdev_db.py with 'unmeasured' present.
-- A table rebuild is too blunt to run unconditionally against a database this
-- migration cannot inspect.
SELECT 1;

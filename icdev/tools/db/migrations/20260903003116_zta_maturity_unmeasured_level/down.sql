-- Rollback: 20260903003116_zta_maturity_unmeasured_level
-- CUI // SP-CTI
--
-- Narrowing the constraint again would fail on any row the scorer has since
-- written as 'unmeasured', so those rows are retired to NULL first --
-- maturity_level is nullable and a NULL fails no CHECK. Retiring the band
-- rather than inventing a level for those rows is deliberate: 'traditional'
-- is the exact false reading this migration exists to remove.

-- @pg-only
UPDATE zta_maturity_scores SET maturity_level = NULL WHERE maturity_level = 'unmeasured';

ALTER TABLE zta_maturity_scores DROP CONSTRAINT IF EXISTS zta_maturity_scores_maturity_level_check;

ALTER TABLE zta_maturity_scores ADD CONSTRAINT zta_maturity_scores_maturity_level_check
    CHECK (maturity_level IN ('traditional', 'advanced', 'optimal'));

-- @sqlite-only
SELECT 1;

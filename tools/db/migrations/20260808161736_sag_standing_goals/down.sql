-- CUI // SP-CTI
-- Rollback 20260808161736_sag_standing_goals.
--
-- Standing goals are operator-authored state, not derived data: dropping the
-- table loses them. Indexes go first so the rollback is also safe on engines
-- that do not cascade index drops with the table.

DROP INDEX IF EXISTS idx_sag_standing_goals_context;
DROP INDEX IF EXISTS idx_sag_standing_goals_owner_status;
DROP TABLE IF EXISTS sag_standing_goals;

-- Rollback: 20260902223458_asset_visibility_snapshots
-- CUI // SP-CTI
--
-- Dropping this table DESTROYS EVIDENCE, and it is not re-derivable. Every
-- other rollback in this tree drops a projection whose facts can be
-- recomputed; a visibility snapshot records what could be seen AT A MOMENT,
-- and re-running the measurement tomorrow answers a different question. An
-- RMF/cATO package's coverage history is exactly this series.
--
-- Kept reversible only so `migrate.py --down` has a definition. Take a copy
-- of the table before running it.

DROP TABLE IF EXISTS asset_visibility_snapshots;

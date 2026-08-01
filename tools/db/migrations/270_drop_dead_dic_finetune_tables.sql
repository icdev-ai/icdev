-- Migration 270: drop the dead DIC fine-tuning tables
--
-- The DIC /finetune surface (route + template + engine) was removed as a
-- genuinely dead feature. Its tables were left behind: dic_finetune_jobs,
-- dic_finetune_models (migration 187) and dic_ft_datasets, dic_ft_jobs,
-- dic_ft_models. On the live corpus all five hold zero rows, and the only
-- references remaining anywhere in the tree are historical kanban task
-- descriptions — no runtime module reads or writes them, no test uses them, and
-- the DIC init_db does not (re)create them.
--
-- Empty, unreferenced, and no longer part of any surface: drop them. CASCADE
-- clears the attached sequences/indexes. IF EXISTS keeps this idempotent and
-- safe on any DB where a table was never created.
--
-- pg_consolidated.sql still carries these definitions; a fresh install will
-- create them and this migration will drop them again on migrate. The next
-- schema squash removes them from the consolidated dump.

DROP TABLE IF EXISTS dic_finetune_jobs CASCADE;
DROP TABLE IF EXISTS dic_finetune_models CASCADE;
DROP TABLE IF EXISTS dic_ft_datasets CASCADE;
DROP TABLE IF EXISTS dic_ft_jobs CASCADE;
DROP TABLE IF EXISTS dic_ft_models CASCADE;

-- Migration: 20260819011454_pr_merge_eligibility_events (down)
-- CUI // SP-CTI
--
-- This migration is the sole creator of the table, so the inverse of "create it" is
-- "drop it". Note what that discards: the recorded first-seen-ready history every
-- `awaiting_merge` age is measured from. After a down-migration the stall report
-- degrades to `ready_since_source: ci_estimate` (and to `unmeasured` where even that
-- is unavailable) rather than reporting a confident zero.

DROP TABLE IF EXISTS pr_merge_eligibility_events;

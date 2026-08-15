-- Rollback: 20260815063941_trust_hitl_deltas
-- CUI // SP-CTI
--
-- This drops EVIDENCE, not state. Every row in trust_deltas is the only record
-- of what a human actually approved when they overrode a TRUST gate — the
-- corresponding approval_items row carries the disposition and the
-- agent_approval_log row carries the decision, but neither holds the before/after
-- text, and nothing else in the platform does either. Rolling this back returns
-- the platform to recording THAT an override happened and never WHAT CHANGED,
-- which is the defect trust-hitl-01 exists to close.
--
-- Take a copy before running it if the deployment has ever served an override.

DROP INDEX IF EXISTS idx_trust_deltas_stage;
DROP INDEX IF EXISTS idx_trust_deltas_supersedes;
DROP INDEX IF EXISTS idx_trust_deltas_approval_item;
DROP INDEX IF EXISTS idx_trust_deltas_artifact;
DROP TABLE IF EXISTS trust_deltas;

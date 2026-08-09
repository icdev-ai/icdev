-- Rollback: 20260808195754_resync_hook_events_id_sequence
-- CUI // SP-CTI
--
-- Intentionally a no-op.
--
-- The "before" state was a sequence desynced from its table, which made every
-- INSERT into hook_events fail. There is nothing to restore: rewinding the
-- sequence would only reinstate the UniqueViolation and lose audit rows again.
-- Forward-only by design.

SELECT 1;

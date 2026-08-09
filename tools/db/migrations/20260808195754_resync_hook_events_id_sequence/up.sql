-- CUI // SP-CTI
-- Resync hook_events_id_seq with the table (hgx-guard-02).
--
-- `hook_events.id` defaults to nextval('hook_events_id_seq'), but a bulk import
-- inserted rows with EXPLICIT ids (up to 900004) without advancing the sequence,
-- which was left at 1. Every subsequent INSERT therefore raised
--   UniqueViolation: Key (id)=(1) already exists
-- and, because tools/airgap/hook_compat.py caught only sqlite3.OperationalError,
-- the exception propagated out of run_pre_tool_check — so AUDITING a blocked
-- tool call took the safety guard down with it.
--
-- The code no longer lets an audit failure escape; this repairs the cause, so
-- the NIST AU rows actually land again.
--
-- setval(..., false) means "the NEXT nextval() returns this value", so the first
-- new row gets max(id)+1. Idempotent: re-running it recomputes from the table.
-- GREATEST(...) keeps it valid on an empty table, where MAX(id) is NULL.

SELECT setval(
    'hook_events_id_seq',
    GREATEST(COALESCE((SELECT MAX(id) FROM hook_events), 0) + 1, 1),
    false
);

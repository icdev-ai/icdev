-- Migration: 20260809203855_agov_approval_items
-- CUI // SP-CTI
--
-- agov-inbox-01 — the pending-approval store for the unified approval inbox.
--
-- WHY THIS TABLE IS MUTABLE, AND WHY THAT IS NOT A REGRESSION
--
-- A pending approval and an approval decision are two different things with two
-- different lifetimes, and ICDEV already has the second one.
--
--   agent_approval_log  migration 20260803002224, APPEND-ONLY, in
--                       APPEND_ONLY_TABLES. It is EVIDENCE. A decision was made
--                       once, by someone, for a stated reason, and correcting
--                       one means appending a correction.
--
--   approval_items      this table. It is short-lived STATE. An item is created
--                       pending, then moves exactly once to resolved, expired or
--                       cancelled. That transition IS an UPDATE.
--
-- So this table is deliberately NOT append-only and is deliberately NOT added to
-- APPEND_ONLY_TABLES in .claude/hooks/pre_tool_use.py. Conflating the two is how
-- you end up unable to resolve an item without violating the audit invariant:
-- append-only would force a second row per state change, and "which row is the
-- current state" then has no answer the gate can trust.
--
-- The two are joined at resolution time, not in the schema:
-- approval_inbox.resolve() writes the permanent agent_approval_log row through
-- the existing approval_gate.record_decision(), so the mutable row can be pruned
-- later without losing the decision.
--
-- NO ARGUMENT VALUES, BY CONSTRUCTION
--
-- arg_keys holds KEY NAMES ONLY and input_sha256 a digest of the flattened
-- input, exactly as agent_approval_log and runtime_invocations (migration 341)
-- do. Tool arguments can carry CUI and this table's whole purpose is to be
-- MIRRORED OUT to Slack/Teams/Telegram/email, so it is the last place a raw
-- argument value may sit. `title` and `body` are a rendered summary produced by
-- approval_inbox.render_summary -- tier, rule, policy prose and argument key
-- names -- never a dump of the arguments. Note that ApprovalRequest.summary()
-- is NOT safe for this purpose: it previews the `command` / `path` / `file_path`
-- VALUE. The digest still proves two records describe the same call.
--
-- These three columns are the only additions beyond the card's column list, and
-- they exist so resolve() can emit a FAITHFUL agent_approval_log row rather than
-- a lossy reconstruction. All three are already established as CUI-safe by
-- migration 20260803002224.
--
-- VOCABULARY LIVES IN PYTHON, NOT IN A CHECK CONSTRAINT
--
-- origin (sag|ace|workflow_hitl), state (pending|resolved|expired|cancelled) and
-- resolution (approved|denied) are validated by ORIGINS / STATES / RESOLUTIONS
-- in tools/agent_runtime/approval_inbox.py. Same call as migration
-- 20260803002224 made for tier/decision: a CHECK constraint here would be a
-- second hardcoded copy that drifts, and agov-inbox-05 adds origins. The columns
-- are NOT NULL, the Python constants remain the single source of the vocabulary,
-- and the store refuses an out-of-vocabulary value before the INSERT.

CREATE TABLE IF NOT EXISTS approval_items (
    item_id         TEXT PRIMARY KEY,
    session_id      TEXT,
    origin          TEXT NOT NULL,
    tool_name       TEXT NOT NULL,
    tier            TEXT NOT NULL,
    title           TEXT NOT NULL,
    body            TEXT,
    inbox           TEXT,
    state           TEXT NOT NULL,
    resolution      TEXT,
    resolved_by     TEXT,
    resolved_at     TEXT,
    expires_at      TEXT,
    rule            TEXT,
    arg_keys        TEXT,
    input_sha256    TEXT,
    classification  TEXT DEFAULT 'CUI',
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at      TEXT
);

-- The dominant read is "what is still pending in this inbox" -- the poll every
-- waiting approver runs. Then "everything for this session" for the CASE epic's
-- timeline, and "what has expired" for the sweep.
CREATE INDEX IF NOT EXISTS idx_approval_items_inbox_state
    ON approval_items (inbox, state);
CREATE INDEX IF NOT EXISTS idx_approval_items_state
    ON approval_items (state);
CREATE INDEX IF NOT EXISTS idx_approval_items_session
    ON approval_items (session_id);
CREATE INDEX IF NOT EXISTS idx_approval_items_expires
    ON approval_items (expires_at);

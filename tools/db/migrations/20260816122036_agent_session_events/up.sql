-- Migration: 20260816122036_agent_session_events
-- CUI // SP-CTI
--
-- hcx-evt-01 — the append-only agent event log.
--
-- WHAT WAS MISSING
--
-- `icdev/tools/llm/agent_loop_session.py::save_session` persists an agent run as
-- ONE `messages_json` blob, UPSERT-overwritten on every turn:
--
--     INSERT INTO agent_loop_sessions (... messages_json ...) VALUES (...)
--     ON CONFLICT (session_id) DO UPDATE SET messages_json = excluded.messages_json
--
-- That is enough to RESUME a session and nothing else. There is no record of
-- turn N once turn N+1 has been written, so forking a run at an earlier turn and
-- replaying a run step by step are not merely unimplemented — the data they
-- would need has already been overwritten. The blob is also the only artifact,
-- so "what did the model see, and when" is answerable only by re-deriving it
-- from a transcript that no longer exists.
--
-- The obvious second candidate does not cover it either. `llm_gateway_audit`
-- stores hashes only, and it is imported by `tools/cortex/*` and
-- `tools/ops_hub/llmops_engine.py` — nothing on the agent-runtime path. SAG's
-- own LLM calls are therefore unaudited today.
--
-- This table is the missing half: one immutable row per model-visible event, in
-- order, hashed.
--
-- WHY THE MAIN DATABASE AND NOT THE CANVAS DATABASE
--
-- Stated explicitly because CLAUDE.md's canvas rule cuts the other way, and
-- `agent_loop_sessions` — the table this one complements — lives in the ACE
-- canvas DB behind `get_canvas_connection(ACE_DB_PATH)`.
--
-- Two reasons override that here:
--
--   1. RLS. This table carries `tenant_id` and `classification` precisely so the
--      predicate `get_connection()` injects applies to it. A canvas table has
--      neither column, so every read would be unfiltered — and these rows can
--      hold verbatim model input.
--
--   2. The join. `tools/agent_case/session_timeline.py` builds one ordered
--      timeline per `session_id` out of `hook_events` and `audit_trail`, both of
--      which are in the main DB. A canvas-resident event log could not be joined
--      to either, and the timeline's own docstring already names three tables it
--      cannot correlate for exactly that kind of reason.
--
-- APPEND-ONLY, AND REGISTERED AS SUCH
--
-- Registered in APPEND_ONLY_TABLES in .claude/hooks/pre_tool_use.py. No row here
-- is ever UPDATEd or DELETEd, and `tools/agent_runtime/event_log.py` exposes no
-- function that could: the module is `append()`, `read_session()`, `next_seq()`.
-- A correction is a new event, not an edit — the same rule
-- `sbom_records.supersedes_sbom_id` and `trust_deltas.supersedes_delta_id`
-- follow.
--
-- ORDERING IS `seq`, NOT `occurred_at`
--
-- `occurred_at` is a wall clock and two events inside one turn routinely share a
-- millisecond, so a timestamp cannot totally order a session. `seq` is monotonic
-- per session starting at 1, and `idx_agent_session_events_seq` is UNIQUE over
-- (session_id, seq) — which is what makes it safe for two concurrent writers to
-- allocate a number optimistically: the loser gets a constraint violation and
-- retries, rather than silently writing a duplicate position that replay would
-- resolve arbitrarily. `occurred_at` is kept for correlation with hook_events
-- and audit_trail, never for ordering.
--
-- payload_hash IS ALWAYS WRITTEN; payload_json IS NOT
--
-- `payload_hash` is NOT NULL. It is the SHA-256 of the canonical payload
-- rendering computed by `tools/audit/row_hash.py::compute_payload_hash` — the
-- same module, the same algorithm and the same encoding constants the
-- migration-149 audit chain uses, so there is one hashing recipe in this
-- codebase and not two.
--
-- `payload_json` is NULLABLE because retaining verbatim model input at rest is a
-- classification decision, not a storage decision. `event_log.append()` consults
-- `args/agent_event_log.yaml` and stores the document only when the event's
-- classification is at or below the configured ceiling. A NULL `payload_json`
-- beside a NOT NULL `payload_hash` therefore means WITHHELD BY POLICY, not
-- "nothing happened" — the hash still proves what the payload was to anyone who
-- holds a copy of it.
--
-- VOCABULARY LIVES IN PYTHON, NOT IN A CHECK CONSTRAINT
--
-- `event_type` is validated by EVENT_TYPES in tools/agent_runtime/event_log.py.
-- Same call migrations 20260803002224, 20260809203855 and 20260815063941 made: a
-- CHECK here is a second hardcoded copy of the vocabulary, and it drifts the
-- first time an event type is added.
--
-- payload_json IS TEXT AND IS PARSED IN PYTHON
--
-- No runtime query filters or groups inside it, so there is no json_extract /
-- jsonb branch to keep portable (CLAUDE.md: compute in Python).

CREATE TABLE IF NOT EXISTS agent_session_events (
    event_id        TEXT PRIMARY KEY,
    session_id      TEXT NOT NULL,
    seq             INTEGER NOT NULL,
    event_type      TEXT NOT NULL,
    occurred_at     TEXT NOT NULL,
    payload_hash    TEXT NOT NULL,
    payload_json    TEXT,
    tenant_id       TEXT NOT NULL DEFAULT 'default',
    classification  TEXT NOT NULL DEFAULT 'CUI',
    correlation_id  TEXT
);

-- The ordering invariant, enforced by the database rather than by the writer.
-- UNIQUE so a lost race is a constraint violation the writer can retry, never a
-- duplicate position.
CREATE UNIQUE INDEX IF NOT EXISTS idx_agent_session_events_seq
    ON agent_session_events (session_id, seq);

-- The dominant read is "every event for this session, in order" — read_session()
-- and, once hcx-evt-02..06 land, fork and replay.
CREATE INDEX IF NOT EXISTS idx_agent_session_events_session
    ON agent_session_events (session_id);

-- RLS injects `tenant_id = ?` into every SELECT this table serves, so the
-- planner never sees the module's SQL shape — index the tenant alongside the
-- range column the query actually orders on.
CREATE INDEX IF NOT EXISTS idx_agent_session_events_tenant
    ON agent_session_events (tenant_id, occurred_at);

-- The cross-table join: one correlation_id spans an event here, the hook_events
-- row for the same tool call, and the audit_trail entry that recorded it.
CREATE INDEX IF NOT EXISTS idx_agent_session_events_correlation
    ON agent_session_events (correlation_id);

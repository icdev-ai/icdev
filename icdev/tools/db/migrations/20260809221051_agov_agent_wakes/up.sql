-- Migration: 20260809221051_agov_agent_wakes
-- CUI // SP-CTI
--
-- agov-wake-01 — the wake store that lets an agent suspend itself.
--
-- WHAT THIS ADDS THAT ICDEV DOES NOT ALREADY HAVE
--
-- ICDEV has plenty of scheduling, and all of it is EXTERNAL to the agent:
-- 124 Genesis reflexes on fixed cadences, agent_cron_jobs (migration 289) where
-- an operator declares a schedule up front, and the kanban scheduler. Every one
-- of them answers "run this thing every N minutes". None answers "the agent
-- decided, mid-turn, to stop here and be resumed when PR #1342 goes CI-green".
-- That is one row per suspension, created by the agent, with a condition that
-- something else in the platform later satisfies.
--
--   agent_cron_jobs  operator-declared, recurring, schedule known at create time.
--   agent_wakes      agent-declared, single-shot, condition satisfied by an
--                    event that has not happened yet.
--
-- THE STATE MACHINE IS ONE-DIRECTIONAL, AND THE SCHEMA IS NOT WHAT ENFORCES IT
--
--   pending  -> due  -> fired
--   pending  -> cancelled
--   due      -> cancelled
--
-- There is no edge back. A wake that has fired is spent, and a wake that was
-- cancelled is spent. What enforces this is not a trigger or a CHECK: it is that
-- every transition in tools/agent_runtime/wake.py is a CONDITIONAL UPDATE naming
-- the state it is allowed to move FROM, and the caller learns from rowcount
-- whether it was the one that moved it. Read-then-write would let two ticks of
-- the reflex, or two sessions against the one shared database, both observe
-- `due` and both fire the same wake -- and a wake firing twice means an agent
-- resumed twice from one suspension.
--
-- MUTABLE ON PURPOSE, AND DELIBERATELY NOT IN APPEND_ONLY_TABLES
--
-- A wake is short-lived STATE, not evidence: it is created pending and moves at
-- most twice before it is spent. Adding it to APPEND_ONLY_TABLES in
-- .claude/hooks/pre_tool_use.py would make the pending -> due -> fired
-- transitions -- the only reason the table exists -- hook violations, and the
-- symptom would be a feature that simply stops resuming anything. The permanent
-- record of what an agent actually did after it woke belongs to the existing
-- append-only trails (agent_approval_log, hook_events, audit_trail), which this
-- table does not duplicate.
--
-- ONE ROW, THREE KINDS, THREE NULLABLE CONDITION COLUMNS
--
-- fire_at, job_id and event_key are the condition, and exactly one is populated
-- per row -- selected by `kind`. Three narrow columns beat one opaque
-- condition_json blob because each is directly indexable, which is what the
-- three promotion queries need: fire_at for the timer sweep, job_id for
-- complete_job, event_key for fire_event. A NOT NULL constraint per kind is not
-- expressible without a CHECK per kind, so the store validates instead -- see
-- the vocabulary note below, and note that a wake created with no condition at
-- all would simply never be promoted, never fire, and never be visible as a bug.
-- wake.py refuses it before the INSERT.
--
-- fire_at is a UTC ISO-8601 TEXT timestamp written to a FIXED microsecond width
-- by wake.py, so lexicographic ordering equals chronological ordering and the
-- `fire_at <= now` sweep is a plain string comparison on both backends. That is
-- the same convention agent_cron_jobs.next_run_at uses.
--
-- VOCABULARY LIVES IN PYTHON, NOT IN A CHECK CONSTRAINT
--
-- kind (timer|completion|event) and state (pending|due|fired|cancelled) are
-- validated by KINDS / STATES in tools/agent_runtime/wake.py. Same call
-- migrations 289 and 20260803002224 made for mode and tier: a CHECK here would
-- be a second hardcoded copy of a Python constant, and CLAUDE.md requires the
-- two be kept in lockstep if both exist. The columns are NOT NULL, the Python
-- constants remain the single source, and the store refuses an
-- out-of-vocabulary value before the INSERT rather than after it.
--
-- tenant_id and classification are the only additions beyond the card's column
-- list. They are not decoration: get_connection injects an RLS predicate over
-- BOTH columns whenever a security context is set, so a table without them
-- raises UndefinedColumn on every query the moment this store is used from a
-- tenant-scoped session. agent_cron_jobs carries the same pair for the same
-- reason.
--
-- Conventions mirror migration 289: TEXT-only, dialect-neutral, idempotent
-- CREATE TABLE IF NOT EXISTS (PostgreSQL primary, SQLite init/test fallback).
-- wake.py also self-creates the table via _ensure_schema, so a checkout that has
-- not migrated still works.

CREATE TABLE IF NOT EXISTS agent_wakes (
    wake_id        TEXT PRIMARY KEY,
    session_id     TEXT NOT NULL,
    kind           TEXT NOT NULL,          -- 'timer' | 'completion' | 'event'
    state          TEXT NOT NULL,          -- 'pending' | 'due' | 'fired' | 'cancelled'
    fire_at        TEXT,                   -- kind='timer': UTC ISO-8601, fixed width
    job_id         TEXT,                   -- kind='completion': the job awaited
    event_key      TEXT,                   -- kind='event': the event key awaited
    note           TEXT,                   -- why the agent suspended, for the operator
    tenant_id      TEXT DEFAULT '',
    classification TEXT DEFAULT 'CUI',
    created_at     TEXT,
    updated_at     TEXT
);

-- The timer sweep: pending timers whose fire_at has passed.
CREATE INDEX IF NOT EXISTS idx_agent_wakes_timer
    ON agent_wakes (state, kind, fire_at);
-- complete_job: pending completion wakes for one job.
CREATE INDEX IF NOT EXISTS idx_agent_wakes_job
    ON agent_wakes (state, job_id);
-- fire_event: pending event wakes for one key.
CREATE INDEX IF NOT EXISTS idx_agent_wakes_event
    ON agent_wakes (state, event_key);
-- pending(session_id): what one agent session is waiting on.
CREATE INDEX IF NOT EXISTS idx_agent_wakes_session
    ON agent_wakes (session_id, state);

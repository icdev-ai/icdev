-- Migration: 20260815063956_trust_hitl_deltas
-- CUI // SP-CTI
--
-- trust-hitl-01/02 — the DELTA becomes the reviewable unit.
--
-- WHAT THIS FIXES
--
-- Today a force_* override records THAT a human overrode, never WHAT CHANGED.
-- `idr_publish_audit`, `agent_approval_log` and the pulse.py force_reason all
-- answer "who cleared this gate, and why did they say they did" — none of them
-- answers "what text did the human actually accept". A reviewer approving a
-- self-corrected draft is approving a diff they have never been shown, which
-- makes the approval unauditable after the fact and indistinguishable from a
-- rubber stamp.
--
-- WHY THIS TABLE IS APPEND-ONLY, AND ITS TWIN IS NOT
--
-- This is the same split migration 20260809203855 made, for the same reason,
-- and the split is deliberate on both sides:
--
--   trust_deltas    this table. APPEND-ONLY EVIDENCE, registered in
--                   APPEND_ONLY_TABLES in .claude/hooks/pre_tool_use.py. A
--                   delta is an observation: this text became that text, these
--                   findings became those findings, at this instant. An
--                   observation is never edited.
--
--   approval_items  migration 20260809203855. MUTABLE STATE, deliberately NOT
--                   in APPEND_ONLY_TABLES. The human's disposition is created
--                   `pending` and moves exactly once to a terminal state. That
--                   transition IS an UPDATE, and forcing it to be an append
--                   would leave "which row is the current state" with no answer
--                   the gate can trust.
--
-- The two are joined by `approval_item_id`, at write time, not in the schema —
-- so an approval item may be pruned later without losing the evidence of what
-- was reviewed.
--
-- A CORRECTION IS A SUCCESSOR, NEVER AN EDIT
--
-- `supersedes_delta_id` points at the row this one corrects or settles, exactly
-- as `sbom_records.supersedes_sbom_id` does (and for the identical reason: a
-- recipient may already hold the document the old row describes). Nothing ever
-- UPDATEs a predecessor — not even to flag it superseded. That flag is DERIVED
-- at read time by `hitl_delta.delta_chain`. A human's approve/deny therefore
-- APPENDS a `settlement` delta alongside the `pending` one it settles; the
-- pending row stays exactly as it was written.
--
-- VOCABULARY LIVES IN PYTHON, NOT IN A CHECK CONSTRAINT
--
-- `stage` (self_correction|override|manual_edit|settlement) and `disposition`
-- (pending|approved|denied) are validated by DELTA_STAGES / DISPOSITIONS in
-- tools/quality/hitl_delta.py before the INSERT. Same call migrations
-- 20260803002224 and 20260809203855 made for tier/decision/state: a CHECK here
-- is a second hardcoded copy that drifts out of sync with the constant, and
-- trust-hitl-03 adds override call sites. The columns are NOT NULL and the
-- store refuses an out-of-vocabulary value.
--
-- ON STORING before_text / after_text
--
-- These ARE the artifact, and the artifact is the thing under review — a delta
-- that stores only hashes cannot be rendered side by side, which is the entire
-- point. So the row carries the text and carries `classification` + `tenant_id`
-- to be RLS-eligible through get_connection(), the same posture every other
-- table holding drafted CUI takes. This is the opposite call from
-- approval_items, and deliberately so: THAT table is mirrored out to Slack /
-- Teams / Telegram, which is why it may hold only argument KEY NAMES. This one
-- is never mirrored anywhere; it is read by the review panel and by nothing
-- else.

CREATE TABLE IF NOT EXISTS trust_deltas (
    delta_id            TEXT PRIMARY KEY,
    artifact_id         TEXT NOT NULL,
    artifact_type       TEXT,
    stage               TEXT NOT NULL,
    gate                TEXT,
    before_hash         TEXT NOT NULL,
    after_hash          TEXT NOT NULL,
    before_text         TEXT,
    after_text          TEXT,
    findings_before     TEXT,
    findings_after      TEXT,
    findings_before_n   INTEGER DEFAULT 0,
    findings_after_n    INTEGER DEFAULT 0,
    spans               TEXT,
    actor               TEXT,
    rationale           TEXT,
    disposition         TEXT NOT NULL DEFAULT 'pending',
    approval_item_id    TEXT,
    supersedes_delta_id TEXT,
    session_id          TEXT,
    tenant_id           TEXT,
    classification      TEXT DEFAULT 'CUI',
    created_at          TEXT DEFAULT CURRENT_TIMESTAMP
);

-- The dominant read is the review panel's "what is still pending", then "the
-- whole history of this artifact" for the side-by-side chain, then the
-- successor lookup that derives whether a row has been superseded.
CREATE INDEX IF NOT EXISTS idx_trust_deltas_disposition
    ON trust_deltas (disposition);
CREATE INDEX IF NOT EXISTS idx_trust_deltas_artifact
    ON trust_deltas (artifact_id);
CREATE INDEX IF NOT EXISTS idx_trust_deltas_supersedes
    ON trust_deltas (supersedes_delta_id);
CREATE INDEX IF NOT EXISTS idx_trust_deltas_created
    ON trust_deltas (created_at);
CREATE INDEX IF NOT EXISTS idx_trust_deltas_approval_item
    ON trust_deltas (approval_item_id);

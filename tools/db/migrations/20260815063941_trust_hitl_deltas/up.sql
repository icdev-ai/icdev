-- Migration: 20260815063941_trust_hitl_deltas
-- CUI // SP-CTI
--
-- trust-hitl-01 — the delta becomes the reviewable unit.
--
-- WHAT WAS MISSING
--
-- Every force_* override in this codebase records THAT a human overrode a gate
-- and never WHAT CHANGED. `response_drafter.approve_draft` writes
-- "citation_guard_override: Draft <id> promoted by <reviewer> despite N citation
-- defect(s)" and the draft text itself — the thing the reviewer actually
-- approved — is nowhere in the record. A reviewer cannot review a count, and an
-- auditor reading that row six months later cannot tell whether the override
-- fixed the artifact or waved it through unchanged.
--
-- This table is the missing half: the before/after pair, decomposed into
-- claim-anchored spans, with the gate findings on each side.
--
-- WHY THIS ONE IS APPEND-ONLY AND approval_items IS NOT
--
-- Same split migration 20260809203855 made, for the same reason, and the two
-- tables are meant to be read together:
--
--   trust_deltas    this table. APPEND-ONLY, registered in APPEND_ONLY_TABLES
--                   in .claude/hooks/pre_tool_use.py. It is EVIDENCE: this text
--                   became that text, under these findings, at this moment.
--                   That fact never stops being true, so no row here is ever
--                   UPDATEd or DELETEd.
--
--   approval_items  migration 20260809203855. MUTABLE, deliberately. It is
--                   STATE: the human's disposition, created pending and moved
--                   exactly once to resolved/expired/cancelled. That transition
--                   IS an UPDATE.
--
-- The join is `approval_item_id` below, written at INSERT time because
-- approval_inbox.enqueue() accepts a caller-supplied item_id. An append-only
-- table cannot be back-filled with the id of an ask made after the fact, so the
-- id is minted first and the two rows are written in one direction: evidence,
-- then ask. If the enqueue fails the delta survives with no inbox row, and
-- pending_deltas() reports it as still pending — an unqueued ask must surface as
-- unanswered, never as settled.
--
-- A CORRECTION IS A SUCCESSOR ROW
--
-- `supersedes_delta_id` points at the row being corrected, exactly as
-- sbom_records.supersedes_sbom_id does under the SBOM Accommodation of Updates
-- element. Correcting a delta means INSERTing a successor; the predecessor keeps
-- every value it had, not even a "superseded" flag, because a reviewer may
-- already be looking at the document that row describes. Whether a row is
-- superseded is derived at read time by hitl_delta.revision_chain().
--
-- WHY THIS TABLE DOES HOLD FULL TEXT, UNLIKE approval_items
--
-- approval_items stores argument KEY NAMES and a digest because its rows are
-- mirrored out to Slack, Teams, Telegram and email. That constraint is about the
-- DESTINATION, not the table, and it still holds: hitl_delta.record_delta()
-- renders an approval body carrying counts and the delta_id ONLY, never a line
-- of the artifact. The text lives here, behind the RLS predicate the
-- `classification` column makes this table eligible for, and is read by a
-- reviewer who is already cleared for the artifact.
--
-- before_hash/after_hash are SHA-256 of the respective text. They are what lets
-- a later reader prove a stored artifact is the one this delta describes without
-- re-reading the text, and what makes a no-op override (before_hash =
-- after_hash) detectable in SQL.
--
-- VOCABULARY LIVES IN PYTHON, NOT IN A CHECK CONSTRAINT
--
-- `stage` is validated by STAGES in tools/quality/hitl_delta.py. Same call
-- migrations 20260803002224 and 20260809203855 made: a CHECK here is a second
-- hardcoded copy that drifts the first time a new drafting surface is added, and
-- trust-hitl-03 adds surfaces.
--
-- JSON COLUMNS ARE TEXT AND ARE PARSED IN PYTHON
--
-- findings_before, findings_after and spans hold JSON documents as TEXT. No
-- runtime query filters or groups inside them, so there is no json_extract /
-- jsonb branch to keep portable (CLAUDE.md: compute in Python).

CREATE TABLE IF NOT EXISTS trust_deltas (
    delta_id            TEXT PRIMARY KEY,
    artifact_id         TEXT NOT NULL,
    stage               TEXT NOT NULL,
    before_hash         TEXT NOT NULL,
    after_hash          TEXT NOT NULL,
    before_text         TEXT,
    after_text          TEXT,
    findings_before     TEXT,
    findings_after      TEXT,
    spans               TEXT,
    actor               TEXT NOT NULL,
    rationale           TEXT NOT NULL,
    approval_item_id    TEXT,
    supersedes_delta_id TEXT,
    session_id          TEXT,
    classification      TEXT DEFAULT 'CUI',
    created_at          TEXT DEFAULT CURRENT_TIMESTAMP
);

-- The dominant read is "every delta for this artifact, newest first" — the
-- side-by-side review panel (trust-hitl-02). Then the inbox join for
-- pending_deltas(), and the successor lookup that derives whether a row has been
-- corrected.
CREATE INDEX IF NOT EXISTS idx_trust_deltas_artifact
    ON trust_deltas (artifact_id, created_at);
CREATE INDEX IF NOT EXISTS idx_trust_deltas_approval_item
    ON trust_deltas (approval_item_id);
CREATE INDEX IF NOT EXISTS idx_trust_deltas_supersedes
    ON trust_deltas (supersedes_delta_id);
CREATE INDEX IF NOT EXISTS idx_trust_deltas_stage
    ON trust_deltas (stage);

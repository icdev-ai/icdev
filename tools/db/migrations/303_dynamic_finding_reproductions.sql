-- CUI // SP-CTI
-- Migration 303: reproduce-or-drop storage for DYNAMIC security findings
-- (oss-poc-01, tools/security/reproduction_validator.py).
--
-- Two tables with deliberately different mutability:
--
--   dynamic_findings          MUTABLE. One row per dynamic finding. `status`
--                             transitions unconfirmed -> confirmed ->
--                             remediated as replays are run, so this is NOT
--                             append-only and is intentionally absent from
--                             APPEND_ONLY_TABLES.
--
--   finding_replay_attempts   APPEND-ONLY (NIST AU). One row per replay, ever.
--                             This is the evidence trail that makes a
--                             confirmed finding auditable after the fact and
--                             the record that proves a reproduction
--                             discriminates (fires on the vulnerable target,
--                             does not fire once the fix is applied). Listed
--                             in APPEND_ONLY_TABLES.
--
-- RLS: both carry tenant_id (NOT NULL) + classification so they filter through
-- the standard global get_connection predicate. TEXT-only and dialect-neutral
-- (mirrors migrations 289-294); the runtime tolerates a missing table because
-- persistence is best-effort and classification is never load-bearing on it.

CREATE TABLE IF NOT EXISTS dynamic_findings (
    id                 TEXT PRIMARY KEY,
    finding_key        TEXT NOT NULL,
    detector           TEXT NOT NULL DEFAULT '',
    title              TEXT NOT NULL DEFAULT '',
    severity           TEXT NOT NULL DEFAULT 'medium',
    target             TEXT NOT NULL DEFAULT '',
    -- 'unconfirmed' until a stored reproduction actually replays; 'confirmed'
    -- once it does; 'remediated' when a previously-confirmed reproduction
    -- stops firing (that transition is what proves the fix landed).
    status             TEXT NOT NULL DEFAULT 'unconfirmed'
        CHECK (status IN ('unconfirmed', 'confirmed', 'remediated')),
    -- Serialized Reproduction (kind + steps + predicate). NULL/'' means the
    -- finding shipped without one, which is exactly the case the rule drops.
    reproduction       TEXT DEFAULT '',
    -- 1 only after verify_discrimination() observed reproduce-then-not-reproduce.
    discriminating     INTEGER NOT NULL DEFAULT 0,
    last_outcome       TEXT NOT NULL DEFAULT '',
    last_replayed_at   TEXT,
    tenant_id          TEXT NOT NULL DEFAULT 'default',
    classification     TEXT NOT NULL DEFAULT 'CUI'
        CHECK (classification IN ('PUBLIC', 'CUI', 'ECI', 'SECRET', 'TOP SECRET', 'TOP SECRET//SCI')),
    created_at         TEXT,
    updated_at         TEXT
);

CREATE INDEX IF NOT EXISTS idx_dynamic_findings_key ON dynamic_findings (finding_key);
CREATE INDEX IF NOT EXISTS idx_dynamic_findings_status ON dynamic_findings (status);
CREATE INDEX IF NOT EXISTS idx_dynamic_findings_tenant ON dynamic_findings (tenant_id);

CREATE TABLE IF NOT EXISTS finding_replay_attempts (
    id                 TEXT PRIMARY KEY,
    finding_id         TEXT NOT NULL,
    finding_key        TEXT NOT NULL DEFAULT '',
    -- 'reproduced' | 'not_reproduced' | 'unavailable' | 'error' | 'refused'
    outcome            TEXT NOT NULL DEFAULT 'error',
    target             TEXT NOT NULL DEFAULT '',
    -- Why this replay ran: 'confirm' | 'recheck' | 'discrimination'.
    purpose            TEXT NOT NULL DEFAULT 'confirm',
    predicate_json     TEXT DEFAULT '',
    -- Per-step status / body length / body sha256. Bodies themselves are NOT
    -- stored: a reproduction needs the observation, not the payload dump.
    observations_json  TEXT DEFAULT '',
    detail             TEXT DEFAULT '',
    duration_ms        INTEGER NOT NULL DEFAULT 0,
    tenant_id          TEXT NOT NULL DEFAULT 'default',
    classification     TEXT NOT NULL DEFAULT 'CUI'
        CHECK (classification IN ('PUBLIC', 'CUI', 'ECI', 'SECRET', 'TOP SECRET', 'TOP SECRET//SCI')),
    created_at         TEXT
);

CREATE INDEX IF NOT EXISTS idx_replay_attempts_finding ON finding_replay_attempts (finding_id);
CREATE INDEX IF NOT EXISTS idx_replay_attempts_outcome ON finding_replay_attempts (outcome);
CREATE INDEX IF NOT EXISTS idx_replay_attempts_tenant ON finding_replay_attempts (tenant_id);

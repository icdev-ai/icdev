-- Migration: 20260819020723_dic_docdrift_resolutions
-- CUI // SP-CTI
--
-- cef-ui-01 — persist what cortex.resolve() answered about one DocDrift finding.
--
-- WHY A TABLE AT ALL. A resolution costs 10-12s live on this deployment (five
-- backends, per-backend timeouts from args/cortex_config.yaml), so a page that
-- resolved on render would take minutes for the 72 findings on the live board.
-- Resolutions are computed on demand and read back here.
--
-- WHY THESE COLUMNS ARE SEPARATE. `verdict` (what the DETERMINISTIC packs
-- concluded) and `backend_errors_json` (which retrieval rungs died) are two
-- INDEPENDENT axes and are stored as two, because the live data proves they
-- move independently: "TLS 1.1" resolves `superseded` with four of five
-- backends timed out. Folding a dead backend into the verdict would turn an
-- infrastructure outage into a claim about the entity; folding the verdict into
-- evidence health would hide a confident finding behind a degraded sweep.
--
-- `gaps_json` is likewise NOT a nullable flavour of `verdict`. An `unknown`
-- always carries a gap naming WHY (no_pack_matched / no_evidence /
-- backends_failed / packs_failed), and those are four different fixes.
--
-- `advisory_json` holds the SME rung's OPINION and never contributes to
-- `verdict` — tools/cortex/schemas.py ADVISORY_BACKENDS. It records its own
-- state so "not consulted" and "consulted and unavailable" cannot be read as
-- "the expert had no concerns".
--
-- tenant_id/classification are present because this table is read through the
-- RLS-aware get_connection(), exactly like the dic_* tables acoic.py owns.
-- classification holds a LABEL ('CUI'), never a banner ('CUI // SP-CTI') — a
-- banner matches no clearance at any level and the row would be written,
-- retained and invisible.

CREATE TABLE IF NOT EXISTS dic_docdrift_resolutions (
    resolution_id        TEXT PRIMARY KEY,
    entity               TEXT NOT NULL,
    entity_key           TEXT NOT NULL,
    question             TEXT,
    -- The deterministic axis. `state` is the rendered discriminator and is
    -- derived from `verdict` + `verdict_source`; both are stored so a reader
    -- never has to re-derive one from the other.
    state                TEXT NOT NULL,
    verdict              TEXT,
    verdict_source       TEXT,
    superseded_by        TEXT,
    replacement_source   TEXT,
    grounded             INTEGER NOT NULL DEFAULT 0,
    -- The evidence axis, independent of the verdict above.
    evidence_health      TEXT,
    citation_count       INTEGER NOT NULL DEFAULT 0,
    citations_json       TEXT,
    gaps_json            TEXT,
    conflicts_json       TEXT,
    assessments_json     TEXT,
    backend_errors_json  TEXT,
    backends_consulted_json TEXT,
    -- The advisory axis. Never evidence, never a verdict input.
    advisory_state       TEXT,
    advisory_json        TEXT,
    resolution_text      TEXT,
    provenance_id        TEXT,
    error                TEXT,
    duration_ms          INTEGER,
    resolved_at          TEXT NOT NULL,
    tenant_id            TEXT,
    classification       TEXT
);

-- The page reads "the newest resolution for this entity", per tenant.
CREATE INDEX IF NOT EXISTS idx_docdrift_res_entity
    ON dic_docdrift_resolutions (tenant_id, entity_key, resolved_at);

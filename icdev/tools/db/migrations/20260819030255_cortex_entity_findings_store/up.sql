-- Migration: 20260819030255_cortex_entity_findings_store
-- CUI // SP-CTI
--
-- cef-ui-02 — persist what cortex.resolve() DETECTED so a human can browse it.
--
-- cef-rsv-02 made cross-source conflicts and per-entity gaps computable, and
-- cef-rsv-03 gave each of them citations. Both travel on the CortexResolution
-- the caller already holds and nowhere else, so the only reader of a finding
-- was the code that triggered the resolution. A conflict is a finding a HUMAN
-- adjudicates and a gap is a data-quality ticket; neither is actionable if it
-- dies with the request.
--
-- Deliberately NOT append-only: one row per (tenant, entity, finding), upserted
-- on re-observation. A conflict seen on 40 resolutions is ONE disagreement, and
-- 40 rows would render as 40 findings. `seen_count` keeps the recurrence.

CREATE TABLE IF NOT EXISTS cortex_entity_findings (
    finding_id            TEXT PRIMARY KEY,
    tenant_id             TEXT NOT NULL DEFAULT 'default',
    -- The RLS LABEL ('CUI'), never a banner ('CUI // SP-CTI') — the predicate
    -- get_connection() attaches compares against labels, and a banner here
    -- matches no clearance, so the row would be written, retained and invisible.
    classification        TEXT NOT NULL DEFAULT 'CUI',
    finding_type          TEXT NOT NULL DEFAULT 'gap',   -- conflict | gap
    entity_key            TEXT NOT NULL DEFAULT '',
    entity_label          TEXT NOT NULL DEFAULT '',
    entity_type           TEXT NOT NULL DEFAULT '',
    -- Conflict only: which axis the sources disagree on (status, successor, ...)
    conflict_kind         TEXT NOT NULL DEFAULT '',
    -- Gap only: the reason vocabulary, as a LIST, because reasons co-occur.
    reasons_json          TEXT NOT NULL DEFAULT '[]',
    -- Conflict only: the distinct claimed values, sorted. No winner is stored,
    -- because cef-rsv-02 does not compute one and this table must not invent it.
    values_json           TEXT NOT NULL DEFAULT '[]',
    -- Every EntityClaim that took part, each with its OWN provenance (backend,
    -- source, source_id, source_table, as_of, authoritative, extraction).
    sides_json            TEXT NOT NULL DEFAULT '[]',
    backends_json         TEXT NOT NULL DEFAULT '[]',
    -- Carried as its own column, never folded into reasons: a partial outage is
    -- context for a gap, not the gap's cause.
    backends_failed_json  TEXT NOT NULL DEFAULT '[]',
    cross_backend         INTEGER NOT NULL DEFAULT 0,
    citations_json        TEXT NOT NULL DEFAULT '[]',
    -- Sides that name an authority and no row id. Reported, never lent a
    -- neighbour's citation (cef-rsv-03).
    uncited_sides_json    TEXT NOT NULL DEFAULT '[]',
    -- Which of the three causes an EMPTY citation list is, for a gap.
    citation_basis        TEXT NOT NULL DEFAULT '',
    -- The resolve() subject that produced the finding, and its verdict. A
    -- conflict is reported ALONGSIDE the verdict, never instead of it.
    subject_entity        TEXT NOT NULL DEFAULT '',
    subject_verdict       TEXT NOT NULL DEFAULT '',
    provenance_id         TEXT NOT NULL DEFAULT '',
    seen_count            INTEGER NOT NULL DEFAULT 1,
    first_seen_at         TIMESTAMP,
    last_seen_at          TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_cef_findings_browse
    ON cortex_entity_findings (tenant_id, finding_type, last_seen_at);
CREATE INDEX IF NOT EXISTS idx_cef_findings_entity
    ON cortex_entity_findings (tenant_id, entity_key);

-- The DENOMINATOR. Without it an empty findings table has two causes that read
-- identically: no resolution has ever run (UNMEASURED), and resolutions ran and
-- every source agreed (a measured clean bill of health). One row per tenant.
CREATE TABLE IF NOT EXISTS cortex_finding_runs (
    tenant_id           TEXT PRIMARY KEY,
    classification      TEXT NOT NULL DEFAULT 'CUI',
    resolutions         INTEGER NOT NULL DEFAULT 0,
    conflicts_seen      INTEGER NOT NULL DEFAULT 0,
    gaps_seen           INTEGER NOT NULL DEFAULT 0,
    clean_resolutions   INTEGER NOT NULL DEFAULT 0,
    last_run_at         TIMESTAMP
);

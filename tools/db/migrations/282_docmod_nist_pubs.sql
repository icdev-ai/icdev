-- CUI // SP-CTI
-- Migration 282: NIST publication revision cache (dmx-loop-02)
--
-- Mutable evidence cache for the docmod policy_refs pack. One row per NIST
-- publication (e.g. 'SP 800-53') recording the LATEST revision NIST currently
-- publishes. Populated by tools/doc_modernization/nist_pubs_sync.py from the
-- NIST CSRC publications RSS/Atom feed (air-gap: static seed or operator import).
--
-- Wiring: policy_refs.evaluate() consults this cache so a document that cites an
-- OLDER revision than the one NIST now publishes is flagged as a superseded
-- standard WITHOUT a human first hand-editing rulebook_policy.yaml. The verdict
-- is deterministic (a numeric revision comparison — TRUST rule 1, no LLM).
--
-- MUTABLE (upsert by pub_id) — modeled on docmod_eol_products (migration 257),
-- read as backend evidence via get_canvas_connection(). Additive; PG-authored,
-- CREATE ... IF NOT EXISTS. Carries tenant_id/classification for parity with the
-- sibling docmod tables (global evidence rows use NULL tenant / 'CUI').

CREATE TABLE IF NOT EXISTS docmod_nist_pubs (
    id              TEXT PRIMARY KEY,
    pub_id          TEXT NOT NULL,           -- normalized publication id, e.g. 'SP 800-53'
    latest_revision TEXT,                    -- display revision, e.g. 'Rev 5'
    revision_num    INTEGER,                 -- numeric revision for comparison (5)
    title           TEXT,
    url             TEXT,
    published_date  TEXT,                    -- ISO date of the revision announcement
    source          TEXT NOT NULL DEFAULT 'nist.gov'
                        CHECK (source IN ('nist.gov','seed','manual')),
    synced_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    tenant_id       TEXT,
    classification  TEXT DEFAULT 'CUI',
    UNIQUE (pub_id)
);
CREATE INDEX IF NOT EXISTS idx_docmod_nist_pubs_pub ON docmod_nist_pubs(pub_id);

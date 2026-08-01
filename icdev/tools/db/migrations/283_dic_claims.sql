-- CUI // SP-CTI
-- Migration 283: dic_claims — semantic claim tracking (dmx-claims-02, Phase A).
--
-- A claim is a typed (subject, predicate, object) proposition anchored to a
-- VERBATIM char span in a specific approved dic_versions row. It replaces the
-- coarse entity_label string a docmod finding attaches to with a span-anchored,
-- human-reviewable assertion, so a rulebook/evidence change can flag the exact
-- sentence rather than re-flag the whole document.
--
-- Design authority: docs/design/dmx-claims-tracking-spike.md §3 (human-approved).
--
-- DIC-table conventions (like every dic_* table): first-class
-- tenant_id + classification columns, RLS-aware via tools.db.storage
-- get_connection() (NOT get_canvas_connection()) — no RLS bypass.
--
-- APPEND-ONLY with a supersedes_id chain, exactly like docmod_findings: a status
-- change is a NEW row whose supersedes_id points at the prior row for the same
-- dedupe_key (latest-wins resolution mirrors scanner._open_findings). Registered
-- in APPEND_ONLY_TABLES (.claude/hooks/pre_tool_use.py).
--
-- Deterministic-first (TRUST rule 1): the LLM only PROPOSES claim structure; it
-- never evaluates validity. Claims land status='pending_review' (HITL). The
-- verbatim anchor (claim_text == version_text[anchor_start:anchor_end]) is the
-- claim's identity and the anti-hallucination guard.
--
-- Portable DDL (TEXT/INTEGER/REAL, CHECK, IF NOT EXISTS) — applies to both PG
-- and SQLite, matching the single-block style of migration 282.

CREATE TABLE IF NOT EXISTS dic_claims (
    claim_id        TEXT PRIMARY KEY,              -- 'clm-<uuid12>'
    doc_id          TEXT NOT NULL,                 -- FK dic_documents.doc_id
    version_id      TEXT NOT NULL,                 -- FK dic_versions.version_id (approved version the span lives in)
    section         TEXT,                          -- section heading (mirrors docmod_findings.section_heading)
    chunk_link_id   TEXT,                          -- dic_chunk_links.link_id the span came from
    page            INTEGER,

    claim_text      TEXT NOT NULL,                 -- VERBATIM slice: must equal version_text[anchor_start:anchor_end]
    anchor_start    INTEGER NOT NULL,              -- char offset into the version's reconstructed text
    anchor_end      INTEGER NOT NULL,

    subject_label   TEXT NOT NULL,                 -- e.g. 'TLS 1.2'
    subject_type    TEXT,                          -- e.g. 'protocol' (KG_ENTITY_TYPES)
    predicate       TEXT NOT NULL,                 -- controlled verb, e.g. 'requires', 'prohibits', 'references'
    object_label    TEXT,                          -- e.g. 'all API endpoints'
    object_type     TEXT,

    pack_domain     TEXT,                          -- pack that owns validity checks, e.g. 'crypto_protocols'
    linked_evidence_ids TEXT,                      -- JSON array of finding_ids / evidence source ids

    status          TEXT NOT NULL DEFAULT 'pending_review'
                    CHECK (status IN ('pending_review','active','invalidated','superseded')),
    supersedes_id   TEXT,                          -- append-only chain: a state change is a NEW row
    dedupe_key      TEXT,                          -- sha256(doc_id|subject|predicate|object) — stable across versions

    prov_model      TEXT,                          -- LLM model id, or 'no_llm_rulebook'
    prov_prompt_version TEXT,                       -- claim-extraction prompt registry version
    extracted_at    TEXT NOT NULL,                 -- ISO-8601 UTC
    confidence      REAL DEFAULT 1.0,

    tenant_id       TEXT DEFAULT 'default',        -- RLS
    classification  TEXT DEFAULT 'CUI'             -- RLS
);
CREATE INDEX IF NOT EXISTS idx_dic_claims_tenant   ON dic_claims(tenant_id);
CREATE INDEX IF NOT EXISTS idx_dic_claims_doc      ON dic_claims(doc_id, version_id);
CREATE INDEX IF NOT EXISTS idx_dic_claims_subject  ON dic_claims(subject_label);
CREATE INDEX IF NOT EXISTS idx_dic_claims_dedupe   ON dic_claims(dedupe_key);

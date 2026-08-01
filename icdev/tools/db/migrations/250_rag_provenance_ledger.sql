-- Migration 250: materialize rag_provenance_ledger for databases that were
-- bootstrapped before it was added to pg_consolidated.sql (trust-cite-04,
-- fixes A-gap-4 — DIC footnote provenance silently returned empty when the
-- table was absent). Append-only AIA chain-of-custody ledger (NIST AU-3).
-- Idempotent: safe where init_icdev_db.py already created the table.

CREATE TABLE IF NOT EXISTS rag_provenance_ledger (
    id SERIAL PRIMARY KEY,
    chunk_uuid TEXT NOT NULL,
    parent_doc_uuid TEXT,
    sha256_hash TEXT,
    token_count INTEGER DEFAULT 0,
    classification_label TEXT,
    version_tree_ref TEXT,
    model_id TEXT,
    hyperparams_json TEXT DEFAULT '{}',
    prompt_sha256 TEXT,
    signature TEXT,
    event_type TEXT NOT NULL DEFAULT 'ingest'
        CHECK (event_type IN ('ingest', 'chain_of_custody')),
    ingest_timestamp TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_rag_prov_chunk ON rag_provenance_ledger(chunk_uuid);
CREATE INDEX IF NOT EXISTS idx_rag_prov_parent_doc ON rag_provenance_ledger(parent_doc_uuid);
CREATE INDEX IF NOT EXISTS idx_rag_prov_event_type ON rag_provenance_ledger(event_type);

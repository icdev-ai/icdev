-- Migration 267: dic_chunk_links.chunk_hash — the evidence baseline
--
-- A DIC document version records WHICH rag chunks it was built from
-- (dic_chunk_links), but not what those chunks SAID at the time. Without that
-- baseline there is no way to answer "has the evidence this document cites
-- changed since it was approved?" — the one currency question that needs no
-- domain knowledge and therefore works for any document type
-- (tools/doc_modernization/packs/evidence_currency.py).
--
-- Additive only. PG-authored; ADD COLUMN IF NOT EXISTS follows migration 257.

ALTER TABLE dic_chunk_links ADD COLUMN IF NOT EXISTS chunk_hash TEXT;

-- Backfill existing links to the chunk's CURRENT hash.
--
-- These links predate the column, so their true link-time hash is unknowable.
-- Seeding them as "whatever the chunk says now" makes every legacy document
-- start out current: the alternative (leaving NULL and treating NULL as
-- divergent) would emit a false drift finding for every pre-existing document
-- on the first sweep, which in an SSP chain is worse than a missed one.
-- Genuine divergence is still caught from this point forward — any change AFTER
-- the backfill moves content_hash away from the recorded value.
--
-- Correlated subquery rather than UPDATE ... FROM: the latter is PG-only, and
-- migrations also run against SQLite on the init fallback path.
UPDATE dic_chunk_links
SET chunk_hash = (
    SELECT r.content_hash FROM rag_chunks r WHERE r.id = dic_chunk_links.rag_chunk_id
)
WHERE chunk_hash IS NULL;

CREATE INDEX IF NOT EXISTS idx_dic_chunk_links_hash ON dic_chunk_links(chunk_hash);

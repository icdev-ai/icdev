-- CUI // SP-CTI
-- Migration 296: page / section / doc_id on rag_chunks (oss-chunk-02).
--
-- rag_chunks records WHAT a chunk says and nothing about WHERE it came from
-- inside its document. Page and section exist only in the DIC-side join table
-- dic_chunk_links, so every non-DIC ingestion path loses them entirely — and a
-- chunk reading "shall be documented in the System Security Plan" is
-- positionally orphaned: you cannot tell a reviewer which page of which section
-- it came from, and retrieval cannot expand a hit to its enclosing section.
--
-- This is RAGFlow 0.21's "long-context RAG" idea, done deterministically.
--
-- It is the COMPLEMENT of tools/rag/contextual_retrieval.py, not a replacement.
-- That ships ON and prepends an LLM-generated per-chunk prefix (measured
-- +0.0151 recall@5, +0.0202 MRR in rce-eval-04). The difference that justifies
-- both: an LLM prefix is prose inside the embedded text and can only help
-- ranking. Real columns are also FILTERABLE and CITABLE — "every chunk from
-- section 3.4" is a query, and "p. 47, §3.4" is a citation. Neither can be done
-- with a prose prefix.
--
-- All three columns are nullable with no default. Existing rows keep NULL and
-- every read path must tolerate that: back-filling would mean re-parsing every
-- source document, and a wrong page number is worse than an absent one on a
-- surface whose whole purpose is provenance.
--
-- Additive and idempotent (ADD COLUMN IF NOT EXISTS is PG-native), matching the
-- conventions in migrations 293/294.

-- @pg-only
ALTER TABLE IF EXISTS rag_chunks ADD COLUMN IF NOT EXISTS page INTEGER;
-- @pg-only
ALTER TABLE IF EXISTS rag_chunks ADD COLUMN IF NOT EXISTS section TEXT;
-- @pg-only
-- doc_id groups every chunk of one logical document across re-ingests. chunk ids
-- change on re-ingest (they are content-derived), so without this there is no
-- stable handle for "the rest of this document".
ALTER TABLE IF EXISTS rag_chunks ADD COLUMN IF NOT EXISTS doc_id TEXT;

-- Section-level expansion is the read pattern this exists to serve: given a hit,
-- fetch its siblings. That is doc_id + section, so index the pair.
-- @pg-only
CREATE INDEX IF NOT EXISTS idx_rag_chunks_doc_section ON rag_chunks (doc_id, section);
-- @pg-only
CREATE INDEX IF NOT EXISTS idx_rag_chunks_doc_page ON rag_chunks (doc_id, page);

-- @sqlite-only
-- SQLite is an init-only fallback; init_icdev_db.py creates rag_chunks fresh
-- with these columns, and the runtime tolerates their absence (persistence of
-- position is best-effort, exactly like the chunking_template column added by
-- oss-chunk-01).

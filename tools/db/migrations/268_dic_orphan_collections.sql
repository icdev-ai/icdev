-- Migration 268: register orphaned DIC collections — make invisible documents visible
--
-- dic_documents.collection_id is free-text with no FK, and every ingest path
-- takes it from the caller without creating the matching dic_collections row
-- (/api/ingest defaults to 'default', the CLI passes --collection verbatim, the
-- IDR flow mints 'idr-<session_id>'). The Collections UI enumerates
-- dic_collections, not dic_documents, so a document whose collection_id has no
-- row has no container to appear in: ingested, chunked, embedded, linked,
-- scanned — and unreachable. On the live corpus this hid 49 of 53 documents.
--
-- The code fix (tools/document_intelligence/collection_registry.ensure_collection)
-- stops NEW orphans. This repairs the ones already on disk.
--
-- We create rows keyed by the EXISTING slug rather than rewriting collection_id
-- to a proper hash id, because collection_id is load-bearing identity, not a
-- label: ingest_orchestrator._doc_id derives doc_id from
-- f"{collection_id}:{filepath}", chunks carry it as project_id, and content
-- hashes are scoped by it. Renaming would orphan every document from its chunks.
--
-- Data-driven and therefore self-limiting: on a fresh database there are no
-- orphans and this is a no-op. It intentionally also surfaces test-fixture
-- collections — they hold real rows, and a collection the operator can see and
-- delete beats one silently withheld because a migration judged it junk.
--
-- Additive. Idempotent (ON CONFLICT DO NOTHING). PG-authored; the CTE and
-- ON CONFLICT are supported by both PostgreSQL and SQLite 3.24+.

INSERT INTO dic_collections (collection_id, name, description, tenant_id, classification)
SELECT
    o.collection_id,
    -- The id verbatim: the operator chose this string, so showing it back is how
    -- they recognise their own documents.
    o.collection_id,
    'Auto-registered by migration 268 — documents existed with no collection row.',
    o.tenant_id,
    -- Most-restrictive-wins. The ranking is explicit because classification does
    -- NOT sort alphabetically: MAX() over the raw text would rank 'UNCLASSIFIED'
    -- above 'SECRET' and under-mark a collection holding classified documents.
    CASE o.max_rank
        WHEN 4 THEN 'TOP SECRET'
        WHEN 3 THEN 'SECRET'
        WHEN 1 THEN 'UNCLASSIFIED'
        ELSE 'CUI'
    END
FROM (
    SELECT
        d.collection_id                       AS collection_id,
        -- If one collection_id somehow spans tenants, MIN picks one and the
        -- other tenant's documents stay hidden. That fails CLOSED (no
        -- cross-tenant exposure) and is the safe direction for a repair.
        MIN(COALESCE(d.tenant_id, 'default')) AS tenant_id,
        MAX(CASE UPPER(COALESCE(d.classification, 'CUI'))
                WHEN 'UNCLASSIFIED' THEN 1
                WHEN 'CUI'          THEN 2
                WHEN 'SECRET'       THEN 3
                WHEN 'TOP SECRET'   THEN 4
                ELSE 2  -- unknown marking is not evidence that content is releasable
            END)                              AS max_rank
    FROM dic_documents d
    LEFT JOIN dic_collections c ON c.collection_id = d.collection_id
    WHERE c.collection_id IS NULL
      AND d.collection_id IS NOT NULL
      AND TRIM(d.collection_id) <> ''
    GROUP BY d.collection_id
) o
ON CONFLICT (collection_id) DO NOTHING;

-- Migration 269: stop emptied KG graphs claiming entities they no longer hold
--
-- Until #302, rag_to_kg_ingester._create_graph minted a NEW kg_graphs row per
-- chunk (name = 'rag-chunk-<chunk_id>'). #302 keys graphs on
-- (project_id, tenant_id, source_table) instead, so re-ingest moves each chunk's
-- nodes into a shared per-corpus graph — _delete_stale_nodes removes the old
-- rows first — and the old per-chunk graph is left holding nothing.
--
-- Nothing garbage-collects kg_graphs. So those rows survive, still reporting the
-- entity_count they had when each held one chunk. The dashboard reads
-- SUM(entity_count)/SUM(edge_count) straight from these columns
-- (tools/dashboard/app.py), so it counts entities that are not there. On the live
-- corpus after the re-graph: 39 emptied graphs claiming 59 phantom entities.
--
-- This zeroes the counts. It does NOT delete the rows: kg_retrieval_log.graph_id
-- and kg_ontology.graph_id are foreign keys to kg_graphs(id), and the live DB has
-- 46 kg_retrieval_log rows pointing at these graphs. Deleting them would either
-- fail on the FK or force destroying retrieval history — too high a price for a
-- cosmetic count, and history is not ours to discard. An empty graph that admits
-- it is empty is honest; a deleted one takes evidence with it.
--
-- Scoped deliberately narrowly:
--   * name LIKE 'rag-chunk-%' — only the superseded per-chunk graphs. That name
--     is produced by exactly one line (the old _create_graph) and nothing else
--     in the codebase parses or emits it.
--   * 0 actual nodes — a graph that still holds nodes is left alone; #302's
--     recount keeps those honest on their own.
--
-- Data-driven and therefore self-limiting: on a database that never ran the old
-- bridge there are no such rows and this is a no-op. Idempotent — re-running
-- finds nothing left to correct.
--
-- Additive in spirit (no rows removed). PG-authored; the correlated subquery
-- works on both PostgreSQL and SQLite.

UPDATE kg_graphs
SET entity_count = 0,
    edge_count = 0
WHERE name LIKE 'rag-chunk-%'
  AND (entity_count <> 0 OR edge_count <> 0)
  AND NOT EXISTS (
      SELECT 1 FROM kg_nodes n WHERE n.graph_id = kg_graphs.id
  );

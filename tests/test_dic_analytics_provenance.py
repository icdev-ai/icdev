# CUI // SP-CTI
"""Does consolidating the KG per project make DIC analytics over-count?

_dic_graph_ids selects graph ids from DIC-sourced NODES, then callers pull every
node in those graphs (`WHERE n.graph_id IN (...)`). That was exact while graphs
were per-chunk — one graph, one chunk, one provenance. Once a graph spans a
project, any non-DIC chunk sharing that project_id rides along.

This proves whether that is real, so the answer is measured rather than assumed.
"""

import pytest

from tools.document_intelligence import analytics_engine
from tools.rag import rag_to_kg_ingester as rki


@pytest.fixture()
def conn(icdev_db, monkeypatch):
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("ICDEV_DB_PATH", str(icdev_db))
    from tools.db.storage import get_connection

    conn = get_connection(db_path=str(icdev_db))
    cur = conn.cursor()
    cur.execute("""CREATE TABLE IF NOT EXISTS kg_graphs (
        id TEXT PRIMARY KEY, project_id TEXT, name TEXT, description TEXT,
        entity_count INTEGER, edge_count INTEGER, metadata TEXT,
        created_at TEXT, updated_at TEXT, classification TEXT)""")
    cur.execute("""CREATE TABLE IF NOT EXISTS kg_nodes (
        id TEXT PRIMARY KEY, graph_id TEXT, label TEXT, entity_type TEXT,
        properties TEXT, centrality REAL, created_at TEXT,
        source_chunk_id TEXT, classification TEXT)""")
    cur.execute("""CREATE TABLE IF NOT EXISTS kg_edges (
        id TEXT PRIMARY KEY, graph_id TEXT, source_id TEXT, target_id TEXT,
        relationship TEXT, weight REAL, properties TEXT, created_at TEXT)""")
    cur.execute("""CREATE TABLE IF NOT EXISTS rag_chunks (
        id TEXT PRIMARY KEY, content TEXT, metadata TEXT, tier TEXT,
        tenant_id TEXT, project_id TEXT, classification TEXT,
        source_id TEXT, source_table TEXT, kg_node_ids TEXT)""")
    cur.execute("""CREATE TABLE IF NOT EXISTS dic_documents (
        doc_id TEXT PRIMARY KEY, collection_id TEXT, source_id TEXT,
        title TEXT, tenant_id TEXT, classification TEXT)""")
    conn.commit()
    yield conn
    conn.close()


def _chunk(conn, chunk_id, text, project_id, source_id, source_table):
    conn.cursor().execute(
        "INSERT INTO rag_chunks (id, content, metadata, tier, tenant_id, project_id, "
        "classification, source_id, source_table, kg_node_ids) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        (chunk_id, text, "{}", "warm", "acme", project_id, "CUI",
         source_id, source_table, "[]"),
    )
    conn.commit()


def test_analytics_does_not_count_a_non_dic_chunk_sharing_the_project(conn, monkeypatch):
    """A non-DIC chunk in the same project must not appear in DIC analytics.

    Both chunks now share one graph (that is the point of consolidation), so if
    analytics filtered purely on graph_id it would report the non-DIC entity as a
    document entity.
    """
    monkeypatch.setattr(rki, "_has_llm", lambda: False)

    # A real DIC document + its chunk.
    conn.cursor().execute(
        "INSERT INTO dic_documents (doc_id, collection_id, title) VALUES (%s,%s,%s)",
        ("dic_doc_1", "default", "Network Architecture"))
    conn.commit()
    _chunk(conn, "chunk-dic", "The Catalyst 6500 is deployed.", "default",
           "dic_doc_1", "dic_documents")

    # A chunk from somewhere else that happens to share project_id='default'
    # (a generic name — nothing stops another ingester using it).
    _chunk(conn, "chunk-other", "The Nexus 9000 is unrelated.", "default",
           "some-other-source", "govcon_opportunities")

    rki.ingest_chunk(conn, "chunk-dic")
    rki.ingest_chunk(conn, "chunk-other")

    # Consolidation groups by (project_id, tenant_id, source_table), so these two
    # stay in separate graphs despite sharing project_id="default". That is what
    # keeps each graph pure by provenance and makes the graph-id filter below
    # exact. Without source_table in the key they would merge and the assertion
    # at the end of this test fails — verified: the non-DIC entity leaked in.
    cur = conn.cursor()
    cur.execute("SELECT COUNT(DISTINCT graph_id) FROM kg_nodes")
    row = cur.fetchone()
    assert (list(row.values())[0] if isinstance(row, dict) else row[0]) == 2, \
        "different provenances must not share a graph"

    monkeypatch.setattr(analytics_engine, "_conn", lambda: conn)
    out = analytics_engine.entity_frequency()
    labels = {e["label"] for e in out.get("top_entities", [])}

    # Labels come from the no-LLM title-case extractor, hence "The Catalyst" /
    # "The Nexus" rather than the full model strings.
    assert any("Catalyst" in lbl for lbl in labels), \
        f"the DIC document's entity must be reported, got {labels}"
    assert not any("Nexus" in lbl for lbl in labels), (
        f"a non-DIC chunk sharing project_id leaked into DIC analytics: {labels} — "
        "the filter must key on node provenance, not on graph membership"
    )

# CUI // SP-CTI
"""/analytics reported an empty corpus because of a schema error, not empty data.

_dic_graph_ids joined kg_graphs.source_doc_id — a column kg_graphs does not have.
The query raised on every call, a bare `except: return []` swallowed it, and the
caller rendered the empty list as "No DIC documents ingested yet. Upload
documents to see analytics." On the live corpus that message was shown against
53 documents / 559 chunks / 58 document-derived KG nodes.
"""

import pytest

from tools.document_intelligence import analytics_engine


@pytest.fixture()
def conn(icdev_db, monkeypatch):
    """Real StorageConnection (not raw sqlite3 — %s/? translation must apply)."""
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("ICDEV_DB_PATH", str(icdev_db))
    from tools.db.storage import get_connection

    conn = get_connection(db_path=str(icdev_db))
    cur = conn.cursor()
    # Only the columns this path touches — mirroring the live schema, which
    # notably has NO source_doc_id on kg_graphs.
    cur.execute("""CREATE TABLE IF NOT EXISTS kg_graphs (
        id TEXT PRIMARY KEY, project_id TEXT, name TEXT, description TEXT,
        entity_count INTEGER, edge_count INTEGER, metadata TEXT,
        created_at TEXT, updated_at TEXT, classification TEXT)""")
    cur.execute("""CREATE TABLE IF NOT EXISTS kg_nodes (
        id TEXT PRIMARY KEY, graph_id TEXT, label TEXT, entity_type TEXT,
        properties TEXT, embedding TEXT, centrality REAL, created_at TEXT,
        source_chunk_id TEXT, classification TEXT)""")
    cur.execute("""CREATE TABLE IF NOT EXISTS rag_chunks (
        id TEXT PRIMARY KEY, content TEXT, source_id TEXT, source_table TEXT,
        tenant_id TEXT, project_id TEXT, classification TEXT)""")
    cur.execute("""CREATE TABLE IF NOT EXISTS dic_documents (
        doc_id TEXT PRIMARY KEY, collection_id TEXT, source_id TEXT,
        title TEXT, tenant_id TEXT, classification TEXT)""")
    conn.commit()
    yield conn
    conn.close()


def _seed_ingested_document(conn):
    """One DIC document -> one chunk -> one KG node, exactly as ingest writes it."""
    cur = conn.cursor()
    cur.execute("INSERT INTO dic_documents (doc_id, collection_id, title) VALUES (%s,%s,%s)",
                ("dic_doc_1", "default", "Network Architecture"))
    # ingest_orchestrator sets rag_chunks.source_id = doc_id
    cur.execute("INSERT INTO rag_chunks (id, content, source_id, source_table) "
                "VALUES (%s,%s,%s,%s)",
                ("chunk-1", "CORE-RTR-01 runs BGP.", "dic_doc_1", "dic_documents"))
    cur.execute("INSERT INTO kg_nodes (id, graph_id, label, entity_type, source_chunk_id) "
                "VALUES (%s,%s,%s,%s,%s)",
                ("node-1", "kg-doc-graph", "CORE-RTR-01", "component", "chunk-1"))
    cur.execute("INSERT INTO kg_graphs (id, project_id, name) VALUES (%s,%s,%s)",
                ("kg-doc-graph", "default", "rag-chunk-chunk-1"))
    conn.commit()


def _seed_system_graph(conn):
    """An ICDEV system graph that analytics must NOT claim as a document graph."""
    cur = conn.cursor()
    cur.execute("INSERT INTO kg_graphs (id, project_id, name) VALUES (%s,%s,%s)",
                ("kg-icdev-self-awareness", None, "ICDEV Self-Awareness"))
    cur.execute("INSERT INTO kg_nodes (id, graph_id, label, entity_type, source_chunk_id) "
                "VALUES (%s,%s,%s,%s,%s)",
                ("node-sys", "kg-icdev-self-awareness", "component_indexer", "module", None))
    conn.commit()


class TestDicGraphIds:
    def test_finds_graphs_for_ingested_documents(self, conn):
        """The regression. Before the fix this raises on kg_graphs.source_doc_id,
        is swallowed, and returns []."""
        _seed_ingested_document(conn)
        assert analytics_engine._dic_graph_ids(conn) == ["kg-doc-graph"]

    def test_excludes_icdev_system_graphs(self, conn):
        """The original filter's purpose: system graphs are not document graphs."""
        _seed_ingested_document(conn)
        _seed_system_graph(conn)
        assert analytics_engine._dic_graph_ids(conn) == ["kg-doc-graph"]

    def test_empty_corpus_really_is_empty(self, conn):
        """No documents -> no graphs. The message is only honest when true."""
        _seed_system_graph(conn)
        assert analytics_engine._dic_graph_ids(conn) == []

    def test_node_with_no_source_chunk_is_not_a_document_graph(self, conn):
        _seed_system_graph(conn)
        cur = conn.cursor()
        cur.execute("INSERT INTO kg_nodes (id, graph_id, label, source_chunk_id) "
                    "VALUES (%s,%s,%s,%s)", ("node-2", "kg-orphan", "Stray", None))
        conn.commit()
        assert "kg-orphan" not in analytics_engine._dic_graph_ids(conn)

    def test_failure_is_logged_not_silently_reported_as_empty(self, conn, monkeypatch):
        """A broken query must not be indistinguishable from an empty corpus —
        that is precisely how this survived unnoticed.

        Asserts on the module's own logger rather than caplog: ICDEV's
        get_logger() does not necessarily propagate to the root logger, so
        caplog can miss the record and the test would pass for the wrong reason.
        """
        warnings: list[str] = []
        monkeypatch.setattr(
            analytics_engine.logger, "warning",
            lambda msg, *a, **kw: warnings.append(str(msg) % a if a else str(msg)),
        )
        conn.cursor().execute("DROP TABLE kg_nodes")
        conn.commit()
        assert analytics_engine._dic_graph_ids(conn) == []
        assert any("cannot resolve DIC graph ids" in w for w in warnings), \
            f"the failure must be logged, got: {warnings}"


class TestUserVisibleSymptom:
    def test_analytics_no_longer_claims_an_empty_corpus_when_documents_exist(self, conn, monkeypatch):
        """The actual lie: 'No DIC documents ingested yet' shown against 53 docs."""
        _seed_ingested_document(conn)
        monkeypatch.setattr(analytics_engine, "_conn", lambda: conn)
        out = analytics_engine.entity_frequency()
        assert not out.get("empty"), out.get("message")
        assert out.get("total", 0) >= 1
        labels = [e["label"] for e in out.get("top_entities", [])]
        assert "CORE-RTR-01" in labels

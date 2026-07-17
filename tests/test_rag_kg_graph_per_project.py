# CUI // SP-CTI
"""The RAG->KG bridge minted a new graph per chunk, so entities never met.

_create_graph called _kg_id() and INSERTed unconditionally on every chunk, named
rag-chunk-<chunk_id>, with project_id set to the TENANT rather than the project.
Live consequence: 58 nodes across 38 graphs, 31 holding exactly one node,
co_occurrence() returning 0 pairs because two entities can only co-occur if they
share a graph.
"""

import pytest

from tools.rag import rag_to_kg_ingester as rki


@pytest.fixture()
def conn(icdev_db, monkeypatch):
    """Real StorageConnection — the bridge uses sql_placeholder/translate_sql."""
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
    conn.commit()
    yield conn
    conn.close()


def _graphs(conn):
    cur = conn.cursor()
    cur.execute("SELECT id FROM kg_graphs")
    return {(list(r.values())[0] if isinstance(r, dict) else r[0]) for r in cur.fetchall()}


DIC = "dic_documents"


class TestGraphKey:
    def test_same_corpus_is_the_same_graph(self):
        assert rki._graph_key("coll-a", "acme", DIC) == rki._graph_key("coll-a", "acme", DIC)

    def test_different_projects_are_different_graphs(self):
        assert rki._graph_key("coll-a", "acme", DIC) != rki._graph_key("coll-b", "acme", DIC)

    def test_tenants_do_not_share_a_graph(self):
        """Two tenants with a same-named collection must stay separate."""
        assert rki._graph_key("coll-a", "acme", DIC) != rki._graph_key("coll-a", "globex", DIC)

    def test_different_source_tables_do_not_share_a_graph(self):
        """Keeps each graph pure by provenance. analytics_engine._dic_graph_ids
        picks graph ids from DIC-sourced nodes and then reads EVERY node in those
        graphs — exact only while a graph has one provenance."""
        assert rki._graph_key("default", "acme", DIC) != \
            rki._graph_key("default", "acme", "govcon_opportunities")

    def test_missing_project_falls_back_without_colliding_with_a_real_one(self):
        assert rki._graph_key("", "acme", DIC) == rki._graph_key(None, "acme", DIC)
        assert rki._graph_key("", "acme", DIC) != rki._graph_key("coll-a", "acme", DIC)


class TestResolveGraph:
    def test_creates_the_graph_when_absent(self, conn):
        gid = rki._resolve_graph(conn, "coll-a", "acme", DIC)
        assert gid in _graphs(conn)

    def test_is_idempotent(self, conn):
        """The regression: two chunks in one corpus must reuse one graph."""
        first = rki._resolve_graph(conn, "coll-a", "acme", DIC)
        second = rki._resolve_graph(conn, "coll-a", "acme", DIC)
        assert first == second
        assert len(_graphs(conn)) == 1

    def test_project_id_is_the_project_not_the_tenant(self, conn):
        """_create_graph wrote `project_id = tenant_id or 'rag-bridge'`, which is
        why 42 live graphs all claim project_id='default' (the tenant)."""
        rki._resolve_graph(conn, "coll-a", "acme", DIC)
        cur = conn.cursor()
        cur.execute("SELECT project_id FROM kg_graphs")
        row = cur.fetchone()
        assert (list(row.values())[0] if isinstance(row, dict) else row[0]) == "coll-a"

    def test_graph_carries_no_classification_claim(self, conn):
        """A graph spanning a whole corpus cannot honestly assert one chunk's
        marking; kg_nodes.classification stays authoritative."""
        import json
        rki._resolve_graph(conn, "coll-a", "acme", DIC)
        cur = conn.cursor()
        cur.execute("SELECT metadata FROM kg_graphs")
        row = cur.fetchone()
        meta = json.loads(list(row.values())[0] if isinstance(row, dict) else row[0])
        assert "classification" not in meta
        assert meta["project_id"] == "coll-a"
        assert meta["source_table"] == DIC


class TestChunksShareAGraph:
    """The behaviour the whole fix exists for."""

    def _add_chunk(self, conn, chunk_id, text, project_id, source_table=DIC):
        conn.cursor().execute(
            "INSERT INTO rag_chunks (id, content, metadata, tier, tenant_id, "
            "project_id, source_table, classification, kg_node_ids) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (chunk_id, text, "{}", "warm", "acme", project_id, source_table, "CUI", "[]"),
        )
        conn.commit()

    def test_different_corpora_in_one_project_stay_separate(self, conn, monkeypatch):
        """'default' is a generic project id — a DIC collection and another
        subsystem can both use it. Their entities must not merge."""
        monkeypatch.setattr(rki, "_has_llm", lambda: False)
        self._add_chunk(conn, "chunk-dic", "The Catalyst 6500 is deployed.", "default", DIC)
        self._add_chunk(conn, "chunk-other", "The Nexus 9000 is unrelated.", "default",
                        "govcon_opportunities")

        rki.ingest_chunk(conn, "chunk-dic")
        rki.ingest_chunk(conn, "chunk-other")

        cur = conn.cursor()
        cur.execute("SELECT COUNT(DISTINCT graph_id) FROM kg_nodes")
        row = cur.fetchone()
        distinct = list(row.values())[0] if isinstance(row, dict) else row[0]
        assert distinct == 2, "different source tables must not share a graph"

    def test_two_chunks_in_one_collection_land_in_one_graph(self, conn, monkeypatch):
        monkeypatch.setattr(rki, "_has_llm", lambda: False)  # deterministic, offline
        self._add_chunk(conn, "chunk-1", "The Catalyst 6500 is deployed.", "coll-a")
        self._add_chunk(conn, "chunk-2", "The Catalyst 6500 reaches EOL.", "coll-a")

        rki.ingest_chunk(conn, "chunk-1")
        rki.ingest_chunk(conn, "chunk-2")

        cur = conn.cursor()
        cur.execute("SELECT COUNT(DISTINCT graph_id) FROM kg_nodes")
        row = cur.fetchone()
        distinct = list(row.values())[0] if isinstance(row, dict) else row[0]
        assert distinct == 1, (
            "entities from the same collection must share a graph — before the fix "
            "each chunk minted its own, so they could never co-occur"
        )

    def test_chunks_in_different_collections_stay_separate(self, conn, monkeypatch):
        monkeypatch.setattr(rki, "_has_llm", lambda: False)
        self._add_chunk(conn, "chunk-1", "The Catalyst 6500 is deployed.", "coll-a")
        self._add_chunk(conn, "chunk-2", "The Catalyst 6500 is deployed.", "coll-b")

        rki.ingest_chunk(conn, "chunk-1")
        rki.ingest_chunk(conn, "chunk-2")

        cur = conn.cursor()
        cur.execute("SELECT COUNT(DISTINCT graph_id) FROM kg_nodes")
        row = cur.fetchone()
        distinct = list(row.values())[0] if isinstance(row, dict) else row[0]
        assert distinct == 2, "collections must not bleed into each other"

    def test_graph_counts_are_recounted_not_overwritten(self, conn, monkeypatch):
        """Consolidation makes `SET entity_count = <this chunk's count>` wrong: the
        second chunk would stomp the first's contribution, and the dashboard reads
        SUM(entity_count) straight from this column."""
        monkeypatch.setattr(rki, "_has_llm", lambda: False)
        self._add_chunk(conn, "chunk-1", "The Catalyst 6500 is deployed.", "coll-a")
        self._add_chunk(conn, "chunk-2", "The Nexus 9000 replaces it.", "coll-a")

        rki.ingest_chunk(conn, "chunk-1")
        rki.ingest_chunk(conn, "chunk-2")

        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM kg_nodes")
        row = cur.fetchone()
        real_nodes = list(row.values())[0] if isinstance(row, dict) else row[0]
        cur.execute("SELECT entity_count FROM kg_graphs")
        row = cur.fetchone()
        claimed = list(row.values())[0] if isinstance(row, dict) else row[0]
        assert claimed == real_nodes, (
            f"graph claims {claimed} entities but holds {real_nodes} — the count "
            "must be a recount, not this chunk's total"
        )

    def test_reingesting_a_chunk_does_not_inflate_the_count(self, conn, monkeypatch):
        """_delete_stale_nodes removes the old rows first, so `+=` would drift."""
        monkeypatch.setattr(rki, "_has_llm", lambda: False)
        self._add_chunk(conn, "chunk-1", "The Catalyst 6500 is deployed.", "coll-a")
        rki.ingest_chunk(conn, "chunk-1")
        rki.ingest_chunk(conn, "chunk-1")  # same chunk again

        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM kg_nodes")
        row = cur.fetchone()
        real_nodes = list(row.values())[0] if isinstance(row, dict) else row[0]
        cur.execute("SELECT entity_count FROM kg_graphs")
        row = cur.fetchone()
        claimed = list(row.values())[0] if isinstance(row, dict) else row[0]
        assert claimed == real_nodes

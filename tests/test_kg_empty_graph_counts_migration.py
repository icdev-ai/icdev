# CUI // SP-CTI
"""Migration 269 — emptied per-chunk graphs must stop claiming entities.

#302 moved each chunk's nodes into a shared per-corpus graph, leaving the old
'rag-chunk-<id>' rows holding nothing while still reporting their old
entity_count. The dashboard SUMs those columns, so it counts entities that are
not there (39 graphs, 59 phantom entities on the live corpus).

The risk in this migration is OVER-reach, not under-reach: it must not touch a
graph that still holds nodes, nor any graph the old bridge did not create.
"""

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
MIGRATION = REPO_ROOT / "tools" / "db" / "migrations" / "269_kg_empty_graph_counts.sql"


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
        source_chunk_id TEXT)""")
    conn.commit()
    yield conn
    conn.close()


def _graph(conn, gid, name, entity_count=0, edge_count=0):
    conn.cursor().execute(
        "INSERT INTO kg_graphs (id, project_id, name, entity_count, edge_count) "
        "VALUES (%s,%s,%s,%s,%s)", (gid, "p", name, entity_count, edge_count))
    conn.commit()


def _node(conn, nid, gid):
    conn.cursor().execute(
        "INSERT INTO kg_nodes (id, graph_id, label) VALUES (%s,%s,%s)", (nid, gid, "X"))
    conn.commit()


def _apply(conn):
    sql = MIGRATION.read_text(encoding="utf-8")
    # Strip comments; execute the single statement.
    stmt = "\n".join(ln for ln in sql.splitlines() if not ln.strip().startswith("--"))
    conn.cursor().execute(stmt.strip().rstrip(";"))
    conn.commit()


def _counts(conn, gid):
    cur = conn.cursor()
    cur.execute("SELECT entity_count, edge_count FROM kg_graphs WHERE id = %s", (gid,))
    r = cur.fetchone()
    return tuple(r.values()) if isinstance(r, dict) else tuple(r)


def test_zeroes_an_emptied_per_chunk_graph(conn):
    """The case that exists 39 times on the live corpus."""
    _graph(conn, "kg-636ff35f69", "rag-chunk-chunk-80a5dd5fd6f0", entity_count=9, edge_count=2)
    _apply(conn)
    assert _counts(conn, "kg-636ff35f69") == (0, 0)


def test_does_not_delete_the_row(conn):
    """kg_retrieval_log.graph_id and kg_ontology.graph_id are FKs to kg_graphs,
    and the live DB has 46 retrieval-log rows pointing at these graphs. History
    is not ours to discard for a cosmetic count."""
    _graph(conn, "kg-636ff35f69", "rag-chunk-chunk-80a5dd5fd6f0", entity_count=9)
    _apply(conn)
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM kg_graphs WHERE id = %s", ("kg-636ff35f69",))
    row = cur.fetchone()
    assert (list(row.values())[0] if isinstance(row, dict) else row[0]) == 1


def test_leaves_a_per_chunk_graph_that_still_holds_nodes(conn):
    """Not every legacy graph is empty. One that still holds nodes is honest —
    #302's recount keeps it that way. Zeroing it would create the very lie this
    migration removes."""
    _graph(conn, "kg-still-used", "rag-chunk-chunk-abc", entity_count=3)
    _node(conn, "n1", "kg-still-used")
    _apply(conn)
    assert _counts(conn, "kg-still-used") == (3, 0)


def test_leaves_the_consolidated_graphs_alone(conn):
    """The graphs #302 creates are named rag-<source>-<project>, not rag-chunk-*."""
    _graph(conn, "kg-rag-abc123", "rag-dic_documents-default", entity_count=231)
    _apply(conn)
    assert _counts(conn, "kg-rag-abc123") == (231, 0)


def test_leaves_icdev_system_graphs_alone(conn):
    """kg-icdev-self-awareness has its own count drift (2258 claimed vs 2289
    actual) — pre-existing, a different engine's problem, and out of scope."""
    _graph(conn, "kg-icdev-self-awareness", "ICDEV Self-Awareness", entity_count=2258)
    _apply(conn)
    assert _counts(conn, "kg-icdev-self-awareness") == (2258, 0)


def test_leaves_canvas_design_graphs_alone(conn):
    _graph(conn, "bdc-designs", "Boundary Design Canvas", entity_count=65)
    _apply(conn)
    assert _counts(conn, "bdc-designs") == (65, 0)


def test_is_idempotent(conn):
    _graph(conn, "kg-636ff35f69", "rag-chunk-chunk-80a5dd5fd6f0", entity_count=9, edge_count=2)
    _apply(conn)
    _apply(conn)
    assert _counts(conn, "kg-636ff35f69") == (0, 0)


def test_is_a_noop_on_a_fresh_database(conn):
    """Data-driven: a database that never ran the old bridge has no such rows."""
    _graph(conn, "kg-rag-abc123", "rag-dic_documents-default", entity_count=10)
    _apply(conn)
    assert _counts(conn, "kg-rag-abc123") == (10, 0)

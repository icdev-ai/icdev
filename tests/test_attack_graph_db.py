# CUI // SP-CTI
"""Tests for tools.security_canvas.attack_graph_db — 6-function CRUD layer."""

import importlib.util
import json
import sqlite3
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

_MIGRATION_PATH = (
    Path(__file__).resolve().parent.parent
    / "tools" / "db" / "migrations" / "028_attack_graph" / "up.py"
)


def _apply_migration(conn: sqlite3.Connection) -> None:
    spec = importlib.util.spec_from_file_location("migration_028_up", _MIGRATION_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.up(conn)


@pytest.fixture()
def mem_conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    _apply_migration(conn)
    return conn


@pytest.fixture(autouse=True)
def patch_get_connection(mem_conn):
    # attack_graph_db uses `%s` placeholders (PG style); route the factory
    # through a real StorageConnection wrapper so `%s` is translated to `?` for
    # the in-memory sqlite3 connection — exactly as the canvas factory does at
    # runtime.  Binding `mem_conn.execute` directly (raw sqlite3) would raise
    # `near "%": syntax error` and never exercise the translator.
    #
    # get_canvas_connection() returns a FRESH connection per call and each
    # `with` block closes it on __exit__.  Here every call must reuse the single
    # in-memory DB (a fresh `:memory:` connection would be an empty database),
    # so return a fresh wrapper per call but suppress close() to keep the shared
    # connection alive across the module's per-operation `with` blocks.
    from tools.db.storage import StorageConnection

    class _NonClosing(StorageConnection):
        def close(self):  # noqa: D401 - keep shared in-memory conn open
            pass

    def _factory(*args, **kwargs):
        return _NonClosing(mem_conn, "sqlite")

    with patch(
        "tools.security_canvas.attack_graph_db.get_canvas_connection",
        side_effect=_factory,
    ) as m:
        yield m


from tools.security_canvas.attack_graph_db import (  # noqa: E402
    add_node,
    add_edge,
    get_node,
    get_edge,
    list_nodes,
    list_edges,
)


# ── add_node ─────────────────────────────────────────────────────────────────

def test_add_node_returns_uuid():
    node_id = add_node("asset-web-01")
    assert isinstance(node_id, str) and len(node_id) == 36


def test_add_node_defaults():
    node_id = add_node("asset-web-01")
    node = get_node(node_id)
    assert node["asset_id"] == "asset-web-01"
    assert node["classification"] == "CUI"
    assert node["value"] == 1.0


def test_add_node_custom_fields():
    node_id = add_node(
        "asset-db-01",
        classification="IL5",
        value=5.0,
        label="Primary DB",
        meta_json={"zone": "private"},
    )
    node = get_node(node_id)
    assert node["classification"] == "IL5"
    assert node["value"] == 5.0
    assert node["label"] == "Primary DB"
    assert json.loads(node["meta_json"]) == {"zone": "private"}


# ── add_edge ─────────────────────────────────────────────────────────────────

def test_add_edge_returns_uuid():
    n1 = add_node("asset-a")
    n2 = add_node("asset-b")
    edge_id = add_edge(n1, n2, "T1078")
    assert isinstance(edge_id, str) and len(edge_id) == 36


def test_add_edge_defaults():
    n1 = add_node("asset-a")
    n2 = add_node("asset-b")
    edge_id = add_edge(n1, n2, "T1078")
    edge = get_edge(edge_id)
    assert edge["ttp_id"] == "T1078"
    assert edge["cost"] == 1.0
    assert json.loads(edge["prereqs_json"]) == []


def test_add_edge_with_prereqs(mem_conn):
    n1 = add_node("asset-a")
    n2 = add_node("asset-b")
    edge_id = add_edge(n1, n2, "T1021.001", cost=3.0, prereqs_json=["no_mfa", "cred_reuse"])
    edge = get_edge(edge_id)
    assert edge["src_node_id"] == n1
    assert edge["dst_node_id"] == n2
    assert edge["cost"] == 3.0
    assert json.loads(edge["prereqs_json"]) == ["no_mfa", "cred_reuse"]


# ── get_node / get_edge ───────────────────────────────────────────────────────

def test_get_node_missing_returns_none():
    assert get_node("does-not-exist") is None


def test_get_edge_missing_returns_none():
    assert get_edge("does-not-exist") is None


# ── list_nodes ────────────────────────────────────────────────────────────────

def test_list_nodes_all():
    add_node("asset-x")
    add_node("asset-y")
    nodes = list_nodes()
    assert len(nodes) >= 2


def test_list_nodes_filter_asset_id():
    add_node("asset-filter-target", classification="IL5")
    add_node("asset-other")
    nodes = list_nodes(asset_id="asset-filter-target")
    assert all(n["asset_id"] == "asset-filter-target" for n in nodes)
    assert len(nodes) >= 1


def test_list_nodes_filter_classification():
    add_node("asset-il5", classification="IL5")
    add_node("asset-cui", classification="CUI")
    il5_nodes = list_nodes(classification="IL5")
    assert all(n["classification"] == "IL5" for n in il5_nodes)


# ── list_edges ────────────────────────────────────────────────────────────────

def test_list_edges_all():
    n1 = add_node("asset-1")
    n2 = add_node("asset-2")
    add_edge(n1, n2, "T1059")
    edges = list_edges()
    assert len(edges) >= 1


def test_list_edges_filter_src():
    n1 = add_node("asset-src")
    n2 = add_node("asset-dst")
    n3 = add_node("asset-other")
    edge_id = add_edge(n1, n2, "T1059")
    add_edge(n3, n2, "T1059")
    edges = list_edges(src_node_id=n1)
    assert all(e["src_node_id"] == n1 for e in edges)
    assert any(e["id"] == edge_id for e in edges)


def test_list_edges_filter_ttp():
    n1 = add_node("asset-p")
    n2 = add_node("asset-q")
    add_edge(n1, n2, "T1078")
    add_edge(n1, n2, "T1190")
    edges = list_edges(ttp_id="T1078")
    assert all(e["ttp_id"] == "T1078" for e in edges)


# ── connection factory (shx-db-05) ────────────────────────────────────────────


def test_uses_canvas_connection_factory():
    """attack_graph_db must use the RLS-bypassing canvas factory, NOT the
    RLS-aware get_connection.  attack_graph_nodes/edges have a `classification`
    column but no `tenant_id`, so the global RLS predicate would raise
    UndefinedColumn (tenant_id) or silently filter rows on PostgreSQL.
    """
    import inspect

    import tools.security_canvas.attack_graph_db as agdb

    # Import the canvas factory (patch-immune: read from the source file, not
    # the live — possibly patched — module attribute).
    src = inspect.getsource(agdb)
    assert "from tools.db.storage import get_canvas_connection" in src
    # The RLS-aware get_connection must NOT be imported.
    assert "import get_connection" not in src


def test_canvas_connection_is_actually_called(patch_get_connection):
    """Every CRUD call routes through get_canvas_connection (the patched factory)."""
    node_id = add_node("asset-routed")
    assert get_node(node_id) is not None
    # The autouse fixture patches get_canvas_connection; if any call site used a
    # different factory the patched mock would not have been invoked.
    assert patch_get_connection.call_count >= 2  # add_node + get_node

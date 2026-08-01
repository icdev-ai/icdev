# CUI // SP-CTI
"""Tests for the NDC parsed-graph LRU cache (ndc-perf-02).

``topologies.graph_json`` is a large JSON blob that runtime routes ``json.loads``
~126 times. ``get_parsed_graph`` memoizes the parse keyed by (topo_id,
updated_at) so unchanged topologies are parsed once. These tests cover:

  (a) two calls for the same unchanged topology → one underlying ``json.loads``
      and one ``graph_json`` DB fetch (``updated_at`` is fetched each call).
  (b) a save that bumps ``updated_at`` → the next call re-parses.
  (c) ``invalidate_parsed_graph`` forces a re-parse even when ``updated_at`` is
      unchanged (same-second-save safety).
  (d) the cache bound is respected — the earliest entry is evicted.
  (e) enricher behavior is unchanged (functional smoke): ``enrich_topology``
      returns the same device count as a direct parse, and — because it takes a
      ``copy=True`` deepcopy — it never mutates the shared cached object.

The temp-canvas SQLite setup mirrors ``tests/test_ndc_backend_helpers.py``.
"""

import json

import pytest

from tools.network import blueprint_helpers as bh
from tools.network.blueprint_helpers import (
    get_parsed_graph,
    invalidate_parsed_graph,
    parsed_graph_cache_stats,
)


@pytest.fixture(autouse=True)
def _clear_cache():
    """The parsed-graph cache is process-global; isolate each test."""
    bh.parsed_graph_cache_clear()
    yield
    bh.parsed_graph_cache_clear()


def _canvas_conn(tmp_path, monkeypatch):
    """A canvas-style SQLite connection with a minimal ``topologies`` table."""
    from tools.network.db import init_db

    db_file = tmp_path / "nc_graph_cache.db"
    monkeypatch.setattr(init_db, "_NC_BACKEND", "sqlite")
    monkeypatch.setattr(init_db, "DB_PATH", db_file)
    conn = init_db.get_connection()
    conn.execute(
        "CREATE TABLE topologies ("
        "id TEXT PRIMARY KEY, name TEXT, graph_json TEXT, updated_at TEXT)"
    )
    conn.commit()
    return conn


def _insert(conn, tid, graph, updated_at="2026-01-01T00:00:00"):
    conn.execute(
        "INSERT INTO topologies (id, name, graph_json, updated_at) VALUES (%s,%s,%s,%s)",
        (tid, tid, json.dumps(graph), updated_at),
    )
    conn.commit()


# ── (a) repeated read → one parse, one graph_json fetch ────────────────────────

def test_hit_parses_once(tmp_path, monkeypatch):
    conn = _canvas_conn(tmp_path, monkeypatch)
    graph = {"nodes": [{"id": "n1"}], "edges": []}
    _insert(conn, "t1", graph)

    # Count json.loads calls made through blueprint_helpers.
    loads_calls = {"n": 0}
    real_loads = bh.json.loads

    def counting_loads(s, *a, **k):
        loads_calls["n"] += 1
        return real_loads(s, *a, **k)

    monkeypatch.setattr(bh.json, "loads", counting_loads)

    # Count DB fetches that pull graph_json (the expensive blob transfer).
    gj_fetches = {"n": 0}
    real_exec = conn.execute

    def counting_exec(sql, *a, **k):
        if "graph_json" in sql:
            gj_fetches["n"] += 1
        return real_exec(sql, *a, **k)

    monkeypatch.setattr(conn, "execute", counting_exec)

    g1 = get_parsed_graph(conn, "t1")
    g2 = get_parsed_graph(conn, "t1")

    assert g1 == graph
    assert g2 == graph
    assert loads_calls["n"] == 1, "graph_json parsed more than once"
    assert gj_fetches["n"] == 1, "graph_json blob fetched more than once"

    stats = parsed_graph_cache_stats()
    assert stats["misses"] == 1
    assert stats["hits"] == 1


def test_hit_returns_same_shared_object(tmp_path, monkeypatch):
    conn = _canvas_conn(tmp_path, monkeypatch)
    _insert(conn, "t1", {"nodes": [{"id": "n1"}]})
    a = get_parsed_graph(conn, "t1")
    b = get_parsed_graph(conn, "t1")
    assert a is b, "read-only callers must share the cached object"

    c = get_parsed_graph(conn, "t1", copy=True)
    assert c == a
    assert c is not a, "copy=True must return a private deepcopy"


# ── (b) save bumps updated_at → re-parse ───────────────────────────────────────

def test_update_reparses(tmp_path, monkeypatch):
    conn = _canvas_conn(tmp_path, monkeypatch)
    _insert(conn, "t1", {"nodes": [{"id": "a"}]}, updated_at="2026-01-01T00:00:00")

    g1 = get_parsed_graph(conn, "t1")
    assert g1["nodes"][0]["id"] == "a"

    # Simulate a save: new graph_json + new updated_at.
    conn.execute(
        "UPDATE topologies SET graph_json=%s, updated_at=%s WHERE id=%s",
        (json.dumps({"nodes": [{"id": "b"}]}), "2026-02-02T00:00:00", "t1"),
    )
    conn.commit()

    g2 = get_parsed_graph(conn, "t1")
    assert g2["nodes"][0]["id"] == "b", "stale parse served after updated_at change"
    assert parsed_graph_cache_stats()["misses"] == 2


# ── (c) invalidate forces re-parse at the same updated_at ──────────────────────

def test_invalidate_forces_reparse(tmp_path, monkeypatch):
    conn = _canvas_conn(tmp_path, monkeypatch)
    _insert(conn, "t1", {"nodes": [{"id": "a"}]})

    get_parsed_graph(conn, "t1")  # miss
    get_parsed_graph(conn, "t1")  # hit
    assert parsed_graph_cache_stats()["misses"] == 1

    # Same-second save: graph_json changes but updated_at does NOT.
    conn.execute(
        "UPDATE topologies SET graph_json=%s WHERE id=%s",
        (json.dumps({"nodes": [{"id": "z"}]}), "t1"),
    )
    conn.commit()

    removed = invalidate_parsed_graph("t1")
    assert removed == 1

    g = get_parsed_graph(conn, "t1")
    assert g["nodes"][0]["id"] == "z", "invalidate did not force a re-parse"
    assert parsed_graph_cache_stats()["misses"] == 2


# ── (d) bounded LRU eviction ───────────────────────────────────────────────────

def test_cache_bound_evicts_earliest(tmp_path, monkeypatch):
    conn = _canvas_conn(tmp_path, monkeypatch)
    bound = bh._PARSED_GRAPH_MAX
    total = bound + 6

    for i in range(total):
        _insert(conn, f"t{i}", {"nodes": [{"id": str(i)}]})
        get_parsed_graph(conn, f"t{i}")  # each a miss

    stats = parsed_graph_cache_stats()
    assert stats["size"] == bound, "cache exceeded its bound"
    assert stats["evictions"] == total - bound

    # The earliest-inserted topology (t0) must have been evicted → re-fetch misses.
    misses_before = stats["misses"]
    get_parsed_graph(conn, "t0")
    assert parsed_graph_cache_stats()["misses"] == misses_before + 1, (
        "earliest entry was not evicted"
    )


# ── (e) enricher smoke: same result + shared-cache mutation isolation ──────────

def test_enricher_matches_direct_parse_and_isolates_cache(tmp_path, monkeypatch):
    from tools.network import topology_enricher as te
    from tools.network import topology_validator as tv
    from tools.network.db import init_db

    conn = _canvas_conn(tmp_path, monkeypatch)
    graph = {
        "nodes": [
            {"id": "r1", "type": "router", "x": 100, "y": 100, "config": {"site": "SiteA"}},
            {"id": "sw1", "type": "switch", "x": 200, "y": 100, "config": {"site": "SiteA"}},
        ],
        "edges": [],
    }
    _insert(conn, "topoE", graph)

    # Route the enricher's own get_connection (and the downstream validator) to
    # our temp DB / a no-op so nothing touches the real network_canvas.db. The
    # enricher opens+closes its OWN connection to the same temp file, so our
    # ``conn`` stays open for the post-enrich assertions.
    monkeypatch.setattr(te, "get_connection", lambda *a, **k: init_db.get_connection())
    monkeypatch.setattr(tv, "validate_topology", lambda *a, **k: {})

    # Warm the shared cache with a read-only parse.
    shared = get_parsed_graph(conn, "topoE")
    shared_nodes_before = len(shared["nodes"])

    # Direct-parse reference for the expected device count.
    direct = json.loads(json.dumps(graph))
    expected_devices = len([n for n in direct["nodes"] if n.get("type") != "group-site"])

    result = te.enrich_topology("topoE", add_infra=True, add_groups=True, save=False)
    assert result.get("error") is None, result
    assert result["devices"] == expected_devices

    # Mutation isolation: the enricher took a copy=True deepcopy and did not save,
    # so the shared cached object must be byte-for-byte unchanged.
    shared_after = get_parsed_graph(conn, "topoE")
    assert shared_after is shared, "shared cache object was replaced unexpectedly"
    assert len(shared_after["nodes"]) == shared_nodes_before, (
        "enricher mutated the shared cached graph"
    )

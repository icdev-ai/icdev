# CUI // SP-CTI
"""Tests for the TFW persona-narrative read-through cache (ndc-perf-01).

narrative_generator.generate_all persists every persona narrative into
nc_step_persona_responses but historically never read it back, re-paying
N_steps x N_personas LLM calls on every walkthrough run. This suite proves the
read-through cache:

  (a) first generate_all invokes the router once per step x persona and persists
      rows; a second run on the unchanged flow invokes the router 0 times and
      returns byte-identical narratives.
  (b) changing the flow content (new canonical hash) regenerates.
  (c) force_regenerate=True bypasses the cache and re-invokes.
  (d) a pre-existing row with no cache meta is a MISS (regenerates), then a
      subsequent run is a HIT (0 invocations).
  (e) a different classification is a different cache key (no cross-class hit).

The LLMRouter is monkeypatched at its import site
(tools.network.narrative_generator.LLMRouter), mirroring
tests/test_ndc_narrative_egress.py. A LOCAL provider spec is used so the
fail-closed classification egress gate never blocks — the tests isolate cache
behavior, not egress. The canvas DB is a temp SQLite opened via the canvas
init_db.get_connection(), mirroring tests/test_ndc_backend_helpers.py.
"""

from __future__ import annotations

import json
import types
import uuid

import tools.network.narrative_generator as ng


# ── Fake router (local provider so egress gate always permits) ────────────────
_LOCAL_PROVIDER = {"type": "ollama", "base_url": "http://localhost:11434"}


class _Resp:
    def __init__(self, content: str) -> None:
        self.content = content


def _install_router(monkeypatch):
    """Install a fake LLMRouter whose tfw_narrative provider is local. Returns a
    ``calls`` list; each router.invoke appends and returns a unique narrative so a
    cache hit (no invoke) is distinguishable from a fresh generation."""
    calls: list = []

    def _invoke(fn, req):
        idx = len(calls)
        calls.append(fn)
        return _Resp(f"narrative-{idx}")

    router = types.SimpleNamespace(
        has_any_llm=lambda refresh=False: True,
        get_provider_for_function=lambda fn: (object(), "model-id", {"provider": "ollama"}),
        invoke=_invoke,
        _config={"providers": {"ollama": _LOCAL_PROVIDER}},
    )
    monkeypatch.setattr(ng, "LLMRouter", lambda *a, **k: router)
    return calls


# ── Temp canvas SQLite DB seeded with a flow + walkthrough steps ──────────────

_PERSONAS = ["seceng", "neteng"]


def _seed_db(tmp_path, monkeypatch, *, steps):
    """Point canvas init_db at a temp SQLite DB, create the needed tables and seed
    a topology, a traffic flow and its walkthrough steps. Returns (conn, flow_id)."""
    from tools.network.db import init_db
    from tools.network.traffic_flow import TrafficFlowEngine

    db_file = tmp_path / "ndc_cache_test.db"
    monkeypatch.setattr(init_db, "_NC_BACKEND", "sqlite")
    monkeypatch.setattr(init_db, "DB_PATH", db_file)
    conn = init_db.get_connection()

    # nc_traffic_flows + nc_flow_walkthrough_steps
    TrafficFlowEngine()._ensure_tables(conn)

    conn.execute(
        "CREATE TABLE IF NOT EXISTS topologies "
        "(id TEXT PRIMARY KEY, name TEXT, graph_json TEXT)"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS nc_step_persona_responses ("
        " id TEXT PRIMARY KEY,"
        " step_id TEXT NOT NULL,"
        " persona_id TEXT NOT NULL,"
        " narrative TEXT DEFAULT '',"
        " detail_json TEXT DEFAULT '{}',"
        " created_at TEXT DEFAULT CURRENT_TIMESTAMP,"
        " UNIQUE(step_id, persona_id))"
    )
    conn.commit()

    topo_id = "topo-1"
    node_ids = sorted({s["node_id"] for s in steps})
    graph = {"nodes": [{"id": n, "label": n, "type": "firewall"} for n in node_ids], "edges": []}
    conn.execute(
        "INSERT INTO topologies (id, name, graph_json) VALUES (%s, %s, %s)",
        (topo_id, "Topo", json.dumps(graph)),
    )

    flow_id = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO nc_traffic_flows "
        "(id, topology_id, name, src_zone, dst_zone, app_type, classification) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s)",
        (flow_id, topo_id, "Flow", "on_prem", "csp_il4", "api_rest", "NIPR"),
    )

    _insert_steps(conn, flow_id, steps)
    conn.commit()
    return conn, flow_id


def _insert_steps(conn, flow_id, steps):
    conn.execute("DELETE FROM nc_flow_walkthrough_steps WHERE flow_id = %s", (flow_id,))
    for s in steps:
        conn.execute(
            "INSERT INTO nc_flow_walkthrough_steps "
            "(id, flow_id, step_number, node_id, node_label, action_type,"
            " security_detail, network_detail) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
            (
                str(uuid.uuid4()),
                flow_id,
                s["step_number"],
                s["node_id"],
                s["node_label"],
                s["action_type"],
                json.dumps(s.get("security_detail", {})),
                json.dumps(s.get("network_detail", {})),
            ),
        )
    conn.commit()


def _default_steps():
    return [
        {"step_number": 1, "node_id": "n1", "node_label": "FW-1",
         "action_type": "authenticate", "security_detail": {"domain_type": "nipr"},
         "network_detail": {}},
        {"step_number": 2, "node_id": "n2", "node_label": "VPN-1",
         "action_type": "encrypt_vpn", "security_detail": {"domain_type": "csp_il4"},
         "network_detail": {}},
    ]


def _narratives(result):
    """Flatten {(step_number, persona): narrative} for equality checks."""
    out = {}
    for step in result["steps"]:
        for pid, pr in step["personas"].items():
            out[(step["step_number"], pid)] = pr["narrative"]
    return out


def _run(conn, flow_id, **kw):
    return ng.generate_all(
        flow_id=flow_id, conn=conn, personas=_PERSONAS,
        classification=kw.pop("classification", "NIPR"), **kw
    )


# ── (a) fresh run generates + persists; second run hits cache ──────────────────

def test_second_run_hits_cache_zero_invocations(tmp_path, monkeypatch):
    calls = _install_router(monkeypatch)
    conn, flow_id = _seed_db(tmp_path, monkeypatch, steps=_default_steps())

    r1 = _run(conn, flow_id)
    assert len(calls) == len(_default_steps()) * len(_PERSONAS)  # 2 x 2 == 4
    n1 = _narratives(r1)
    assert all(v.startswith("narrative-") for v in n1.values())

    # Rows were persisted with cache meta.
    rows = conn.execute(
        "SELECT detail_json FROM nc_step_persona_responses"
    ).fetchall()
    assert rows, "expected persisted persona rows"
    for r in rows:
        meta = json.loads(dict(r)["detail_json"])
        assert meta[ng._CACHE_HASH_KEY]
        assert meta[ng._CACHE_CLS_KEY] == "NIPR"

    before = len(calls)
    r2 = _run(conn, flow_id)
    assert len(calls) == before, "unchanged flow must not re-invoke the router"
    assert _narratives(r2) == n1, "cached narratives must match the first run"
    # Returned detail_json must not leak the cache meta keys.
    for step in r2["steps"]:
        for pr in step["personas"].values():
            assert ng._CACHE_HASH_KEY not in pr["detail_json"]
            assert ng._CACHE_CLS_KEY not in pr["detail_json"]


# ── (b) changed flow content regenerates ───────────────────────────────────────

def test_changed_flow_regenerates(tmp_path, monkeypatch):
    calls = _install_router(monkeypatch)
    conn, flow_id = _seed_db(tmp_path, monkeypatch, steps=_default_steps())

    _run(conn, flow_id)
    before = len(calls)

    # Mutate step content -> different canonical hash -> cache miss.
    changed = _default_steps()
    changed[0]["security_detail"] = {"domain_type": "nipr", "extra": "mutated"}
    _insert_steps(conn, flow_id, changed)

    _run(conn, flow_id)
    assert len(calls) > before, "changed flow content must regenerate"


# ── (c) force_regenerate bypasses the cache ────────────────────────────────────

def test_force_regenerate_bypasses_cache(tmp_path, monkeypatch):
    calls = _install_router(monkeypatch)
    conn, flow_id = _seed_db(tmp_path, monkeypatch, steps=_default_steps())

    _run(conn, flow_id)
    before = len(calls)

    _run(conn, flow_id, force_regenerate=True)
    assert len(calls) - before == len(_default_steps()) * len(_PERSONAS), \
        "force_regenerate must re-invoke every step x persona"


# ── (d) pre-existing row without cache meta is a miss, then hits ───────────────

def test_legacy_row_without_meta_is_miss_then_hit(tmp_path, monkeypatch):
    calls = _install_router(monkeypatch)
    conn, flow_id = _seed_db(tmp_path, monkeypatch, steps=_default_steps())

    # Pre-seed a legacy row (no cache meta) for step 1 / seceng.
    s_id = dict(conn.execute(
        "SELECT id FROM nc_flow_walkthrough_steps WHERE flow_id = %s AND step_number = 1",
        (flow_id,),
    ).fetchone())["id"]
    conn.execute(
        "INSERT INTO nc_step_persona_responses (id, step_id, persona_id, narrative, detail_json)"
        " VALUES (%s, %s, %s, %s, %s)",
        (str(uuid.uuid4()), s_id, "seceng", "LEGACY", json.dumps({"persona": "seceng"})),
    )
    conn.commit()

    _run(conn, flow_id)
    assert len(calls) == len(_default_steps()) * len(_PERSONAS), \
        "legacy meta-less row must be treated as a cache miss"

    before = len(calls)
    _run(conn, flow_id)
    assert len(calls) == before, "row rewritten with meta must now hit"


# ── (e) different classification is a different cache key ──────────────────────

def test_different_classification_is_different_key(tmp_path, monkeypatch):
    calls = _install_router(monkeypatch)
    conn, flow_id = _seed_db(tmp_path, monkeypatch, steps=_default_steps())

    _run(conn, flow_id, classification="IL4")
    before = len(calls)

    # Same flow, different classification -> no cross-classification hit.
    _run(conn, flow_id, classification="IL5")
    assert len(calls) - before == len(_default_steps()) * len(_PERSONAS), \
        "a different classification must not hit the IL4 cache entries"

    # And IL5 now caches for itself.
    before2 = len(calls)
    _run(conn, flow_id, classification="IL5")
    assert len(calls) == before2, "second IL5 run must hit the IL5 cache"

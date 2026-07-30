# CUI // SP-CTI
"""Route-surface + lifecycle tests for the Agentic AI Canvas (penta-aadc-06).

The blueprint carries ~130 routes and was previously untested end-to-end. Two
kinds of coverage here:

1. **GET smoke sweep** — every GET page/API route belonging to the aadc
   blueprint is driven through the full dashboard app with a dummy id
   substituted. A handler may legitimately 404/400 for a nonexistent design,
   but a 500 (unhandled server error) is a failure. This sweep found — and
   this change fixed — a latent 500 in ``aadc_api_list_assessments`` which
   selected a nonexistent ``hitl_paths`` column.

2. **Lifecycle** — create design → save graph (+ assessment) → run assessment →
   scorecard API → versions (save + list) → checkpoint (save + list) → delete,
   proving the primary authoring flow round-trips through the DB.

Runtime is PostgreSQL; SQLite is used only because conftest.py hard-forces
``ICDEV_STORAGE_BACKEND=sqlite`` for the whole suite. The two Phase-4 tables
(``aadc_checkpoints``/``aadc_parallel_groups``) live in migration 105, which in
production PG is the same database the canvas connection targets; under the
isolated per-canvas SQLite file we apply that migration's DDL in the fixture so
the checkpoint/parallel routes exercise real tables rather than 500 on a
missing one.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from flask import Flask

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import tools.agentic_ai_canvas.blueprint as bp  # noqa: E402

# Phase-4 tables created by migration 105 (present in prod PG; recreated here
# for the isolated SQLite canvas file).
_MIGRATION_105_DDL = """
CREATE TABLE IF NOT EXISTS aadc_checkpoints (
    id TEXT PRIMARY KEY, design_id TEXT NOT NULL, node_id TEXT DEFAULT '',
    label TEXT DEFAULT '', graph_json TEXT NOT NULL, created_by TEXT DEFAULT 'user',
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS aadc_parallel_groups (
    id TEXT PRIMARY KEY, design_id TEXT NOT NULL, label TEXT DEFAULT 'Parallel Group',
    color TEXT DEFAULT '#7e22ce', node_ids_json TEXT NOT NULL DEFAULT '[]',
    created_at TEXT DEFAULT CURRENT_TIMESTAMP, updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);
"""


# ---------------------------------------------------------------------------
# GET-route enumeration (built once at import from a throwaway registration)
# ---------------------------------------------------------------------------

_DUMMY_IDS = {
    "design_id": "aadc-nope", "did": "aadc-nope", "template_id": "tpl-x",
    "snippet_id": "snp-x", "checkpoint_id": "ckpt-x", "group_id": "grp-x",
    "risk_id": "risk-x", "ref_id": "ref-x", "pack_id": "pack-x", "lid": "1",
}


def _substitute(rule_str: str) -> str:
    url = rule_str
    for key, val in _DUMMY_IDS.items():
        url = (url.replace(f"<{key}>", val)
                  .replace(f"<int:{key}>", val)
                  .replace(f"<string:{key}>", val))
    return url


def _enumerate_get_routes() -> list[str]:
    probe = Flask(__name__)
    if "agentic_ai_canvas" not in probe.blueprints:
        probe.register_blueprint(bp.aadc_bp)
    urls: set[str] = set()
    for rule in probe.url_map.iter_rules():
        if rule.endpoint.split(".")[0] != "agentic_ai_canvas":
            continue
        methods = (rule.methods or set()) - {"HEAD", "OPTIONS"}
        if "GET" not in methods:
            continue
        url = _substitute(rule.rule)
        if "<" in url:  # unmapped converter — skip
            continue
        urls.add(url)
    return sorted(urls)


GET_ROUTES = _enumerate_get_routes()


# ---------------------------------------------------------------------------
# Fixture — full app, temp platform DB + temp canvas DB, authenticated
# ---------------------------------------------------------------------------

@pytest.fixture
def client(icdev_db, tmp_path, monkeypatch):
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("ICDEV_DB_PATH", str(icdev_db))
    monkeypatch.setenv("AADC_STORAGE_BACKEND", "sqlite")

    import tools.dashboard.auth as _auth
    monkeypatch.setattr(_auth, "DB_PATH", str(icdev_db))

    import tools.agentic_ai_canvas.db.init_db as initdb
    monkeypatch.setattr(initdb, "_BACKEND", "sqlite", raising=True)
    monkeypatch.setattr(initdb, "DB_PATH", tmp_path / "aadc_canvas.db", raising=True)

    # Force a fresh init against the temp canvas DB on first request.
    monkeypatch.setattr(bp, "_INIT_DONE", False, raising=False)

    # Flask refuses register_blueprint once an app has served its first request,
    # and tools.dashboard.app.app is a module-level SINGLETON shared by every test
    # in the run. So whether this fixture worked depended on whether some earlier
    # module had already made a request against that same app — running this file
    # alone passed, running it after its siblings produced 79 errors reading
    # "The setup method 'register_blueprint' can no longer be called".
    #
    # Build an isolated app instead, the way _enumerate_get_routes above already
    # builds its probe. Nothing here needs the dashboard's other blueprints, and not
    # mutating the shared app also stops this fixture from leaking the canvas into
    # every later test.
    from tools.dashboard.app import app as _dashboard_app

    app = Flask(
        __name__,
        template_folder=_dashboard_app.template_folder,
        static_folder=_dashboard_app.static_folder,
    )
    app.config.update(_dashboard_app.config)
    app.jinja_env.filters.update(_dashboard_app.jinja_env.filters)
    app.jinja_env.globals.update(_dashboard_app.jinja_env.globals)
    # The canvas templates extend base.html, which reads nav_tree and friends from
    # the dashboard's app-wide context processors. Without these the pages render
    # but raise UndefinedError, which a "never 500" smoke test would report as a
    # route bug rather than a missing fixture.
    app.template_context_processors[None].extend(
        _dashboard_app.template_context_processors.get(None, [])
    )
    app.register_blueprint(bp.aadc_bp)
    app.config["TESTING"] = True

    with app.test_client() as test_client:
        with test_client.session_transaction() as sess:
            sess["user_id"] = "test-admin"
        # Trigger blueprint init (creates schema+seeds), then add migration-105
        # tables to the isolated canvas file so checkpoint/parallel routes work.
        test_client.get("/agentic-ai/api/portfolio")
        conn = initdb.get_connection()
        try:
            conn.executescript(_MIGRATION_105_DDL)
            conn.commit()
        finally:
            conn.close()
        yield test_client


# ---------------------------------------------------------------------------
# GET smoke sweep — no route may 500
# ---------------------------------------------------------------------------

class TestGetRouteSmoke:
    def test_route_enumeration_nonempty(self):
        # Guards against a silent regression where the sweep degenerates to 0.
        assert len(GET_ROUTES) >= 40, f"only enumerated {len(GET_ROUTES)} GET routes"

    @pytest.mark.parametrize("url", GET_ROUTES)
    def test_get_route_never_500(self, client, url):
        resp = client.get(url)
        assert resp.status_code < 500, (
            f"GET {url} returned {resp.status_code} (server error); "
            f"a nonexistent id may 404/400 but must never 500"
        )


# ---------------------------------------------------------------------------
# Lifecycle — create → save → assess → scorecard → versions → checkpoint → delete
# ---------------------------------------------------------------------------

_GRAPH = {
    "nodes": [
        {"id": "n1", "type": "inference-input", "label": "Input", "x": 50, "y": 50},
        {"id": "n2", "type": "input-sanitizer", "label": "Sanitizer", "x": 200, "y": 50},
        {"id": "n3", "type": "llm", "label": "LLM", "x": 350, "y": 50},
        {"id": "n4", "type": "output-validator", "label": "Validator", "x": 500, "y": 50},
    ],
    "edges": [
        {"id": "e1", "source": "n1", "target": "n2"},
        {"id": "e2", "source": "n2", "target": "n3"},
        {"id": "e3", "source": "n3", "target": "n4"},
    ],
}


@pytest.fixture
def created_design(client):
    resp = client.post("/agentic-ai/api/designs",
                       json={"name": "Lifecycle Test", "domain": "gov"})
    assert resp.status_code == 201, resp.data
    return client, resp.get_json()["id"]


class TestLifecycle:
    def test_create_returns_id(self, created_design):
        _client, did = created_design
        assert did.startswith("aadc-")

    def test_get_after_create(self, created_design):
        client, did = created_design
        resp = client.get(f"/agentic-ai/api/designs/{did}")
        assert resp.status_code == 200
        assert resp.get_json()["name"] == "Lifecycle Test"

    def test_save_graph_runs_assessment(self, created_design):
        client, did = created_design
        resp = client.put(f"/agentic-ai/api/designs/{did}",
                          json={"name": "Lifecycle Test", "domain": "gov", "graph": _GRAPH})
        assert resp.status_code == 200
        body = resp.get_json()
        assert "score" in body and "nist_rmf_score" in body
        assert isinstance(body["score"], (int, float))

    def test_run_assessment_endpoint(self, created_design):
        client, did = created_design
        client.put(f"/agentic-ai/api/designs/{did}",
                   json={"name": "L", "domain": "gov", "graph": _GRAPH})
        resp = client.post(f"/agentic-ai/api/designs/{did}/assess", json={})
        assert resp.status_code == 200
        assert "score" in resp.get_json()

    def test_scorecard_api(self, created_design):
        client, did = created_design
        client.put(f"/agentic-ai/api/designs/{did}",
                   json={"name": "L", "domain": "gov", "graph": _GRAPH})
        resp = client.get(f"/agentic-ai/api/designs/{did}/scorecard")
        assert resp.status_code == 200
        sc = resp.get_json()
        assert "overall_score" in sc and "health" in sc

    def test_list_assessments_endpoint_no_500(self, created_design):
        # Regression guard: this route selected a nonexistent hitl_paths column.
        client, did = created_design
        client.put(f"/agentic-ai/api/designs/{did}",
                   json={"name": "L", "domain": "gov", "graph": _GRAPH})
        resp = client.get(f"/agentic-ai/api/designs/{did}/assessments")
        assert resp.status_code == 200
        assert "assessments" in resp.get_json()

    def test_versions_save_and_list(self, created_design):
        client, did = created_design
        client.put(f"/agentic-ai/api/designs/{did}",
                   json={"name": "L", "domain": "gov", "graph": _GRAPH})
        save = client.post(f"/agentic-ai/canvas/{did}/versions", json={"label": "v-explicit"})
        assert save.status_code == 201
        listing = client.get(f"/agentic-ai/canvas/{did}/versions")
        assert listing.status_code == 200
        assert len(listing.get_json()["versions"]) >= 1

    def test_checkpoint_save_and_list(self, created_design):
        client, did = created_design
        client.put(f"/agentic-ai/api/designs/{did}",
                   json={"name": "L", "domain": "gov", "graph": _GRAPH})
        save = client.post(f"/agentic-ai/api/designs/{did}/checkpoints",
                          json={"label": "cp1"})
        assert save.status_code == 201
        listing = client.get(f"/agentic-ai/api/designs/{did}/checkpoints")
        assert listing.status_code == 200
        assert len(listing.get_json()["checkpoints"]) >= 1

    def test_delete_removes_design(self, created_design):
        client, did = created_design
        resp = client.delete(f"/agentic-ai/api/designs/{did}")
        assert resp.status_code == 200
        gone = client.get(f"/agentic-ai/api/designs/{did}")
        assert gone.status_code == 404

    def test_full_flow_end_to_end(self, client):
        # One design carried through every stage in order.
        did = client.post("/agentic-ai/api/designs",
                         json={"name": "E2E", "domain": "gov"}).get_json()["id"]
        assert client.put(f"/agentic-ai/api/designs/{did}",
                          json={"graph": _GRAPH, "domain": "gov"}).status_code == 200
        assert client.post(f"/agentic-ai/api/designs/{did}/assess",
                           json={}).status_code == 200
        assert client.get(f"/agentic-ai/api/designs/{did}/scorecard").status_code == 200
        assert client.post(f"/agentic-ai/api/designs/{did}/checkpoints",
                           json={"label": "cp"}).status_code == 201
        assert client.delete(f"/agentic-ai/api/designs/{did}").status_code == 200

# CUI // SP-CTI
"""Route tests for tools/foundry/blueprint.py — the ACF canvas factory (acf-dash-01).

Hermetic: a throwaway file-backed SQLite DB holds the foundry_* tables plus a
minimal kanban_tasks slice. ``tools.db.storage.get_connection`` is repointed at the
temp DB (a fresh connection per call so the blueprint's own ``conn.close()`` never
breaks a later request), and the before_request ``init_db`` guard is stubbed so the
blueprint never touches the platform database.

Covers the acf-dash-01 deliverable end-to-end through a real Flask test client:
  * factory feature flag — None when ICDEV_FOUNDRY_ENABLED is off, Blueprint when on
  * GET  /foundry                 index roll-up (JSON fallback when template absent)
  * GET  /foundry/<id>            concept detail (200 hit / 404 miss)
  * GET  /api/foundry/runs        recent runs
  * GET  /api/foundry/concepts    concept list + ?status= filter
  * GET  /api/foundry/concept/<id> one concept + spec + emitted tasks + outcomes,
                                   incl. live kanban_status on emitted tasks
  * POST /api/foundry/run         engine delegation (dry_run flag) + 503 when absent
"""
from __future__ import annotations

import importlib
import sqlite3

import pytest
from flask import Flask

from tools.foundry.db.init_db import _SCHEMA_SQLITE

# Minimal kanban_tasks slice — only the columns _attach_kanban_status reads.
_KANBAN_DDL = "CREATE TABLE kanban_tasks (id TEXT PRIMARY KEY, status TEXT);"


def _new_conn(path):
    c = sqlite3.connect(path)
    c.row_factory = sqlite3.Row
    return c


def _seed(path):
    """One run + two concepts (approved / rejected); the approved concept gets a
    spec, an emitted kanban task (status in_progress), and an outcome."""
    c = _new_conn(path)
    c.executescript(_SCHEMA_SQLITE)
    c.executescript(_KANBAN_DDL)
    c.execute(
        "INSERT INTO foundry_runs (id, harvested, concepts_proposed, concepts_approved, "
        "tasks_emitted, status) VALUES (1, 12, 2, 1, 3, 'completed')"
    )
    c.execute(
        "INSERT INTO foundry_concepts (id, run_id, name, slug, proposed_capability, "
        "novelty_score, composite_score, status) "
        "VALUES (1, '1', 'Alpha Capability', 'alpha', 'do alpha things', 0.8, 0.91, 'approved')"
    )
    c.execute(
        "INSERT INTO foundry_concepts (id, run_id, name, slug, composite_score, status, reject_reason) "
        "VALUES (2, '1', 'Beta Capability', 'beta', 0.2, 'rejected', 'low_novelty')"
    )
    c.execute(
        "INSERT INTO foundry_specs (concept_id, spec_md, task_count) "
        "VALUES (1, '# Alpha spec', 3)"
    )
    c.execute("INSERT INTO kanban_tasks (id, status) VALUES ('alpha-db-01', 'in_progress')")
    c.execute(
        "INSERT INTO foundry_tasks_emitted (concept_id, kanban_task_id, epic, seq) "
        "VALUES (1, 'alpha-db-01', 'db', 1)"
    )
    c.execute(
        "INSERT INTO foundry_outcomes (concept_id, outcome, metric) VALUES (1, 'vv_pass', 1.0)"
    )
    c.commit()
    c.close()


@pytest.fixture
def client(tmp_path, monkeypatch):
    """A Flask test client with the foundry blueprint mounted against a temp DB."""
    path = str(tmp_path / "foundry_bp.db")
    _seed(path)

    monkeypatch.setenv("ICDEV_FOUNDRY_ENABLED", "1")
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")

    # Repoint get_connection at the temp DB (fresh conn per call) on the canonical
    # module object the blueprint's `from tools.db.storage import get_connection`
    # resolves to (the tools.* shim makes the `import ... as` form fail).
    storage = importlib.import_module("tools.db.storage")
    monkeypatch.setattr(storage, "get_connection", lambda *a, **k: _new_conn(path))

    # Never touch the platform DB from the before_request guard.
    init_db_mod = importlib.import_module("tools.foundry.db.init_db")
    monkeypatch.setattr(init_db_mod, "init_db", lambda *a, **k: True)

    from tools.foundry.blueprint import create_foundry_blueprint

    bp = create_foundry_blueprint()
    assert bp is not None, "blueprint should be built when the feature flag is on"

    app = Flask(__name__)
    app.register_blueprint(bp)
    app.config.update(TESTING=True)
    return app.test_client()


@pytest.fixture
def authorized_client(tmp_path, monkeypatch):
    """Like ``client``, but past the admin/pm role gate on the write endpoint.

    ``nav-sec-06`` restricted POST /api/foundry/run to admin/pm because it kicks
    off a harvest→synth→score→seed cycle; the reads stayed open. That is why the
    GET tests never noticed — only the two POST tests started returning 401
    while still asserting 200/503.

    ``require_role`` is applied as a DECORATOR when the blueprint is built, so
    the stub must be installed on the blueprint module BEFORE
    ``create_foundry_blueprint()`` runs. Patching it afterwards leaves the real
    decorator already wrapped around the view and changes nothing.

    Deliberately a separate fixture rather than a change to ``client``: the
    unauthenticated client is what proves the gate still bites, in
    ``test_api_run_requires_a_privileged_role``.
    """
    path = str(tmp_path / "foundry_bp_auth.db")
    _seed(path)

    monkeypatch.setenv("ICDEV_FOUNDRY_ENABLED", "1")
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")

    storage = importlib.import_module("tools.db.storage")
    monkeypatch.setattr(storage, "get_connection", lambda *a, **k: _new_conn(path))

    init_db_mod = importlib.import_module("tools.foundry.db.init_db")
    monkeypatch.setattr(init_db_mod, "init_db", lambda *a, **k: True)

    bp_mod = importlib.import_module("tools.foundry.blueprint")
    monkeypatch.setattr(bp_mod, "require_role", lambda *roles: (lambda fn: fn))

    bp = bp_mod.create_foundry_blueprint()
    assert bp is not None

    app = Flask(__name__)
    app.register_blueprint(bp)
    app.config.update(TESTING=True)
    return app.test_client()


# --------------------------------------------------------------------------- #
# Factory / feature flag
# --------------------------------------------------------------------------- #
def test_factory_returns_none_when_disabled(monkeypatch):
    monkeypatch.setenv("ICDEV_FOUNDRY_ENABLED", "false")
    from tools.foundry.blueprint import create_foundry_blueprint

    assert create_foundry_blueprint() is None


def test_factory_returns_blueprint_when_enabled(monkeypatch):
    monkeypatch.setenv("ICDEV_FOUNDRY_ENABLED", "1")
    from tools.foundry.blueprint import create_foundry_blueprint

    bp = create_foundry_blueprint()
    assert bp is not None
    assert bp.name == "foundry"


# --------------------------------------------------------------------------- #
# Pages
# --------------------------------------------------------------------------- #
def test_index_page(client):
    # No template folder on the bare test app → JSON fallback (still 200).
    resp = client.get("/foundry")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data is not None
    slugs = {c["slug"] for c in data["concepts"]}
    assert {"alpha", "beta"} <= slugs
    assert data["summary"]["by_status"]["approved"] == 1
    assert data["summary"]["by_status"]["rejected"] == 1
    assert any(r["id"] == 1 for r in data["runs"])


def test_detail_page_hit(client):
    resp = client.get("/foundry/1")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["concept"]["slug"] == "alpha"
    assert data["spec"]["task_count"] == 3
    assert data["tasks_emitted"][0]["kanban_status"] == "in_progress"
    assert data["outcomes"][0]["outcome"] == "vv_pass"


def test_detail_page_missing_is_404(client):
    resp = client.get("/foundry/999")
    assert resp.status_code == 404


# --------------------------------------------------------------------------- #
# JSON API — reads
# --------------------------------------------------------------------------- #
def test_api_runs(client):
    resp = client.get("/api/foundry/runs")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["count"] >= 1
    assert data["runs"][0]["status"] == "completed"


def test_api_concepts_lists_all(client):
    resp = client.get("/api/foundry/concepts")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["count"] == 2


def test_api_concepts_status_filter(client):
    resp = client.get("/api/foundry/concepts?status=approved")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["count"] == 1
    assert data["concepts"][0]["slug"] == "alpha"


def test_api_concept_detail_hit(client):
    resp = client.get("/api/foundry/concept/1")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["concept"]["name"] == "Alpha Capability"
    assert data["tasks_emitted"][0]["kanban_status"] == "in_progress"


def test_api_concept_detail_miss(client):
    resp = client.get("/api/foundry/concept/12345")
    assert resp.status_code == 404


# --------------------------------------------------------------------------- #
# JSON API — run one cycle (engine delegation)
# --------------------------------------------------------------------------- #
def test_api_run_requires_a_privileged_role(client):
    """nav-sec-06: the compute trigger is admin/pm only.

    Uses the UNAUTHENTICATED client on purpose. Without this, the two tests
    below could be made to pass by stubbing the gate everywhere, and the
    restriction would be free to regress unnoticed.
    """
    resp = client.post("/api/foundry/run", json={"dry_run": True})
    assert resp.status_code == 401, (
        "POST /api/foundry/run must stay gated on admin/pm — it starts a "
        f"harvest→synth→score→seed cycle. Got {resp.status_code}."
    )


def test_api_run_delegates_to_engine(authorized_client, monkeypatch):
    captured = {}

    def fake_run_cycle(*, dry_run=False, **kwargs):
        captured["dry_run"] = dry_run
        return {"run_id": 99, "status": "completed", "dry_run": dry_run}

    engine = importlib.import_module("tools.foundry.engine")
    monkeypatch.setattr(engine, "run_cycle", fake_run_cycle)

    resp = authorized_client.post("/api/foundry/run", json={"dry_run": True})
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["run_id"] == 99
    assert captured["dry_run"] is True


def test_api_run_503_when_engine_absent(authorized_client, monkeypatch):
    import builtins

    real_import = builtins.__import__

    def blocking_import(name, *a, **k):
        if name == "tools.foundry.engine":
            raise ImportError("engine not present in this branch")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", blocking_import)
    resp = authorized_client.post("/api/foundry/run", json={})
    assert resp.status_code == 503


# --------------------------------------------------------------------------- #
# JSON API — IQE natural-language query (acf-dash-04)
# --------------------------------------------------------------------------- #
def test_api_iqe_query_returns_real_rows(client, monkeypatch):
    """POST /foundry/api/iqe-query translates NL → IQE → REAL foundry_concepts rows.

    The NL→IQE translation is stubbed (offline-safe, no LLM) to a deterministic
    IQE string; the adapter then executes it against the fixture's seeded temp DB
    via the repointed get_connection, so the approved concept must come back."""
    nl_mod = importlib.import_module("tools.iqe.nl_to_iqe")
    monkeypatch.setattr(
        nl_mod,
        "nl_to_iqe",
        lambda question, collections: {
            "iqe": 'foreach c in foundry.concepts where c.status == "approved" '
            "select c.id, c.name, c.slug, c.composite_score",
            "explanation": "approved concepts",
        },
    )

    resp = client.post(
        "/foundry/api/iqe-query", json={"question": "approved concepts"}
    )
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["ok"] is True
    assert data["canvas"] == "foundry"
    slugs = {r["slug"] for r in data["rows"]}
    assert slugs == {"alpha"}  # only the approved concept, not the rejected beta


def test_api_iqe_query_error_is_structured(client, monkeypatch):
    """A translation/parse failure degrades to a 500 with a structured JSON body
    (canvas + error), never an unhandled stack trace."""
    nl_mod = importlib.import_module("tools.iqe.nl_to_iqe")
    monkeypatch.setattr(
        nl_mod,
        "nl_to_iqe",
        lambda question, collections: {"iqe": "this is not valid iqe ((("},
    )

    resp = client.post("/foundry/api/iqe-query", json={"question": "boom"})
    assert resp.status_code == 500
    data = resp.get_json()
    assert data["canvas"] == "foundry"
    assert "error" in data

# CUI // SP-CTI
"""Real-schema tests for pdx-test-01 — remaining PDC value paths.

Covers the untested boundaries / versions / change-requests / csp-equivalence /
template+snippet load / ai-trace / iqe-query / ask / collab routes against a REAL
sqlite DB wrapped in StorageConnection (the blueprint authors ``%s`` SQL; a raw
sqlite3 connection would choke — repo gotcha).

Highlights:
  * Boundaries + versions + change-requests CRUD with FK-enforced real rows.
  * Template + snippet load propagate classification onto the derived pipeline
    (fix-03): a SECRET snippet must NOT default to public.
  * csp-equivalence lookups (static constants) incl. 404 for unknown keys.
  * /api/ai-trace pagination + record_id filter + limit clamp (real
    canvas_ai_decisions rows).
  * /api/iqe-query validation + execute against the pipeline.snapshots collection,
    which on THIS branch reads ``pipeline_snapshots`` (NOT ``pdc_snapshots`` —
    #441's IQE repoint is not in this chain; see KNOWN-ISSUE below).
  * /api/ask delegation + top_k guard.
  * collab join happy path (session identity); push/poll/participants now reconciled
    to CanvasCollabManager's real API (pdx-hyg-01) and asserted as working.
  * pdx-hyg-01 hygiene: full-uuid PKs (no truncation), sanitized export download
    filenames, sops connection close, and the estimate_execution_time parallel flag.
"""
from __future__ import annotations

import json
import sqlite3
import sys
import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from flask import Flask  # noqa: E402

from tools.db.storage import StorageConnection  # noqa: E402
from tools.pipeline.db.init_db import SCHEMA  # noqa: E402

_AI_DECISIONS_SCHEMA = """
CREATE TABLE IF NOT EXISTS canvas_ai_decisions (
    id TEXT PRIMARY KEY, canvas_type TEXT NOT NULL, record_id TEXT,
    decision_type TEXT NOT NULL, decision TEXT NOT NULL, rationale TEXT,
    model_used TEXT, confidence REAL, alternatives TEXT DEFAULT '[]',
    trace_id TEXT, span_id TEXT, actor TEXT NOT NULL DEFAULT 'icdev-system',
    project_id TEXT, classification TEXT NOT NULL DEFAULT 'CUI', created_at TEXT
);
"""

_SNAPSHOTS_SCHEMA = """
CREATE TABLE IF NOT EXISTS pipeline_snapshots (
    id TEXT PRIMARY KEY, pipeline_id TEXT NOT NULL,
    snapshot_type TEXT NOT NULL DEFAULT 'baseline', label TEXT,
    nodes_json TEXT NOT NULL DEFAULT '[]', edges_json TEXT NOT NULL DEFAULT '[]',
    meta_json TEXT NOT NULL DEFAULT '{}', created_by TEXT, created_at TEXT NOT NULL
);
"""


# ── real-sqlite fixtures ──────────────────────────────────────────────────────


def _raw_conn(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _new_conn(db_path: Path) -> StorageConnection:
    return StorageConnection(_raw_conn(db_path), "sqlite")


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    p = tmp_path / "pipeline_canvas_test.db"
    conn = _raw_conn(p)
    conn.executescript(SCHEMA)
    conn.commit()
    conn.close()
    return p


@pytest.fixture
def app():
    import os

    os.environ.setdefault("ICDEV_PIPELINE_ENABLED", "true")
    with patch("tools.pipeline.blueprint.init_db"):
        from tools.pipeline.blueprint import create_pipeline_blueprint

        flask_app = Flask(__name__)
        flask_app.secret_key = "test-secret-key"
        flask_app.config["TESTING"] = True

        @flask_app.context_processor
        def _inject_base_ctx():
            return {"ROLE_VIEWS": {}, "current_role": None, "current_user": None}

        bp = create_pipeline_blueprint()
        assert bp is not None
        flask_app.register_blueprint(bp, url_prefix="/devops")
    return flask_app


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def wired(db_path):
    with patch("tools.pipeline.blueprint.get_connection", side_effect=lambda: _new_conn(db_path)), \
         patch("tools.knowledge_graph.canvas_ask.reindex_canvas_on_save"):
        yield db_path


def _login(client, user_id="dev-alice", role="developer"):
    with client.session_transaction() as sess:
        sess["user_id"] = user_id
        sess["role"] = role


def _seed_pipeline(db_path: Path, pipe_id: str, graph=None) -> None:
    conn = _raw_conn(db_path)
    conn.execute("INSERT INTO pipelines (id, name, graph_json) VALUES (?,?,?)",
                 (pipe_id, "P", json.dumps(graph or {"nodes": [], "edges": []})))
    conn.commit()
    conn.close()


# ══════════════════════════════════════════════════════════════════════════════
# 1. Boundaries CRUD
# ══════════════════════════════════════════════════════════════════════════════


def test_boundary_create_list_delete(client, wired):
    _login(client, role="developer")
    pid = str(uuid.uuid4())
    _seed_pipeline(wired, pid)

    create = client.post(
        f"/devops/api/boundaries/{pid}",
        data=json.dumps({"label": "Zone A", "classification": "SECRET",
                         "pos_x": 10, "pos_y": 20, "width": 400, "height": 300,
                         "node_ids": ["n1", "n2"]}),
        content_type="application/json",
    )
    assert create.status_code == 201, create.get_data(as_text=True)
    bid = create.get_json()["id"]

    listing = client.get(f"/devops/api/boundaries/{pid}")
    assert listing.status_code == 200
    rows = listing.get_json()
    assert len(rows) == 1
    assert rows[0]["id"] == bid
    assert rows[0]["label"] == "Zone A"
    assert rows[0]["classification"] == "SECRET"
    # node_ids stored as JSON string.
    assert json.loads(rows[0]["node_ids"]) == ["n1", "n2"]

    dele = client.delete(f"/devops/api/boundaries/{pid}/{bid}")
    assert dele.status_code == 200
    assert dele.get_json()["deleted"] is True
    assert client.get(f"/devops/api/boundaries/{pid}").get_json() == []


def test_boundary_create_nonnumeric_geometry_400(client, wired):
    _login(client, role="developer")
    pid = str(uuid.uuid4())
    _seed_pipeline(wired, pid)
    resp = client.post(f"/devops/api/boundaries/{pid}",
                       data=json.dumps({"pos_x": "abc"}), content_type="application/json")
    assert resp.status_code == 400, resp.get_data(as_text=True)
    assert "pos_x" in resp.get_json()["error"]


def test_boundary_create_denied_for_unknown_role(client, wired):
    _login(client, role=None)
    with client.session_transaction() as sess:
        sess["user_id"] = "x"
        sess.pop("role", None)
    pid = str(uuid.uuid4())
    _seed_pipeline(wired, pid)
    resp = client.post(f"/devops/api/boundaries/{pid}",
                       data=json.dumps({"label": "Z"}), content_type="application/json")
    assert resp.status_code == 403


# ══════════════════════════════════════════════════════════════════════════════
# 2. Versions
# ══════════════════════════════════════════════════════════════════════════════


def test_version_create_and_list(client, wired):
    _login(client, role="developer")
    pid = str(uuid.uuid4())
    _seed_pipeline(wired, pid, {"nodes": [{"id": "n1", "type": "scm-gitlab"}], "edges": []})

    v1 = client.post(f"/devops/api/versions/{pid}",
                     data=json.dumps({"label": "first", "notes": "init"}),
                     content_type="application/json")
    assert v1.status_code == 201, v1.get_data(as_text=True)
    assert v1.get_json()["version_num"] == 1

    v2 = client.post(f"/devops/api/versions/{pid}", data=json.dumps({}),
                     content_type="application/json")
    assert v2.get_json()["version_num"] == 2   # monotonic increment

    listing = client.get(f"/devops/api/versions/{pid}").get_json()
    assert [v["version_num"] for v in listing] == [2, 1]   # DESC order
    assert listing[1]["label"] == "first"
    assert listing[1]["notes"] == "init"


def test_version_create_unknown_pipeline_404(client, wired):
    _login(client, role="developer")
    resp = client.post(f"/devops/api/versions/{uuid.uuid4()}", data=json.dumps({}),
                       content_type="application/json")
    assert resp.status_code == 404


# ══════════════════════════════════════════════════════════════════════════════
# 3. Change requests
# ══════════════════════════════════════════════════════════════════════════════


def test_change_request_create_and_list(client, wired):
    _login(client, role="developer")
    pid = str(uuid.uuid4())
    _seed_pipeline(wired, pid)
    cr = client.post(f"/devops/api/change-requests/{pid}",
                     data=json.dumps({"cr_number": "CR-42", "cr_type": "modify",
                                      "markup": [{"x": 1}]}),
                     content_type="application/json")
    assert cr.status_code == 201, cr.get_data(as_text=True)
    cr_id = cr.get_json()["id"]
    listing = client.get(f"/devops/api/change-requests/{pid}").get_json()
    assert len(listing) == 1
    assert listing[0]["id"] == cr_id
    assert listing[0]["cr_number"] == "CR-42"
    assert listing[0]["status"] == "draft"


# ══════════════════════════════════════════════════════════════════════════════
# 4. CSP equivalence (static constants)
# ══════════════════════════════════════════════════════════════════════════════


def test_csp_equivalence_full_map(client, wired):
    _login(client)
    body = client.get("/devops/api/csp-equivalence").get_json()
    assert isinstance(body, dict) and "ci_cd_engine" in body


def test_csp_equivalence_detail_and_single(client, wired):
    _login(client)
    detail = client.get("/devops/api/csp-equivalence/ci_cd_engine")
    assert detail.status_code == 200
    assert "aws" in detail.get_json()

    single = client.get("/devops/api/csp-equivalence/ci_cd_engine/aws")
    assert single.status_code == 200
    assert single.get_json()   # non-empty mapping


def test_csp_equivalence_unknown_404(client, wired):
    _login(client)
    assert client.get("/devops/api/csp-equivalence/nope").status_code == 404
    assert client.get("/devops/api/csp-equivalence/ci_cd_engine/marscloud").status_code == 404


# ══════════════════════════════════════════════════════════════════════════════
# 5. Template + snippet load — classification propagation (fix-03)
# ══════════════════════════════════════════════════════════════════════════════


def test_template_load_creates_pipeline(client, wired):
    _login(client, role="developer")
    conn = _raw_conn(wired)
    conn.execute(
        "INSERT INTO pc_templates (id, name, description, graph_json) VALUES (?,?,?,?)",
        ("tpl1", "Baseline DoD", "d", json.dumps({"nodes": [{"id": "n1", "type": "scm-gitlab"}],
                                                  "edges": []})),
    )
    conn.commit()
    conn.close()
    resp = client.post("/devops/api/templates/tpl1/load")
    assert resp.status_code == 201, resp.get_data(as_text=True)
    new_id = resp.get_json()["id"]
    assert resp.get_json()["name"] == "Baseline DoD (copy)"
    # new pipeline persisted with the template's graph.
    row = _raw_conn(wired).execute("SELECT graph_json, template_id FROM pipelines WHERE id=?",
                                   (new_id,)).fetchone()
    assert row["template_id"] == "tpl1"
    assert json.loads(row["graph_json"])["nodes"][0]["type"] == "scm-gitlab"


def test_snippet_load_propagates_classification(client, wired):
    """fix-03: a SECRET snippet must produce a SECRET-classified pipeline, not
    default to public."""
    _login(client, role="developer")
    conn = _raw_conn(wired)
    conn.execute(
        "INSERT INTO pc_snippets (id, name, description, classification_level, graph_json) "
        "VALUES (?,?,?,?,?)",
        ("snip1", "Secret Scan Block", "d", "SECRET",
         json.dumps({"nodes": [{"id": "n1", "type": "scan-gitleaks"}], "edges": []})),
    )
    conn.commit()
    conn.close()
    resp = client.post("/devops/api/snippets/snip1/load")
    assert resp.status_code == 201, resp.get_data(as_text=True)
    new_id = resp.get_json()["id"]
    row = _raw_conn(wired).execute("SELECT classification FROM pipelines WHERE id=?",
                                   (new_id,)).fetchone()
    assert row["classification"] == "SECRET"


def test_template_load_unknown_404(client, wired):
    _login(client, role="developer")
    assert client.post("/devops/api/templates/nope/load").status_code == 404
    assert client.post("/devops/api/snippets/nope/load").status_code == 404


# ══════════════════════════════════════════════════════════════════════════════
# 6. /api/ai-trace — pagination + record_id filter + limit clamp
# ══════════════════════════════════════════════════════════════════════════════


@pytest.fixture
def ai_db(tmp_path):
    p = tmp_path / "ai.db"
    conn = _raw_conn(p)
    conn.executescript(_AI_DECISIONS_SCHEMA)
    for i in range(3):
        conn.execute(
            "INSERT INTO canvas_ai_decisions (id, canvas_type, record_id, decision_type, "
            "decision, created_at) VALUES (?,?,?,?,?,?)",
            (f"d{i}", "pdc", "rec-A" if i == 0 else "rec-B", "assess",
             f"decision-{i}", f"2026-07-1{i}T00:00:00Z"),
        )
    # a non-pdc row that must be excluded.
    conn.execute("INSERT INTO canvas_ai_decisions (id, canvas_type, decision_type, decision, "
                 "created_at) VALUES ('x','sdc','a','other','2026-07-19T00:00:00Z')")
    conn.commit()
    conn.close()
    return p


def test_ai_trace_returns_pdc_decisions(client, ai_db):
    _login(client)
    with patch("tools.db.storage.get_connection", side_effect=lambda *a, **k: _new_conn(ai_db)):
        resp = client.get("/devops/api/ai-trace")
    assert resp.status_code == 200, resp.get_data(as_text=True)
    body = resp.get_json()
    assert body["ok"] is True and body["canvas"] == "pdc"
    # only the 3 pdc rows, newest-first, non-pdc excluded.
    assert len(body["decisions"]) == 3
    assert all(d["canvas_type"] == "pdc" for d in body["decisions"])
    assert body["decisions"][0]["decision"] == "decision-2"


def test_ai_trace_record_id_filter(client, ai_db):
    _login(client)
    with patch("tools.db.storage.get_connection", side_effect=lambda *a, **k: _new_conn(ai_db)):
        resp = client.get("/devops/api/ai-trace?record_id=rec-A")
    body = resp.get_json()
    assert len(body["decisions"]) == 1
    assert body["decisions"][0]["record_id"] == "rec-A"


def test_ai_trace_bad_limit_400(client, ai_db):
    _login(client)
    with patch("tools.db.storage.get_connection", side_effect=lambda *a, **k: _new_conn(ai_db)):
        resp = client.get("/devops/api/ai-trace?limit=abc")
    assert resp.status_code == 400
    assert resp.get_json()["ok"] is False


# ══════════════════════════════════════════════════════════════════════════════
# 7. /api/iqe-query — validation, translate-only, and execute path
# ══════════════════════════════════════════════════════════════════════════════


def test_iqe_query_requires_question(client, wired):
    _login(client)
    resp = client.post("/devops/api/iqe-query", data=json.dumps({"question": "  "}),
                       content_type="application/json")
    assert resp.status_code == 400
    assert "question is required" in resp.get_json()["error"]


def test_iqe_query_translate_only(client, wired):
    """execute=False returns the NL->IQE translation without hitting the DB."""
    _login(client)
    with patch("tools.iqe.nl_to_iqe.nl_to_iqe",
               return_value={"iqe": "foreach s in pipeline.snapshots select *",
                             "explanation": "all snapshots"}):
        resp = client.post("/devops/api/iqe-query",
                           data=json.dumps({"question": "show snapshots", "execute": False}),
                           content_type="application/json")
    assert resp.status_code == 200, resp.get_data(as_text=True)
    body = resp.get_json()
    assert body["ok"] is True
    assert body["iqe"] == "foreach s in pipeline.snapshots select *"
    assert body["explanation"] == "all snapshots"


def test_iqe_query_execute_reads_pipeline_snapshots(client, wired, tmp_path):
    """KNOWN-ISSUE(pdx): the pipeline.snapshots IQE collection on THIS branch reads
    the ``pipeline_snapshots`` table (tools/iqe/adapters/pipeline.py). #441's repoint
    to ``pdc_snapshots`` is NOT in this chain, so a snapshot written to pdc_snapshots
    by the twin would NOT surface here. This test locks in the current source table.
    """
    _login(client)
    snap_db = tmp_path / "snap.db"
    conn = _raw_conn(snap_db)
    conn.executescript(_SNAPSHOTS_SCHEMA)
    conn.execute(
        "INSERT INTO pipeline_snapshots (id, pipeline_id, snapshot_type, nodes_json, "
        "edges_json, created_at) VALUES (?,?,?,?,?,?)",
        ("snap1", "pipe-x", "baseline", json.dumps([{"id": "n1", "type": "build"}]),
         "[]", "2026-06-01T00:00:00"),
    )
    conn.commit()
    conn.close()
    with patch("tools.iqe.nl_to_iqe.nl_to_iqe",
               return_value={"iqe": "foreach s in pipeline.snapshots select *",
                             "explanation": "all"}), \
         patch("tools.db.storage.get_connection", side_effect=lambda *a, **k: _raw_conn(snap_db)):
        resp = client.post("/devops/api/iqe-query",
                           data=json.dumps({"question": "snapshots", "execute": True}),
                           content_type="application/json")
    assert resp.status_code == 200, resp.get_data(as_text=True)
    body = resp.get_json()
    assert body["ok"] is True
    assert body["row_count"] == 1
    assert body["results"][0]["pipeline_id"] == "pipe-x"


# ══════════════════════════════════════════════════════════════════════════════
# 8. /api/ask — delegation + top_k guard
# ══════════════════════════════════════════════════════════════════════════════


def test_ask_delegates_and_returns_status(client, wired):
    _login(client)
    with patch("tools.knowledge_graph.canvas_ask.handle_ask_request",
               return_value={"answer": "build then test", "sources": [], "_status": 200}) as mock:
        resp = client.post("/devops/api/ask",
                           data=json.dumps({"query": "what runs first?", "top_k": 5}),
                           content_type="application/json")
    assert resp.status_code == 200, resp.get_data(as_text=True)
    body = resp.get_json()
    assert body["answer"] == "build then test"
    assert "_status" not in body   # popped before jsonify
    # graph_id / profile are wired for the PDC.
    _, kwargs = mock.call_args
    assert kwargs["graph_id"] == "pdc-designs"
    assert kwargs["top_k"] == 5


def test_ask_bad_top_k_400(client, wired):
    _login(client)
    resp = client.post("/devops/api/ask",
                       data=json.dumps({"query": "x", "top_k": "abc"}),
                       content_type="application/json")
    assert resp.status_code == 400
    assert "top_k" in resp.get_json()["error"]


# ══════════════════════════════════════════════════════════════════════════════
# 9. Collab — join / push / poll / participants (reconciled to manager API, pdx-hyg-01)
# ══════════════════════════════════════════════════════════════════════════════


def test_collab_join_uses_session_identity(client, wired):
    """join writes a pc_collab_sessions row attributed to the SESSION identity,
    ignoring any body-supplied user_id (identity spoofing defence)."""
    _login(client, user_id="real-user", role="developer")
    design_id = str(uuid.uuid4())
    _seed_pipeline(wired, design_id)   # design_id FK-references pipelines(id)
    with patch("tools.canvas.collaboration.get_connection",
               side_effect=lambda *a, **k: _new_conn(wired)):
        resp = client.post(f"/devops/api/collab/{design_id}/join",
                           data=json.dumps({"user_id": "spoofed", "user_name": "Mallory"}),
                           content_type="application/json")
    assert resp.status_code == 200, resp.get_data(as_text=True)
    assert resp.get_json()["user_id"] == "real-user"
    # persisted row carries the session identity, not the body user_id.
    row = _raw_conn(wired).execute(
        "SELECT user_id, user_name FROM pc_collab_sessions WHERE design_id=?", (design_id,)
    ).fetchone()
    assert row["user_id"] == "real-user"
    assert row["user_name"] == "Mallory"


def test_collab_push_records_operation(client, wired):
    """pdx-hyg-01: the push route now adapts to CanvasCollabManager.push(design_id,
    user_id, operation) — bundling op_type + data into the single operation dict the
    real (shared) manager API expects. Returns 200 with the echoed operation instead
    of raising TypeError (the sec-03 signature mismatch)."""
    _login(client, user_id="real-user", role="developer")
    design_id = str(uuid.uuid4())
    _seed_pipeline(wired, design_id)
    with patch("tools.canvas.collaboration.get_connection",
               side_effect=lambda *a, **k: _new_conn(wired)):
        client.post(f"/devops/api/collab/{design_id}/join",
                    data=json.dumps({"user_name": "Alice"}),
                    content_type="application/json")
        resp = client.post(f"/devops/api/collab/{design_id}/push",
                           data=json.dumps({"op_type": "add_node", "data": {"x": 1}}),
                           content_type="application/json")
    assert resp.status_code == 200, resp.get_data(as_text=True)
    body = resp.get_json()
    assert body["design_id"] == design_id
    # op_type + payload are bundled into the operation dict handed to the manager.
    assert body["operation"] == {"op_type": "add_node", "data": {"x": 1}}
    assert "pushed_at" in body


def test_collab_poll_returns_participants(client, wired):
    """pdx-hyg-01: the poll route now calls CanvasCollabManager.poll(design_id) and
    returns {operations, participants, polled_at} (200) instead of unpacking a
    3-tuple from a dict (the sec-03 TypeError)."""
    _login(client, user_id="real-user", role="developer")
    design_id = str(uuid.uuid4())
    _seed_pipeline(wired, design_id)
    with patch("tools.canvas.collaboration.get_connection",
               side_effect=lambda *a, **k: _new_conn(wired)):
        client.post(f"/devops/api/collab/{design_id}/join",
                    data=json.dumps({"user_name": "Alice"}),
                    content_type="application/json")
        resp = client.get(f"/devops/api/collab/{design_id}/poll?since=0")
    assert resp.status_code == 200, resp.get_data(as_text=True)
    body = resp.get_json()
    assert body["operations"] == []   # manager is participant-oriented, no op log
    assert "real-user" in [p["user_id"] for p in body["participants"]]


def test_collab_poll_bad_since_400(client, wired):
    """pdx-hyg-01: the poll route still validates the client-supplied `since` query
    param and returns 400 on garbage (input-validation preserved)."""
    _login(client, user_id="u", role="developer")
    resp = client.get(f"/devops/api/collab/{uuid.uuid4()}/poll?since=not-an-int")
    assert resp.status_code == 400


def test_collab_participants_lists_active(client, wired):
    """pdx-hyg-01: the participants route now calls CanvasCollabManager.participants()
    (the real method name) instead of the nonexistent get_participants(); returns 200
    with the active participant list."""
    _login(client, user_id="real-user", role="developer")
    design_id = str(uuid.uuid4())
    _seed_pipeline(wired, design_id)
    with patch("tools.canvas.collaboration.get_connection",
               side_effect=lambda *a, **k: _new_conn(wired)):
        client.post(f"/devops/api/collab/{design_id}/join",
                    data=json.dumps({"user_name": "Bob"}),
                    content_type="application/json")
        resp = client.get(f"/devops/api/collab/{design_id}/participants")
    assert resp.status_code == 200, resp.get_data(as_text=True)
    assert "real-user" in [p["user_id"] for p in resp.get_json()["participants"]]


# ══════════════════════════════════════════════════════════════════════════════
# 10. pdx-hyg-01 hygiene — full-uuid PKs, download-name sanitize, sops close, parallel
# ══════════════════════════════════════════════════════════════════════════════


def test_created_ids_are_full_uuids(client, wired):
    """pdx-hyg-01: entity PKs use full uuid4() strings, not truncated [:8]/[:12]
    slices (truncation multiplies collision risk)."""
    _login(client, role="developer")
    pid = str(uuid.uuid4())
    _seed_pipeline(wired, pid, {"nodes": [{"id": "n1", "type": "scm-gitlab"}], "edges": []})
    resp = client.post(f"/devops/api/versions/{pid}",
                       data=json.dumps({"label": "x"}), content_type="application/json")
    assert resp.status_code == 201, resp.get_data(as_text=True)
    new_id = resp.get_json()["id"]
    assert len(new_id) == 36            # canonical uuid4 length (not 8 or 12)
    uuid.UUID(new_id)                   # parses as a valid uuid (raises otherwise)


def test_deploy_zip_download_name_sanitized(client, wired):
    """pdx-hyg-01: the deploy-bundle zip download_name is sanitized to [a-z0-9._-]
    so a hostile pipeline name cannot inject path separators / spaces / header chars
    into the Content-Disposition filename."""
    _login(client, role="developer")
    pid = str(uuid.uuid4())
    _seed_pipeline(wired, pid, {"nodes": [{"id": "n1", "type": "scm-gitlab"}], "edges": []})
    # Rename the pipeline to a hostile value AFTER seeding.
    conn = _raw_conn(wired)
    conn.execute("UPDATE pipelines SET name=? WHERE id=?", ("../../etc/passwd owned!", pid))
    conn.commit()
    conn.close()
    resp = client.post(f"/devops/api/deploy/{pid}",
                       data=json.dumps({"format": "zip"}), content_type="application/json")
    assert resp.status_code == 200, resp.get_data(as_text=True)
    cd = resp.headers.get("Content-Disposition", "")
    import re as _re
    m = _re.search(r"filename=\"?([^\";]+)\"?", cd)
    assert m, cd
    filename = m.group(1)
    assert "/" not in filename and " " not in filename
    assert _re.fullmatch(r"[a-z0-9._-]+", filename), filename
    assert filename.endswith("-deploy-bundle.zip")


def test_sops_get_all_closes_connection():
    """pdx-hyg-01: the SQLite fallback wraps _get_conn() in contextlib.closing, so the
    connection is closed after use. sqlite3's `with conn` commits but never closes,
    leaking connections; closing() guarantees the close."""
    mock_conn = MagicMock()
    mock_conn.execute.return_value.fetchall.return_value = []
    with patch("tools.pipeline.db.init_db.get_connection", return_value=mock_conn):
        from tools.pipeline import sops
        result = sops.get_all_sops()
    assert result == []
    mock_conn.close.assert_called_once()


def test_estimate_execution_time_honors_any_parallel_node():
    """pdx-hyg-01: a stage is parallel if ANY of its nodes sets parallel=True, not
    only the first node encountered (the previous first-node-only bug)."""
    from tools.pipeline.constants import estimate_execution_time
    # Two nodes in the SAME stage; only the SECOND declares parallel=True.
    nodes = [
        {"id": "a", "stage": "test", "config": {"avg_execution_min": 10, "parallel": False}},
        {"id": "b", "stage": "test", "config": {"avg_execution_min": 10, "parallel": True}},
    ]
    result = estimate_execution_time(nodes, [])
    # stage total = 20 min; with the parallel discount (0.6) => 12.0, not 20.0.
    assert result["total_minutes"] == 12.0

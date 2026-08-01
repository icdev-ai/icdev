# CUI // SP-CTI
"""Tests for pdx-data-02 — pipeline DELETE cascades to children + 404 semantics.

Defect: DELETE /devops/api/pipelines/<id> raised an unhandled IntegrityError for
any pipeline that had children (pc_versions, pc_boundaries, pdc_snapshots,
pdc_simulations, pc_change_requests, pc_compliance_checks, pc_compliance_findings
all REFERENCES pipelines(id) WITHOUT ON DELETE CASCADE). Because auto-snapshot
fires on every save, every real pipeline has at least one pdc_snapshots child, so
DELETE always 500'd. Also PUT/DELETE returned success for non-existent ids.

Fixes verified here (against a REAL sqlite DB with PRAGMA foreign_keys=ON, so the
FK constraints are actually enforced):
  1. Deleting a pipeline that has a version + snapshot + boundary (+ more) ->
     200, all child rows gone.
  2. pc_audit rows for that pipeline id REMAIN (append-only; never deleted).
  3. DELETE unknown id -> 404.
  4. PUT unknown id -> 404.
  5. Audit failure during DELETE -> rollback leaves children intact (extends the
     sec-04 fail-closed guarantee with a real transaction).
"""
from __future__ import annotations

import json
import sqlite3
import sys
import uuid
from pathlib import Path
from unittest.mock import patch

import pytest

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from flask import Flask  # noqa: E402

from tools.db.storage import StorageConnection  # noqa: E402
from tools.pipeline.db.init_db import SCHEMA  # noqa: E402


# ── real-sqlite fixtures ──────────────────────────────────────────────────────


def _raw_conn(db_path: Path) -> sqlite3.Connection:
    """Fresh raw sqlite connection with FK enforcement (uses ? placeholders).

    Used for test-side seeding + verification, which author their own ? SQL.
    """
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _new_conn(db_path: Path) -> StorageConnection:
    """Connection the ROUTE sees, wrapped in StorageConnection so the blueprint's
    %s placeholders are translated to ? for sqlite (mirrors the production PG
    path; a raw sqlite3 connection would choke on %s). FK enforcement stays on.
    """
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


def _login(client, user_id="isso-dan", role="isso"):
    # DELETE requires an elevated role (admin/isso); PUT requires a write role.
    with client.session_transaction() as sess:
        sess["user_id"] = user_id
        sess["role"] = role


def _seed_pipeline_with_children(db_path: Path, pipe_id: str) -> None:
    """Insert one pipeline plus a child row in every FK table + 2 pc_audit rows."""
    conn = _raw_conn(db_path)
    conn.execute(
        "INSERT INTO pipelines (id, name, graph_json) VALUES (?,?,?)",
        (pipe_id, "Cascade Test", '{"nodes":[],"edges":[]}'),
    )
    conn.execute(
        "INSERT INTO pc_versions (id, pipeline_id, version_num, graph_json) VALUES (?,?,?,?)",
        ("v1", pipe_id, 1, "{}"),
    )
    conn.execute(
        "INSERT INTO pc_boundaries (id, pipeline_id, label) VALUES (?,?,?)",
        ("b1", pipe_id, "Zone A"),
    )
    snap_id = "s1"
    conn.execute(
        "INSERT INTO pdc_snapshots (id, pipeline_id, graph_json) VALUES (?,?,?)",
        (snap_id, pipe_id, "{}"),
    )
    conn.execute(
        "INSERT INTO pdc_simulations (id, pipeline_id, baseline_snap_id, delta_graph_json) "
        "VALUES (?,?,?,?)",
        ("sim1", pipe_id, snap_id, "{}"),
    )
    conn.execute(
        "INSERT INTO pc_change_requests (id, pipeline_id, cr_number) VALUES (?,?,?)",
        ("cr1", pipe_id, "CR-1"),
    )
    check_id = "chk1"
    conn.execute(
        "INSERT INTO pc_compliance_checks (id, pipeline_id, check_type) VALUES (?,?,?)",
        (check_id, pipe_id, "full_audit"),
    )
    conn.execute(
        "INSERT INTO pc_compliance_findings (id, pipeline_id, audit_id, rule_id, framework, title) "
        "VALUES (?,?,?,?,?,?)",
        ("f1", pipe_id, check_id, "R-1", "NIST", "finding"),
    )
    conn.execute(
        "INSERT INTO pc_stages (id, pipeline_id, stage_type, label) VALUES (?,?,?,?)",
        ("st1", pipe_id, "build", "Build"),
    )
    conn.execute(
        "INSERT INTO pc_collab_sessions (id, design_id, user_id) VALUES (?,?,?)",
        ("cs1", pipe_id, "user-1"),
    )
    # Two append-only audit rows referencing the pipeline by id value.
    conn.execute(
        "INSERT INTO pc_audit (action, entity_type, entity_id, details) VALUES (?,?,?,?)",
        ("CREATE", "pipeline", pipe_id, "seed"),
    )
    conn.execute(
        "INSERT INTO pc_audit (action, entity_type, entity_id, details) VALUES (?,?,?,?)",
        ("UPDATE", "pipeline", pipe_id, "seed"),
    )
    conn.commit()
    conn.close()


_CHILD_TABLES = (
    ("pc_versions", "pipeline_id"),
    ("pc_boundaries", "pipeline_id"),
    ("pdc_snapshots", "pipeline_id"),
    ("pdc_simulations", "pipeline_id"),
    ("pc_change_requests", "pipeline_id"),
    ("pc_compliance_checks", "pipeline_id"),
    ("pc_compliance_findings", "pipeline_id"),
    ("pc_stages", "pipeline_id"),
    ("pc_collab_sessions", "design_id"),
)


def _count(db_path: Path, table: str, col: str, pipe_id: str) -> int:
    conn = _raw_conn(db_path)
    n = conn.execute(f"SELECT COUNT(*) FROM {table} WHERE {col}=?", (pipe_id,)).fetchone()[0]
    conn.close()
    return n


# ── tests ─────────────────────────────────────────────────────────────────────


def test_delete_cascades_children_and_preserves_audit(client, db_path):
    """Deleting a pipeline with children -> 200, children gone, pc_audit rows remain."""
    pipe_id = str(uuid.uuid4())
    _seed_pipeline_with_children(db_path, pipe_id)
    _login(client, role="isso")

    with patch(
        "tools.pipeline.blueprint.get_connection",
        side_effect=lambda: _new_conn(db_path),
    ):
        resp = client.delete(f"/devops/api/pipelines/{pipe_id}")

    assert resp.status_code == 200, resp.get_data(as_text=True)
    assert resp.get_json().get("deleted") is True

    # Pipeline row gone.
    assert _count(db_path, "pipelines", "id", pipe_id) == 0
    # Every child row gone.
    for table, col in _CHILD_TABLES:
        assert _count(db_path, table, col, pipe_id) == 0, f"{table} rows survived delete"
    # Audit trail preserved: the 2 seed rows PLUS the DELETE audit row written by
    # the route = 3, and never fewer than the 2 originals.
    audit_rows = _count(db_path, "pc_audit", "entity_id", pipe_id)
    assert audit_rows >= 2, f"pc_audit rows for the pipeline must be preserved, got {audit_rows}"


def test_delete_unknown_id_returns_404(client, db_path):
    """DELETE of a non-existent pipeline -> 404 (nothing deleted, no audit row)."""
    _login(client, role="admin")
    unknown = str(uuid.uuid4())
    with patch(
        "tools.pipeline.blueprint.get_connection",
        side_effect=lambda: _new_conn(db_path),
    ):
        resp = client.delete(f"/devops/api/pipelines/{unknown}")
    assert resp.status_code == 404, resp.get_data(as_text=True)
    # No delete-audit row should have been written for a pipeline that never existed.
    assert _count(db_path, "pc_audit", "entity_id", unknown) == 0


def test_put_unknown_id_returns_404(client, db_path):
    """PUT to a non-existent pipeline -> 404 (previously returned {"updated": true})."""
    _login(client, role="developer")
    unknown = str(uuid.uuid4())
    with patch(
        "tools.pipeline.blueprint.get_connection",
        side_effect=lambda: _new_conn(db_path),
    ), patch("tools.knowledge_graph.canvas_ask.reindex_canvas_on_save"), \
         patch("tools.pipeline.twin.take_snapshot", create=True):
        resp = client.put(
            f"/devops/api/pipelines/{unknown}",
            data=json.dumps({"name": "ghost", "graph_json": '{"nodes":[],"edges":[]}'}),
            content_type="application/json",
        )
    assert resp.status_code == 404, resp.get_data(as_text=True)


def test_delete_audit_failure_rolls_back_children(client, db_path):
    """If the delete-audit write fails, the whole cascade rolls back -> children intact.

    Extends the sec-04 fail-closed guarantee with a REAL transaction: the child
    deletes and the pipeline delete must be rolled back, not left half-applied.
    """
    from tools.pipeline.blueprint import AuditUnavailable

    pipe_id = str(uuid.uuid4())
    _seed_pipeline_with_children(db_path, pipe_id)
    _login(client, role="isso")

    def _boom(*a, **k):
        raise AuditUnavailable("audit db down")

    with patch(
        "tools.pipeline.blueprint.get_connection",
        side_effect=lambda: _new_conn(db_path),
    ), patch("tools.pipeline.blueprint._audit_strict", side_effect=_boom):
        resp = client.delete(f"/devops/api/pipelines/{pipe_id}")

    assert resp.status_code == 500, resp.get_data(as_text=True)
    assert "audit trail unavailable" in resp.get_data(as_text=True)
    # Rollback must have restored the pipeline AND every child row.
    assert _count(db_path, "pipelines", "id", pipe_id) == 1, "pipeline must survive rollback"
    for table, col in _CHILD_TABLES:
        assert _count(db_path, table, col, pipe_id) == 1, f"{table} child must survive rollback"

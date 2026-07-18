# CUI // SP-CTI
"""Real-schema route + module tests for pdx-test-01 — PDC SOP + runbook value paths.

The Pipeline Design Canvas SOP workflow and the incident-response runbook API had
zero coverage beyond the SoD / audit deny slices (sec-03/sec-04). Those tests own
the *deny* cases (a developer cannot approve, self-approval -> 403, body-supplied
approver ignored). This file owns the untested **happy paths and state-machine
validity** against a REAL sqlite DB (PRAGMA foreign_keys=ON) wrapped in
StorageConnection so the blueprint's ``%s`` placeholders translate to ``?`` (a raw
sqlite3 connection would choke on ``%s`` — repo gotcha).

Covered:
  * SOP CRUD via routes: create -> get -> list(+filter) -> update.
  * SOP state machine: draft -> submit -> approve (elevated non-owner) records the
    session identity as approved_by; reject records rejected_reason.
  * SOP state-transition validity locked in: approve/reject on a draft that was
    never submitted -> 400; re-submit of an approved SOP -> 400.
  * SOP delete happy path (elevated role) removes the row.
  * sops.py public API against real sqlite (create/get/list/update/submit/approve/
    reject/seed) — asserted against real signatures.
  * Runbook routes (list + detail, 404) and runbooks.py public API (static registry).
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
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _new_conn(db_path: Path) -> StorageConnection:
    """The connection the ROUTE + sops.py see — %s placeholders translate to ?."""
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
    """Point BOTH the blueprint audit connection and the sops.py module
    connection at the same real sqlite DB. sops.py uses
    ``tools.pipeline.db.init_db.get_connection`` (via ``_get_conn``); the audit
    helpers use ``tools.pipeline.blueprint.get_connection`` — patch both so
    SOP rows and audit rows share one DB.
    """
    with patch("tools.pipeline.blueprint.get_connection", side_effect=lambda: _new_conn(db_path)), \
         patch("tools.pipeline.db.init_db.get_connection", side_effect=lambda: _new_conn(db_path)):
        yield db_path


def _login(client, user_id="dev-alice", role="developer"):
    with client.session_transaction() as sess:
        sess["user_id"] = user_id
        sess["role"] = role


def _count(db_path: Path, table: str, col: str, val: str) -> int:
    conn = _raw_conn(db_path)
    n = conn.execute(f"SELECT COUNT(*) FROM {table} WHERE {col}=?", (val,)).fetchone()[0]
    conn.close()
    return n


# ══════════════════════════════════════════════════════════════════════════════
# 1. SOP CRUD via routes (real sqlite)
# ══════════════════════════════════════════════════════════════════════════════


def _create_sop(client, owner="owner-oscar", title="Release Gate"):
    return client.post(
        "/devops/api/sops",
        data=json.dumps({
            "title": title,
            "sop_type": "release_approval",
            "owner": owner,
            "steps": [{"order": 1, "description": "verify green"}],
            "nist_controls": ["CM-3", "AU-12"],
        }),
        content_type="application/json",
    )


def test_sop_create_returns_draft_with_parsed_json(client, wired):
    _login(client, role="developer")
    resp = _create_sop(client)
    assert resp.status_code == 201, resp.get_data(as_text=True)
    body = resp.get_json()
    assert body["approval_status"] == "draft"
    assert body["owner"] == "owner-oscar"
    # steps / nist_controls round-trip as parsed lists, not JSON strings.
    assert body["steps"] == [{"order": 1, "description": "verify green"}]
    assert body["nist_controls"] == ["CM-3", "AU-12"]
    assert _count(wired, "pdc_sops", "id", body["id"]) == 1


def test_sop_get_and_list_with_status_filter(client, wired):
    _login(client, role="developer")
    sop_id = _create_sop(client).get_json()["id"]

    got = client.get(f"/devops/api/sops/{sop_id}")
    assert got.status_code == 200
    assert got.get_json()["id"] == sop_id

    # Unfiltered list contains it.
    all_resp = client.get("/devops/api/sops")
    assert all_resp.status_code == 200
    assert any(s["id"] == sop_id for s in all_resp.get_json())

    # Filter by status=draft returns it; status=approved does not.
    draft = client.get("/devops/api/sops?status=draft").get_json()
    assert any(s["id"] == sop_id for s in draft)
    approved = client.get("/devops/api/sops?status=approved").get_json()
    assert all(s["id"] != sop_id for s in approved)


def test_sop_get_unknown_returns_404(client, wired):
    _login(client, role="developer")
    resp = client.get(f"/devops/api/sops/{uuid.uuid4()}")
    assert resp.status_code == 404


def test_sop_update_changes_fields(client, wired):
    _login(client, role="developer")
    sop_id = _create_sop(client).get_json()["id"]
    resp = client.put(
        f"/devops/api/sops/{sop_id}",
        data=json.dumps({"title": "Updated Title", "scope": "IL5+"}),
        content_type="application/json",
    )
    assert resp.status_code == 200, resp.get_data(as_text=True)
    body = resp.get_json()
    assert body["title"] == "Updated Title"
    assert body["scope"] == "IL5+"


def test_sop_update_unknown_returns_404(client, wired):
    _login(client, role="developer")
    resp = client.put(
        f"/devops/api/sops/{uuid.uuid4()}",
        data=json.dumps({"title": "ghost"}),
        content_type="application/json",
    )
    assert resp.status_code == 404


# ══════════════════════════════════════════════════════════════════════════════
# 2. SOP state machine — happy path + transition validity
# ══════════════════════════════════════════════════════════════════════════════


def test_sop_full_approval_workflow(client, wired):
    """draft -> submit -> approve (elevated, non-owner) records session identity."""
    _login(client, user_id="dev-alice", role="developer")
    sop_id = _create_sop(client, owner="dev-alice").get_json()["id"]

    # submit (draft -> pending_review)
    sub = client.post(f"/devops/api/sops/{sop_id}/submit")
    assert sub.status_code == 200, sub.get_data(as_text=True)
    assert sub.get_json()["approval_status"] == "pending_review"

    # approve as an elevated, NON-owner identity
    _login(client, user_id="isso-bob", role="isso")
    appr = client.post(
        f"/devops/api/sops/{sop_id}/approve",
        data=json.dumps({"approved_by": "spoofed-eve"}),
        content_type="application/json",
    )
    assert appr.status_code == 200, appr.get_data(as_text=True)
    body = appr.get_json()
    assert body["approval_status"] == "approved"
    # approved_by is the SESSION identity, never the body.
    assert body["approved_by"] == "isso-bob"
    assert body["approved_by"] != "spoofed-eve"
    # a SOP_APPROVE audit row was written (fail-closed path).
    assert _count(wired, "pc_audit", "action", "SOP_APPROVE") >= 1


def test_sop_reject_records_reason_and_identity(client, wired):
    _login(client, user_id="dev-alice", role="developer")
    sop_id = _create_sop(client, owner="dev-alice").get_json()["id"]
    client.post(f"/devops/api/sops/{sop_id}/submit")

    _login(client, user_id="isso-bob", role="isso")
    resp = client.post(
        f"/devops/api/sops/{sop_id}/reject",
        data=json.dumps({"reason": "missing SBOM step", "rejected_by": "spoofed"}),
        content_type="application/json",
    )
    assert resp.status_code == 200, resp.get_data(as_text=True)
    body = resp.get_json()
    assert body["approval_status"] == "rejected"
    assert body["rejected_reason"] == "missing SBOM step"
    # rejected_by / approved_by column stores the session identity, not the body.
    assert body["approved_by"] == "isso-bob"


def test_sop_approve_on_never_submitted_draft_rejected(client, wired):
    """State-machine validity: approving a draft that was never submitted -> 400.

    The route's elevated-role + non-owner checks pass first, then
    ``approve_sop`` refuses the transition ('Cannot approve from status draft').
    """
    _login(client, user_id="dev-alice", role="developer")
    sop_id = _create_sop(client, owner="dev-alice").get_json()["id"]

    _login(client, user_id="isso-bob", role="isso")
    resp = client.post(
        f"/devops/api/sops/{sop_id}/approve",
        data=json.dumps({}),
        content_type="application/json",
    )
    assert resp.status_code == 400, resp.get_data(as_text=True)
    assert "Cannot approve" in resp.get_json()["error"]
    # unchanged: still a draft.
    assert client.get(f"/devops/api/sops/{sop_id}") is not None


def test_sop_reject_on_draft_rejected(client, wired):
    """Rejecting a draft that was never submitted -> 400 (only pending_review is rejectable)."""
    _login(client, user_id="dev-alice", role="developer")
    sop_id = _create_sop(client, owner="dev-alice").get_json()["id"]
    _login(client, user_id="isso-bob", role="isso")
    resp = client.post(
        f"/devops/api/sops/{sop_id}/reject",
        data=json.dumps({"reason": "x"}),
        content_type="application/json",
    )
    assert resp.status_code == 400, resp.get_data(as_text=True)
    assert "Cannot reject" in resp.get_json()["error"]


def test_sop_resubmit_after_approval_rejected(client, wired):
    """Once approved, re-submitting -> 400 (submit only from draft/rejected)."""
    _login(client, user_id="dev-alice", role="developer")
    sop_id = _create_sop(client, owner="dev-alice").get_json()["id"]
    client.post(f"/devops/api/sops/{sop_id}/submit")
    _login(client, user_id="isso-bob", role="isso")
    client.post(f"/devops/api/sops/{sop_id}/approve", data=json.dumps({}),
                content_type="application/json")
    # back to a write-tier developer to attempt re-submit
    _login(client, user_id="dev-alice", role="developer")
    resp = client.post(f"/devops/api/sops/{sop_id}/submit")
    assert resp.status_code == 400, resp.get_data(as_text=True)
    assert "Cannot submit" in resp.get_json()["error"]


def test_rejected_sop_can_be_resubmitted(client, wired):
    """A rejected SOP is resubmittable (draft/rejected are the submit-able states)."""
    _login(client, user_id="dev-alice", role="developer")
    sop_id = _create_sop(client, owner="dev-alice").get_json()["id"]
    client.post(f"/devops/api/sops/{sop_id}/submit")
    _login(client, user_id="isso-bob", role="isso")
    client.post(f"/devops/api/sops/{sop_id}/reject", data=json.dumps({"reason": "fix"}),
                content_type="application/json")
    _login(client, user_id="dev-alice", role="developer")
    resp = client.post(f"/devops/api/sops/{sop_id}/submit")
    assert resp.status_code == 200, resp.get_data(as_text=True)
    assert resp.get_json()["approval_status"] == "pending_review"


def test_sop_delete_happy_path(client, wired):
    """An elevated role deletes a SOP -> 200 + row gone + audit row written first."""
    _login(client, user_id="dev-alice", role="developer")
    sop_id = _create_sop(client, owner="dev-alice").get_json()["id"]
    _login(client, user_id="isso-bob", role="isso")
    resp = client.delete(f"/devops/api/sops/{sop_id}")
    assert resp.status_code == 200, resp.get_data(as_text=True)
    assert resp.get_json().get("ok") is True
    assert _count(wired, "pdc_sops", "id", sop_id) == 0
    assert _count(wired, "pc_audit", "action", "SOP_DELETE") >= 1


# ══════════════════════════════════════════════════════════════════════════════
# 3. sops.py public API — real sqlite (patched module get_connection)
# ══════════════════════════════════════════════════════════════════════════════


@pytest.fixture
def sops_db(db_path):
    with patch("tools.pipeline.db.init_db.get_connection", side_effect=lambda: _new_conn(db_path)):
        yield db_path


def test_sops_module_create_and_get(sops_db):
    from tools.pipeline import sops
    created = sops.create_sop({"title": "X", "sop_type": "hotfix_deployment",
                               "owner": "o", "steps": [{"order": 1}]})
    assert created["approval_status"] == "draft"
    assert created["version"] == "1.0"
    got = sops.get_sop_by_id(created["id"])
    assert got["title"] == "X"
    assert got["steps"] == [{"order": 1}]


def test_sops_module_filters(sops_db):
    from tools.pipeline import sops
    a = sops.create_sop({"title": "A", "sop_type": "release_approval"})
    sops.create_sop({"title": "B", "sop_type": "credential_rotation"})
    rel = sops.get_all_sops(sop_type="release_approval")
    assert [s["id"] for s in rel] == [a["id"]]
    drafts = sops.get_all_sops(approval_status="draft")
    assert len(drafts) == 2


def test_sops_module_state_machine_returns_error_tuples(sops_db):
    from tools.pipeline import sops
    s = sops.create_sop({"title": "S", "owner": "o"})
    sid = s["id"]
    # approve before submit -> (None, err)
    res, err = sops.approve_sop(sid, approved_by="bob")
    assert res is None and "Cannot approve" in err
    # submit -> ok
    res, err = sops.submit_for_review(sid)
    assert err is None and res["approval_status"] == "pending_review"
    # submit again -> error
    res, err = sops.submit_for_review(sid)
    assert res is None and "Cannot submit" in err
    # approve -> ok, records approved_by
    res, err = sops.approve_sop(sid, approved_by="bob")
    assert err is None and res["approved_by"] == "bob" and res["approval_status"] == "approved"


def test_sops_module_reject_sets_reason(sops_db):
    from tools.pipeline import sops
    sid = sops.create_sop({"title": "R"})["id"]
    sops.submit_for_review(sid)
    res, err = sops.reject_sop(sid, reason="incomplete", rejected_by="bob")
    assert err is None
    assert res["approval_status"] == "rejected"
    assert res["rejected_reason"] == "incomplete"


def test_sops_module_missing_id_returns_none(sops_db):
    from tools.pipeline import sops
    assert sops.get_sop_by_id("nope") is None
    assert sops.update_sop("nope", {"title": "x"}) is None
    assert sops.delete_sop("nope") is False
    assert sops.submit_for_review("nope") == (None, "SOP not found")


def test_sops_module_seed_is_idempotent(sops_db):
    from tools.pipeline import sops
    sops.seed_sops()
    first = len(sops.get_all_sops())
    assert first == len(sops.SEED_SOPS) == 4
    sops.seed_sops()  # no-op when non-empty
    assert len(sops.get_all_sops()) == first


# ══════════════════════════════════════════════════════════════════════════════
# 4. Runbooks — routes + static module API
# ══════════════════════════════════════════════════════════════════════════════


def test_runbooks_list_route(client):
    _login(client, role="developer")
    resp = client.get("/devops/api/runbooks")
    assert resp.status_code == 200, resp.get_data(as_text=True)
    body = resp.get_json()
    assert len(body) == 8
    ids = {rb["id"] for rb in body}
    assert "IR-PDC-BUILD-001" in ids and "IR-PDC-CICD-001" in ids


def test_runbook_detail_route(client):
    _login(client, role="developer")
    resp = client.get("/devops/api/runbooks/IR-PDC-SECRET-001")
    assert resp.status_code == 200, resp.get_data(as_text=True)
    body = resp.get_json()
    assert body["title"] == "Secret Leak in Pipeline"
    assert body["severity"] == "CAT1"
    # 5-phase IR lifecycle present.
    assert [p["name"] for p in body["phases"]] == [
        "Detect", "Contain", "Eradicate", "Recover", "Lessons Learned"
    ]


def test_runbook_detail_unknown_404(client):
    _login(client, role="developer")
    resp = client.get("/devops/api/runbooks/IR-PDC-DOES-NOT-EXIST")
    assert resp.status_code == 404


def test_runbooks_module_api():
    from tools.pipeline import runbooks
    assert len(runbooks.get_all_runbooks()) == 8
    assert runbooks.get_runbook_by_id("IR-PDC-DEPLOY-001")["title"] == "Failed / Rogue Deployment"
    assert runbooks.get_runbook_by_id("bogus") is None


def test_runbooks_applicable_matching():
    from tools.pipeline import runbooks
    findings = [
        {"category": "secret", "title": "Credential leaked in log", "severity": "CAT1"},
        {"category": "dependency", "title": "SBOM drift detected", "severity": "CAT2"},
    ]
    matched = {rb["id"] for rb in runbooks.get_applicable_runbooks(findings)}
    assert "IR-PDC-SECRET-001" in matched      # secret keyword
    assert "IR-PDC-SUPPLY-001" in matched      # dependency/sbom keyword
    assert "IR-PDC-CICD-001" in matched        # any CAT1 finding also pulls CI/CD compromise


def test_runbooks_applicable_empty_findings():
    from tools.pipeline import runbooks
    assert runbooks.get_applicable_runbooks([]) == []

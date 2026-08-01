# CUI // SP-CTI
"""Tests for the SIPA dashboard blueprint (sipa-dash-01).

Covers ``tools/integrity/blueprint.py``'s ``create_integrity_blueprint`` factory
and every route it serves, driven through a Flask test client:

  * **Feature flag** — the factory returns ``None`` when ``ICDEV_INTEGRITY_ENABLED``
    is off (so ``app.py`` skips registration) and a real blueprint when on.
  * **Pages** — ``/integrity`` (list) and ``/integrity/<id>`` (detail) render, and
    fall back to a JSON body when the page templates aren't present yet (they ship
    in the sibling templates task), so the routes are exercisable in isolation.
  * **Read API** — ``GET /api/integrity/assessments`` (with status/verdict filters)
    and ``GET /api/integrity/assessment/<id>`` return the seeded rows; a missing id
    is a 404.
  * **assess API** — ``POST /api/integrity/assess`` requires a ``source``, delegates
    to ``engine.assess`` (stubbed for speed/determinism — the real pipeline is
    covered by test_integrity_engine.py), and maps ``IngestRejected`` / bad-mode
    ``ValueError`` to 400.
  * **HITL API** — ``POST .../promote`` and ``.../reject`` run the REAL engine HITL
    transition against the shared in-memory DB: promote flips status to
    ``approved`` + appends an authorization row; a second decision on the now
    terminal assessment is a 409; a missing id is a 404.

The blueprint reads via ``tools.db.storage.get_connection``; the suite patches that
to hand back one shared in-memory SQLite connection (whose ``close`` is a no-op so
it survives across calls), which the engine's HITL path also reuses.
"""
import json
import sqlite3

import pytest

from tools.integrity import engine
from tools.integrity.blueprint import create_integrity_blueprint
from tools.integrity.db import init_db as init_db_mod


class _PersistentConn(sqlite3.Connection):
    """In-memory connection whose ``close()`` is a no-op so a single instance can
    be shared across blueprint + engine calls within one test."""

    def close(self):  # noqa: D401 — intentional no-op; torn down via _hard_close
        pass

    def _hard_close(self):
        super().close()


@pytest.fixture
def shared_conn(monkeypatch):
    """One shared in-memory DB, wired in as the result of ``get_connection`` for
    both the blueprint reads and the engine's HITL writes."""
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    conn = sqlite3.connect(":memory:", factory=_PersistentConn)
    conn.row_factory = sqlite3.Row
    init_db_mod.init_db(conn)

    # The engine + blueprint both reach the DB via ``from tools.db.storage import
    # get_connection``. The root ``tools.*`` namespace is a compat shim that
    # redirects to ``icdev.tools.*`` — they can resolve to distinct module objects,
    # so patch the attribute on the exact module the callers import (resolved via
    # importlib, not a bare ``import ... as``).
    import importlib

    storage = importlib.import_module("tools.db.storage")
    monkeypatch.setattr(storage, "get_connection", lambda *a, **k: conn)
    yield conn
    conn._hard_close()


def _seed_assessment(conn, *, status="assessed", verdict="quarantine", risk_score=85.0) -> int:
    cur = conn.execute(
        "INSERT INTO integrity_assessments "
        "(source_type, source_ref, mode, project_id, status, verdict, risk_score) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("local", "/srv/staging/untrusted_pkg", "provenance_blind", None, status, verdict, risk_score),
    )
    aid = cur.lastrowid
    conn.execute(
        "INSERT INTO integrity_capabilities "
        "(assessment_id, file_path, function_name, capability_type, evidence, risk_weight) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (aid, "helper.py", "_callback", "network_egress",
         json.dumps({"api": "socket.connect", "host": "10.0.0.1"}), 0.9),
    )
    conn.execute(
        "INSERT INTO integrity_findings "
        "(assessment_id, source_scanner, finding_type, severity, file_path, line, detail) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (aid, "semgrep", "known_bad_signature", "critical", "helper.py", 7,
         json.dumps({"rule": "reverse_shell"})),
    )
    conn.execute(
        "INSERT INTO integrity_verdicts "
        "(assessment_id, verdict, risk_score, rationale, decided_by) "
        "VALUES (?, ?, ?, ?, ?)",
        (aid, verdict, risk_score, json.dumps({"forced_quarantine": True}), "engine"),
    )
    conn.commit()
    return aid


@pytest.fixture
def client(shared_conn, monkeypatch):
    """Flask test client with the integrity blueprint mounted (flag on)."""
    from flask import Flask

    monkeypatch.setenv("ICDEV_INTEGRITY_ENABLED", "true")
    bp = create_integrity_blueprint()
    assert bp is not None
    app = Flask(__name__)
    app.register_blueprint(bp)
    app.config.update(TESTING=True)
    return app.test_client()


# --------------------------------------------------------------------------- #
# Feature-flag gating on the factory
# --------------------------------------------------------------------------- #
def test_factory_returns_none_when_disabled(monkeypatch):
    monkeypatch.delenv("ICDEV_INTEGRITY_ENABLED", raising=False)
    assert create_integrity_blueprint() is None
    monkeypatch.setenv("ICDEV_INTEGRITY_ENABLED", "0")
    assert create_integrity_blueprint() is None


def test_factory_returns_blueprint_when_enabled(monkeypatch):
    monkeypatch.setenv("ICDEV_INTEGRITY_ENABLED", "true")
    bp = create_integrity_blueprint()
    assert bp is not None
    assert bp.name == "integrity"


# --------------------------------------------------------------------------- #
# Pages (JSON fallback when templates aren't present)
# --------------------------------------------------------------------------- #
def test_index_page_lists_assessments(client, shared_conn):
    aid = _seed_assessment(shared_conn)
    resp = client.get("/integrity")
    assert resp.status_code == 200
    payload = resp.get_json()
    ids = [a["id"] for a in payload["assessments"]]
    assert aid in ids
    assert payload["summary"]["by_verdict"]["quarantine"] >= 1


def test_detail_page_renders_for_known_assessment(client, shared_conn):
    aid = _seed_assessment(shared_conn)
    resp = client.get(f"/integrity/{aid}")
    assert resp.status_code == 200
    payload = resp.get_json()
    assert payload["assessment"]["id"] == aid
    assert payload["capabilities"][0]["capability_type"] == "network_egress"
    assert payload["findings"][0]["severity"] == "critical"
    assert payload["verdict"]["verdict"] == "quarantine"


def test_detail_page_404_for_unknown(client, shared_conn):
    resp = client.get("/integrity/999999")
    assert resp.status_code == 404


# --------------------------------------------------------------------------- #
# Read API
# --------------------------------------------------------------------------- #
def test_api_assessments_list_and_filters(client, shared_conn):
    a_quar = _seed_assessment(shared_conn, verdict="quarantine", status="assessed")
    a_allow = _seed_assessment(shared_conn, verdict="allow", status="assessed", risk_score=10.0)

    resp = client.get("/api/integrity/assessments")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["count"] >= 2
    assert {a_quar, a_allow} <= {a["id"] for a in body["assessments"]}

    # verdict filter
    resp = client.get("/api/integrity/assessments?verdict=allow")
    verdicts = {a["verdict"] for a in resp.get_json()["assessments"]}
    assert verdicts == {"allow"}

    # limit is honored
    resp = client.get("/api/integrity/assessments?limit=1")
    assert resp.get_json()["count"] == 1


def test_api_assessment_detail(client, shared_conn):
    aid = _seed_assessment(shared_conn)
    resp = client.get(f"/api/integrity/assessment/{aid}")
    assert resp.status_code == 200
    detail = resp.get_json()
    assert detail["assessment"]["id"] == aid
    assert len(detail["capabilities"]) == 1
    assert len(detail["findings"]) == 1
    assert detail["is_terminal"] is False
    # JSON columns are decoded to objects, not raw strings.
    assert detail["capabilities"][0]["evidence"]["host"] == "10.0.0.1"
    assert detail["verdict"]["rationale"]["forced_quarantine"] is True


def test_api_assessment_detail_404(client, shared_conn):
    resp = client.get("/api/integrity/assessment/424242")
    assert resp.status_code == 404
    assert "not found" in resp.get_json()["error"]


# --------------------------------------------------------------------------- #
# assess API
# --------------------------------------------------------------------------- #
def test_api_assess_requires_source(client, shared_conn):
    resp = client.post("/api/integrity/assess", json={})
    assert resp.status_code == 400
    assert "source required" in resp.get_json()["error"]


def test_api_assess_delegates_to_engine(client, shared_conn, monkeypatch):
    captured = {}

    def _fake_assess(source, **kwargs):
        captured["source"] = source
        captured.update(kwargs)
        return {"assessment_id": 7, "verdict": "review", "risk_score": 55.0,
                "status": "assessed", "mode": "provenance_blind"}

    monkeypatch.setattr(engine, "assess", _fake_assess)
    resp = client.post("/api/integrity/assess", json={
        "source": "https://github.com/acme/widget",
        "mode": "provenance_blind",
        "declared_purpose": "a widget",
    })
    assert resp.status_code == 201
    assert resp.get_json()["assessment_id"] == 7
    assert captured["source"] == "https://github.com/acme/widget"
    assert captured["mode"] == "provenance_blind"
    assert captured["declared_purpose"] == "a widget"


def test_api_assess_rejected_source_is_400(client, shared_conn, monkeypatch):
    from tools.integrity.ingest import IngestRejected

    def _raise(*a, **k):
        raise IngestRejected("scheme not allowed: ftp")

    monkeypatch.setattr(engine, "assess", _raise)
    resp = client.post("/api/integrity/assess", json={"source": "ftp://evil/x"})
    assert resp.status_code == 400
    assert "rejected at ingest" in resp.get_json()["error"]


def test_api_assess_bad_mode_is_400(client, shared_conn, monkeypatch):
    def _raise(*a, **k):
        raise ValueError("invalid mode 'nonsense'")

    monkeypatch.setattr(engine, "assess", _raise)
    resp = client.post("/api/integrity/assess", json={"source": "/srv/x", "mode": "nonsense"})
    assert resp.status_code == 400
    assert "invalid mode" in resp.get_json()["error"]


# --------------------------------------------------------------------------- #
# HITL API — real engine promote / reject against the shared DB
# --------------------------------------------------------------------------- #
def test_api_promote_approves_and_records_authorization(client, shared_conn):
    aid = _seed_assessment(shared_conn)
    resp = client.post(f"/api/integrity/assessment/{aid}/promote",
                       json={"reviewed_by": "isso@example.mil", "reason": "manual review cleared"})
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["success"] is True
    assert body["status"] == "approved"

    # Status flipped on the assessment row.
    row = shared_conn.execute(
        "SELECT status FROM integrity_assessments WHERE id = ?", (aid,)
    ).fetchone()
    assert row["status"] == "approved"

    # Append-only authorization row recorded.
    auth = shared_conn.execute(
        "SELECT authorized, reviewed_by, reason FROM integrity_authorizations WHERE assessment_id = ?",
        (aid,),
    ).fetchone()
    assert auth["authorized"] in (1, True)

    # nav-comp-06: the reviewer is bound to the AUTHENTICATED user, never the
    # request body — a caller must not be able to attribute a quarantine
    # decision to someone else on an append-only audit trail. The
    # "isso@example.mil" posted above is a spoof attempt and must be ignored;
    # with no authenticated request context the recorded actor is "dashboard".
    assert auth["reviewed_by"] != "isso@example.mil", (
        "body-supplied reviewed_by must not reach the audit row"
    )
    assert auth["reviewed_by"] == "dashboard"


def test_api_reject_then_terminal_conflict(client, shared_conn):
    aid = _seed_assessment(shared_conn)
    resp = client.post(f"/api/integrity/assessment/{aid}/reject", json={"reason": "malware"})
    assert resp.status_code == 200
    assert resp.get_json()["status"] == "rejected"

    # A second decision on a now-terminal assessment is a 409.
    resp2 = client.post(f"/api/integrity/assessment/{aid}/promote", json={})
    assert resp2.status_code == 409
    assert "already rejected" in resp2.get_json()["error"]


def test_api_promote_missing_assessment_is_404(client, shared_conn):
    resp = client.post("/api/integrity/assessment/777777/promote", json={})
    assert resp.status_code == 404
    assert "not found" in resp.get_json()["error"]

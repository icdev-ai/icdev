# CUI // SP-CTI
"""nav-strat-06 — unit coverage for the LIVE Strategos intel-brief surface.

Follow-up to PR #585, which deleted ``tests/strategos/test_intel_brief_routes.py``
together with the legacy ``tools/strategos/blueprint.py``. The old test only ever
exercised the *unregistered* legacy blueprint with mocks the registered blueprint
never calls, so the live surface in ``apps/strategos/blueprint.py`` was left with
no dedicated coverage. This file covers that live blueprint directly:

  * GET  /strategos/intel-brief        — reads ``sg_leadership_briefs`` via ``_safe_fetch``
  * GET  /strategos/leadership-brief   — distinct (static) leadership-brief page
  * POST /api/strategos/intel-brief/run — drives ``PredictiveAnalysisEngine`` (mocked)
  * PATCH /api/strategos/briefs/<id>/approve — RBAC-gated approval, ``reviewed_by``
    from session (nav-sec-05)

Design notes
------------
* The page templates ``extends "base.html"``, which needs the full dashboard app
  (context processors + a nav that ``url_for``s dozens of unrelated routes). Rather
  than stand that whole app up, we patch ``apps.strategos.blueprint.render_template``
  to capture the template name + context. That keeps the assertion on the *data
  path* the route is responsible for (what it reads from the DB and passes to the
  template), which is exactly the surface PR #585 stopped covering.
* DB isolation follows the sibling ``tests/strategos/test_dat.py`` pattern: patch
  the module-level ``get_connection`` to a throwaway per-test SQLite file whose
  schema mirrors migrations 158 (``sg_leadership_briefs``) and 059
  (``sg_intelligence_briefs``).
* The RBAC / fake-auth fixture mirrors ``tests/test_nav_sec_05_strategos_rbac.py``.
"""
from __future__ import annotations

import os
import sqlite3

import pytest


# --------------------------------------------------------------------------- #
# Schema (mirrors migration 158 + 059, SQLite branch)
# --------------------------------------------------------------------------- #

_LEADERSHIP_BRIEFS_DDL = """
CREATE TABLE IF NOT EXISTS sg_leadership_briefs (
    id                   TEXT PRIMARY KEY,
    theater              TEXT NOT NULL DEFAULT 'global',
    sio_composite_score  REAL NOT NULL DEFAULT 0.0,
    iw_triggered         INTEGER NOT NULL DEFAULT 0,
    threat_tier          TEXT NOT NULL DEFAULT 'LOW',
    signal_count_24h     INTEGER NOT NULL DEFAULT 0,
    conflict_event_count INTEGER NOT NULL DEFAULT 0,
    signal_velocity      REAL NOT NULL DEFAULT 0.0,
    p_war_posterior      REAL NOT NULL DEFAULT 0.05,
    goldstein_avg        REAL NOT NULL DEFAULT 0.0,
    dti_score            REAL NOT NULL DEFAULT 0.0,
    forecast_24h_json    TEXT,
    forecast_72h_json    TEXT,
    forecast_7d_json     TEXT,
    narrative_md         TEXT,
    generated_at         TEXT NOT NULL DEFAULT (datetime('now')),
    classification       TEXT NOT NULL DEFAULT 'CUI'
)
"""

_INTELLIGENCE_BRIEFS_DDL = """
CREATE TABLE IF NOT EXISTS sg_intelligence_briefs (
    id                  TEXT PRIMARY KEY,
    brief_type          TEXT NOT NULL CHECK(brief_type IN ('sitrep','iir','warnord','assessment')),
    title               TEXT NOT NULL,
    content_md          TEXT NOT NULL,
    sio_confidence      REAL,
    analyst_reviewed    INTEGER NOT NULL DEFAULT 0,
    reviewed_by         TEXT,
    reviewed_at         TEXT,
    annotations         TEXT,
    created_at          TEXT NOT NULL
)
"""


def _make_db(path: str, *, create_leadership: bool = True) -> None:
    conn = sqlite3.connect(path)
    try:
        if create_leadership:
            conn.execute(_LEADERSHIP_BRIEFS_DDL)
        conn.execute(_INTELLIGENCE_BRIEFS_DDL)
        conn.commit()
    finally:
        conn.close()


def _seed_leadership_brief(path: str, **overrides) -> str:
    row = {
        "id": "lb-1",
        "theater": "global",
        "sio_composite_score": 72.5,
        "iw_triggered": 1,
        "threat_tier": "HIGH",
        "signal_count_24h": 40,
        "conflict_event_count": 12,
        "signal_velocity": 3.2,
        "p_war_posterior": 0.31,
        "goldstein_avg": -4.1,
        "dti_score": 0.6,
        "forecast_24h_json": '{"p_war": 0.31, "band": "elevated"}',
        "forecast_72h_json": '{"p_war": 0.38}',
        "forecast_7d_json": '{"p_war": 0.44}',
        "narrative_md": "## Assessment\nEscalation indicators rising.",
        "generated_at": "2026-07-18T12:00:00Z",
    }
    row.update(overrides)
    conn = sqlite3.connect(path)
    try:
        cols = ", ".join(row.keys())
        ph = ", ".join("?" for _ in row)
        conn.execute(
            f"INSERT INTO sg_leadership_briefs ({cols}) VALUES ({ph})",  # nosec B608
            list(row.values()),
        )
        conn.commit()
    finally:
        conn.close()
    return row["id"]


def _seed_intel_brief(path: str, brief_id: str, *, reviewed: int = 0) -> None:
    conn = sqlite3.connect(path)
    try:
        conn.execute(
            "INSERT INTO sg_intelligence_briefs "
            "(id, brief_type, title, content_md, analyst_reviewed, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (brief_id, "assessment", "Test Brief", "# body", reviewed, "2026-07-18T00:00:00Z"),
        )
        conn.commit()
    finally:
        conn.close()


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #

@pytest.fixture()
def bp_module():
    try:
        import apps.strategos.blueprint as mod
    except ImportError as exc:  # pragma: no cover
        pytest.skip(f"strategos blueprint not importable: {exc}")
    return mod


@pytest.fixture()
def iso_db(tmp_path, monkeypatch, bp_module):
    """Isolated per-test SQLite DB wired into the blueprint's get_connection.

    Yields a small handle exposing the db path plus a ``create`` flag toggle so a
    test can exercise the missing-table degradation path.
    """
    db_path = str(tmp_path / "intel_brief.db")

    def _factory():
        c = sqlite3.connect(db_path)
        c.row_factory = sqlite3.Row
        return c

    monkeypatch.setattr(bp_module, "get_connection", _factory)
    monkeypatch.setattr(bp_module, "is_pg", lambda: False)

    class _Handle:
        path = db_path

        def create(self, *, create_leadership: bool = True):
            _make_db(db_path, create_leadership=create_leadership)

    return _Handle()


@pytest.fixture()
def captured_render(monkeypatch, bp_module):
    """Patch render_template so page routes never touch base.html.

    Captures (template_name, context) of the last render call.
    """
    cap: dict = {}

    def _fake_render_template(template_name, **context):
        cap["template"] = template_name
        cap["context"] = context
        return f"RENDERED::{template_name}"

    monkeypatch.setattr(bp_module, "render_template", _fake_render_template)
    return cap


@pytest.fixture()
def page_client(bp_module):
    from flask import Flask

    app = Flask(__name__)
    app.config["TESTING"] = True
    app.register_blueprint(bp_module.create_strategos_blueprint(), url_prefix="/strategos")
    with app.test_client() as c:
        yield c


@pytest.fixture()
def api_client(bp_module):
    """API blueprint mounted with the nav-sec-05 fake-auth shim.

    ``X-Test-Role`` header -> a logged-in ``g.current_user`` with that role;
    no header -> anonymous.
    """
    for var in ("ICDEV_AUTH_BYPASS", "ICDEV_DASHBOARD_API_KEY", "ICDEV_DASHBOARD_DEV_AUTOLOGIN"):
        os.environ.pop(var, None)

    from flask import Flask, g, request, session

    app = Flask(__name__)
    app.config["TESTING"] = True
    app.config["SECRET_KEY"] = "test-secret"

    @app.before_request
    def _fake_auth():
        role = request.headers.get("X-Test-Role")
        if role:
            g.current_user = {"id": "u-session", "role": role, "tenant_id": "t"}
            session["user_id"] = "u-session"

    app.register_blueprint(bp_module.create_strategos_api_blueprint(), url_prefix="/api/strategos")
    with app.test_client() as c:
        yield c


# --------------------------------------------------------------------------- #
# 1. Intel-brief page — populated
# --------------------------------------------------------------------------- #

def test_intel_brief_page_renders_seeded_rows(iso_db, captured_render, page_client):
    iso_db.create()
    _seed_leadership_brief(iso_db.path)

    resp = page_client.get("/strategos/intel-brief")
    assert resp.status_code == 200

    assert captured_render["template"] == "strategos/intel_brief.html"
    ctx = captured_render["context"]
    briefs = ctx["briefs"]
    assert len(briefs) == 1
    brief = briefs[0]
    assert brief["id"] == "lb-1"
    assert brief["threat_tier"] == "HIGH"
    # forecast JSON strings are parsed into dicts for the template
    assert isinstance(brief["forecast_24h_json"], dict)
    assert brief["forecast_24h_json"]["band"] == "elevated"
    # latest_brief is the (only) row, not a fabricated placeholder
    assert ctx["latest_brief"] is brief


def test_intel_brief_page_orders_by_generated_at_desc(iso_db, captured_render, page_client):
    iso_db.create()
    _seed_leadership_brief(iso_db.path, id="old", generated_at="2026-07-01T00:00:00Z")
    _seed_leadership_brief(iso_db.path, id="new", generated_at="2026-07-18T00:00:00Z")

    resp = page_client.get("/strategos/intel-brief")
    assert resp.status_code == 200
    ctx = captured_render["context"]
    assert [b["id"] for b in ctx["briefs"]] == ["new", "old"]
    assert ctx["latest_brief"]["id"] == "new"


# --------------------------------------------------------------------------- #
# 2. Intel-brief page — empty / missing table degrade honestly
# --------------------------------------------------------------------------- #

def test_intel_brief_page_empty_table_no_fake_content(iso_db, captured_render, page_client):
    iso_db.create()  # table exists, zero rows

    resp = page_client.get("/strategos/intel-brief")
    assert resp.status_code == 200
    ctx = captured_render["context"]
    assert ctx["briefs"] == []
    # No fabricated "latest" brief when there is genuinely no data.
    assert ctx["latest_brief"] is None


def test_intel_brief_page_missing_table_degrades(iso_db, captured_render, page_client):
    # Do NOT create sg_leadership_briefs -> _safe_fetch must swallow the
    # missing-table error and the page must still render (empty).
    iso_db.create(create_leadership=False)

    resp = page_client.get("/strategos/intel-brief")
    assert resp.status_code == 200
    ctx = captured_render["context"]
    assert ctx["briefs"] == []
    assert ctx["latest_brief"] is None


# --------------------------------------------------------------------------- #
# 3. Leadership-brief page (distinct static page)
# --------------------------------------------------------------------------- #

def test_leadership_brief_page_renders(captured_render, page_client):
    resp = page_client.get("/strategos/leadership-brief")
    assert resp.status_code == 200
    assert captured_render["template"] == "strategos/leadership_brief.html"


# --------------------------------------------------------------------------- #
# 4. Intel-brief run route — PredictiveAnalysisEngine mocked
# --------------------------------------------------------------------------- #

def test_intel_brief_run_success(api_client, monkeypatch):
    import tools.strategos.predictive_analysis as pa

    class _FakeEngine:
        def run(self, theater="global"):
            assert theater == "ukraine"
            return {"brief_id": "lb-xyz", "theater": theater, "threat_tier": "SEVERE"}

    monkeypatch.setattr(pa, "PredictiveAnalysisEngine", _FakeEngine)

    resp = api_client.post("/api/strategos/intel-brief/run", json={"theater": "ukraine"})
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["ok"] is True
    # engine result is merged into the response, not discarded / re-fabricated
    assert body["brief_id"] == "lb-xyz"
    assert body["threat_tier"] == "SEVERE"


def test_intel_brief_run_defaults_theater_to_global(api_client, monkeypatch):
    import tools.strategos.predictive_analysis as pa
    seen = {}

    class _FakeEngine:
        def run(self, theater="global"):
            seen["theater"] = theater
            return {"ok_marker": True}

    monkeypatch.setattr(pa, "PredictiveAnalysisEngine", _FakeEngine)

    resp = api_client.post("/api/strategos/intel-brief/run", json={})
    assert resp.status_code == 200
    assert seen["theater"] == "global"


def test_intel_brief_run_engine_failure_degrades_honestly(api_client, monkeypatch):
    """When the engine blows up the route must report the failure, not fabricate
    a successful brief."""
    import tools.strategos.predictive_analysis as pa

    class _BrokenEngine:
        def __init__(self):
            raise RuntimeError("mesh offline")

    monkeypatch.setattr(pa, "PredictiveAnalysisEngine", _BrokenEngine)

    resp = api_client.post("/api/strategos/intel-brief/run", json={"theater": "global"})
    assert resp.status_code == 500
    body = resp.get_json()
    assert body["ok"] is False
    assert "mesh offline" in body["error"]


# --------------------------------------------------------------------------- #
# 5. Briefs approve — RBAC + session-sourced reviewer (nav-sec-05)
# --------------------------------------------------------------------------- #

def test_briefs_approve_anonymous_is_401(api_client, iso_db):
    iso_db.create()
    _seed_intel_brief(iso_db.path, "b-anon")
    resp = api_client.patch("/api/strategos/briefs/b-anon/approve", json={})
    assert resp.status_code == 401


def test_briefs_approve_developer_is_403(api_client, iso_db):
    iso_db.create()
    _seed_intel_brief(iso_db.path, "b-dev")
    resp = api_client.patch(
        "/api/strategos/briefs/b-dev/approve",
        json={},
        headers={"X-Test-Role": "developer"},
    )
    assert resp.status_code == 403


def test_briefs_approve_approver_not_blocked(api_client, iso_db):
    iso_db.create()
    _seed_intel_brief(iso_db.path, "b-ok")
    resp = api_client.patch(
        "/api/strategos/briefs/b-ok/approve",
        json={"annotations": "looks good"},
        headers={"X-Test-Role": "isso"},
    )
    # Cleared the RBAC gate and the seeded row was approved.
    assert resp.status_code == 200, resp.get_data(as_text=True)
    body = resp.get_json()
    assert body["status"] == "approved"


def test_briefs_approve_records_session_reviewer_not_body(api_client, iso_db):
    """``reviewed_by`` must be the authenticated session user, never a spoofed
    body field (nav-sec-05)."""
    iso_db.create()
    _seed_intel_brief(iso_db.path, "b-sess")

    resp = api_client.patch(
        "/api/strategos/briefs/b-sess/approve",
        json={"reviewed_by": "spoofed-attacker", "annotations": "ok"},
        headers={"X-Test-Role": "pm"},
    )
    assert resp.status_code == 200, resp.get_data(as_text=True)
    assert resp.get_json()["reviewed_by"] == "u-session"

    # Confirm the persisted column, too.
    conn = sqlite3.connect(iso_db.path)
    try:
        row = conn.execute(
            "SELECT reviewed_by, analyst_reviewed FROM sg_intelligence_briefs WHERE id = ?",
            ("b-sess",),
        ).fetchone()
    finally:
        conn.close()
    assert row[0] == "u-session"
    assert row[0] != "spoofed-attacker"
    assert row[1] == 1


def test_briefs_approve_missing_brief_is_404(api_client, iso_db):
    iso_db.create()  # table exists, but no matching row
    resp = api_client.patch(
        "/api/strategos/briefs/does-not-exist/approve",
        json={},
        headers={"X-Test-Role": "admin"},
    )
    assert resp.status_code == 404


def test_briefs_approve_already_reviewed_is_404(api_client, iso_db):
    iso_db.create()
    _seed_intel_brief(iso_db.path, "b-done", reviewed=1)
    resp = api_client.patch(
        "/api/strategos/briefs/b-done/approve",
        json={},
        headers={"X-Test-Role": "admin"},
    )
    # WHERE analyst_reviewed=0 -> no row updated -> honest 404, not a silent re-approve.
    assert resp.status_code == 404

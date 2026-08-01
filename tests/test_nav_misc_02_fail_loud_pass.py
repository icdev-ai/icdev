# CUI // SP-CTI
"""nav-misc-02 — Misc dashboard pages/APIs fail loud, not silent (P2).

``tools/dashboard/app.py`` contained ~200 ``except Exception: pass`` (or
``return []`` / canned-zero) blocks. Several page-level stats/list endpoints
rendered a DB/subsystem outage as a *healthy-but-empty* page, so an operator
could not tell "no data" apart from "the pipeline is down".

This suite exercises a representative sample of the endpoints converted to the
degraded-state pattern (see ``docs/dev/degraded-state-pattern.md``):

  Page routes (``degraded=True`` context + ``includes/_degraded_banner.html``):
    * ``/simulation``      — Digital-Twin scenario read failure
    * ``/knowledge-graph`` — KG store read failure

  JSON APIs (log + explicit ``{"error": true, "detail": ...}`` payload):
    * ``/api/charts/translations`` — chart query failure
    * ``/api/license/tier``        — registry enumeration failure

For each, a mocked backend failure must (1) surface an explicit degraded signal
and (2) emit a logged warning; the healthy path must stay unchanged (no banner /
no error flag / no warning logged).

Maintainer notes mirror nav-bld-04:
  * ``icdev_logger`` sets ``propagate = False``; we attach a capture handler
    directly to the ``icdev.dashboard`` logger (caplog cannot see it).
  * ``tools.*`` is a shim over ``icdev.tools.*``; monkeypatch targets resolve
    through ``importlib.import_module`` per the shim-aware convention.
"""
from __future__ import annotations

import importlib
import logging
import os

import pytest


# ---------------------------------------------------------------------------
# Capture handler — icdev_logger has propagate=False so caplog is blind.
# ---------------------------------------------------------------------------

class _CaptureHandler(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:  # noqa: D401
        self.records.append(record)


@pytest.fixture
def dashboard_logs():
    logger = logging.getLogger("icdev.dashboard")
    handler = _CaptureHandler()
    logger.addHandler(handler)
    prev_level = logger.level
    logger.setLevel(logging.WARNING)
    try:
        yield handler
    finally:
        logger.removeHandler(handler)
        logger.setLevel(prev_level)


@pytest.fixture(scope="module")
def client():
    os.environ.setdefault("ICDEV_STORAGE_BACKEND", "sqlite")
    os.environ["ICDEV_AUTH_BYPASS"] = "1"
    os.environ["ICDEV_CANVAS_ACCESS_OPEN"] = "true"
    from tools.dashboard.app import create_app

    app = create_app(testing=True)
    app.config["TESTING"] = True
    app.config["PROPAGATE_EXCEPTIONS"] = False
    return app.test_client()


@pytest.fixture(autouse=True)
def _login(client, monkeypatch):
    """Authenticate as an active admin for every request in this module."""
    admin = {
        "id": "test-admin",
        "status": "active",
        "role": "admin",
        "tenant_id": "t",
        "email": "admin@test.gov",
        "clearance_level": "CUI",
    }
    auth_mod = importlib.import_module("tools.dashboard.auth")
    monkeypatch.setattr(auth_mod, "get_user_by_id", lambda *a, **k: dict(admin))
    with client.session_transaction() as sess:
        sess["user_id"] = "test-admin"
    yield


# ---------------------------------------------------------------------------
# Fake connections
# ---------------------------------------------------------------------------

class _FakeCursor:
    def fetchone(self):
        return (0, 0, 0)

    def fetchall(self):
        return []


class _HealthyConn:
    """Minimal healthy connection: every count is 0, every list is empty."""

    def execute(self, *_a, **_k):
        return _FakeCursor()

    def set_security_context(self, *_a, **_k):
        pass

    def close(self):
        pass


class _BoomExecConn:
    """Opens fine, but every query raises — models a mid-request DB fault."""

    def execute(self, *_a, **_k):
        raise RuntimeError("simulated query failure")

    def set_security_context(self, *_a, **_k):
        pass

    def close(self):
        pass


def _messages(handler: _CaptureHandler) -> str:
    return "\n".join(r.getMessage() for r in handler.records)


def _boom(*_a, **_k):
    raise RuntimeError("simulated DB outage")


# ===========================================================================
# 1. /simulation — page banner (degraded + healthy)
# ===========================================================================

def test_simulation_db_outage_renders_banner_and_logs(client, dashboard_logs, monkeypatch):
    app_mod = importlib.import_module("tools.dashboard.app")
    monkeypatch.setattr(app_mod, "get_connection", _boom)

    resp = client.get("/simulation")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "temporarily unavailable" in html.lower()
    assert 'role="alert"' in html

    msgs = _messages(dashboard_logs)
    assert "simulation_page" in msgs


def test_simulation_healthy_has_no_banner(client, dashboard_logs, monkeypatch):
    app_mod = importlib.import_module("tools.dashboard.app")
    monkeypatch.setattr(app_mod, "get_connection", lambda *a, **k: _HealthyConn())

    resp = client.get("/simulation")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "temporarily unavailable" not in html.lower()
    assert 'role="alert"' not in html
    assert dashboard_logs.records == []


# ===========================================================================
# 3. /knowledge-graph — page banner
# ===========================================================================

def test_knowledge_graph_db_outage_renders_banner_and_logs(client, dashboard_logs, monkeypatch):
    app_mod = importlib.import_module("tools.dashboard.app")
    monkeypatch.setattr(app_mod, "get_connection", _boom)

    resp = client.get("/knowledge-graph")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "temporarily unavailable" in html.lower()
    assert 'role="alert"' in html

    msgs = _messages(dashboard_logs)
    assert "knowledge_graph_page" in msgs


# ===========================================================================
# 4. /api/charts/translations — JSON degraded (degraded + healthy)
# ===========================================================================

def test_charts_translations_query_failure_is_explicit(client, dashboard_logs, monkeypatch):
    app_mod = importlib.import_module("tools.dashboard.app")
    monkeypatch.setattr(app_mod, "get_connection", lambda *a, **k: _BoomExecConn())

    resp = client.get("/api/charts/translations")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["error"] is True
    assert body["detail"]
    # Shape stays UI-compatible on the degraded path.
    assert body["status_distribution"] == {}
    assert body["language_pair_frequency"] == {}

    msgs = _messages(dashboard_logs)
    assert "api_charts_translations" in msgs


def test_charts_translations_healthy_has_no_error_flag(client, dashboard_logs, monkeypatch):
    app_mod = importlib.import_module("tools.dashboard.app")
    monkeypatch.setattr(app_mod, "get_connection", lambda *a, **k: _HealthyConn())

    resp = client.get("/api/charts/translations")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body.get("error") is None
    assert "status_distribution" in body
    assert dashboard_logs.records == []


# ===========================================================================
# 5. /api/license/tier — JSON degraded
# ===========================================================================

def test_license_tier_registry_failure_is_explicit(client, dashboard_logs, monkeypatch):
    app_mod = importlib.import_module("tools.dashboard.app")

    class _BoomRegistry:
        def iter_canvases(self):
            raise RuntimeError("registry down")

    monkeypatch.setattr(app_mod, "_REGISTRY", _BoomRegistry())

    resp = client.get("/api/license/tier")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["error"] is True
    assert body["detail"]
    # Degraded payload still carries the shape the UI expects.
    assert body["available_features"] == []
    assert body["locked_features"] == []

    msgs = _messages(dashboard_logs)
    assert "api_license_tier" in msgs

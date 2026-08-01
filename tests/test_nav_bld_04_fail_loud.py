# CUI // SP-CTI
"""nav-bld-04 — Build-menu pages fail loud, not silent (P2).

Two fail-silent DB handlers in ``tools/dashboard/app.py`` used to render a DB
outage as a *healthy* empty page:

  1. ``/finetune`` overview wrapped every stat query in ``except Exception: pass``
     and returned all-zero stats — an outage looked like an empty-but-healthy
     pipeline. It now logs the exception and passes ``error=True`` to the
     template, which renders a visible degraded-state banner.

  2. ``/api/connector-forge/list`` swallowed exceptions and returned
     ``{"connectors": [], "total": 0}`` (HTTP 200) — a silent empty on failure.
     It now logs and returns an explicit degraded payload
     (``{"error": true, "detail": ...}``) with HTTP 503; the template shows a
     warning banner instead of "No connectors generated yet".

The healthy paths are asserted unchanged (200, no banner, no error flag).

Notes for maintainers:
  * ``icdev_logger`` sets ``propagate = False``, so pytest's ``caplog`` (whose
    handler hangs on the root logger) cannot see these records. We attach a
    capture handler directly to the ``icdev.dashboard`` logger instead.
  * The full ``create_app`` factory enforces auth; we log a fake admin in by
    patching ``get_user_by_id`` and seeding the session cookie.
  * ``tools.*`` is a shim over ``icdev.tools.*``; monkeypatch targets are
    resolved through ``importlib.import_module`` per the shim-aware convention.
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


class _FakeCursor:
    def fetchone(self):
        return (0,)

    def fetchall(self):
        return []


class _FakeConn:
    """Minimal healthy connection: every count is 0, every list is empty."""

    def execute(self, *_a, **_k):
        return _FakeCursor()

    def set_security_context(self, *_a, **_k):
        pass

    def close(self):
        pass


def _messages(handler: _CaptureHandler) -> str:
    return "\n".join(r.getMessage() for r in handler.records)


# ===========================================================================
# 1. /finetune — degraded vs healthy
# ===========================================================================

def test_finetune_db_outage_renders_visible_banner_and_logs(client, dashboard_logs, monkeypatch):
    """DB unreachable -> 200 page with an explicit degraded banner + logged warning."""
    def _boom(*_a, **_k):
        raise RuntimeError("simulated DB outage")

    # _get_db() resolves get_connection from the app module globals at call time.
    app_mod = importlib.import_module("tools.dashboard.app")
    monkeypatch.setattr(app_mod, "get_connection", _boom)

    resp = client.get("/finetune")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)

    # Visible, honest degradation — not a silent healthy-looking empty page.
    assert "temporarily unavailable" in html.lower()
    assert 'role="alert"' in html

    # The outage was logged, not swallowed.
    msgs = _messages(dashboard_logs)
    assert "finetune_overview_page" in msgs
    assert "simulated DB outage" in msgs


def test_finetune_healthy_has_no_error_banner(client, dashboard_logs, monkeypatch):
    """Healthy DB path is unchanged: 200, zeroed stats, and NO degraded banner."""
    app_mod = importlib.import_module("tools.dashboard.app")
    monkeypatch.setattr(app_mod, "get_connection", lambda *a, **k: _FakeConn())

    resp = client.get("/finetune")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)

    assert "temporarily unavailable" not in html.lower()
    assert 'role="alert"' not in html
    # No warning was logged on the healthy path.
    assert dashboard_logs.records == []


# ===========================================================================
# 2. /api/connector-forge/list — degraded vs healthy
# ===========================================================================

def test_connector_forge_list_failure_is_explicit_503(client, dashboard_logs, monkeypatch):
    """Registry failure -> HTTP 503 with an explicit error payload + logged warning."""
    def _boom():
        raise RuntimeError("registry down")

    reg_mod = importlib.import_module("tools.databridge.registry")
    monkeypatch.setattr(reg_mod, "list_registered", _boom)

    resp = client.get("/api/connector-forge/list")
    assert resp.status_code == 503
    body = resp.get_json()
    assert body["error"] is True
    assert body["detail"]
    # Degraded payload still shape-compatible for the UI.
    assert body["connectors"] == []
    assert body["total"] == 0

    msgs = _messages(dashboard_logs)
    assert "api_connector_forge_list" in msgs
    assert "registry down" in msgs


def test_connector_forge_list_healthy_has_no_error_flag(client, dashboard_logs, monkeypatch):
    """Healthy registry path is unchanged: 200, connectors listed, no error flag."""
    reg_mod = importlib.import_module("tools.databridge.registry")
    monkeypatch.setattr(
        reg_mod, "list_registered", lambda: ["stripe-payments", "github-api"]
    )

    resp = client.get("/api/connector-forge/list")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body.get("error") is None
    assert body["total"] == 2
    assert {c["name"] for c in body["connectors"]} == {"stripe-payments", "github-api"}
    assert dashboard_logs.records == []

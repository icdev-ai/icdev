# CUI // SP-CTI
"""nav-misc-05 — /evidence page renders instead of 500ing (P1).

``tools/dashboard/app.py::evidence_page`` imported a non-existent symbol as the
FIRST line of the handler::

    from tools.compliance.evidence_collector import (
        FRAMEWORK_EVIDENCE_MAP, _get_connection, _table_exists)

``_table_exists`` does not exist in ``evidence_collector`` (the real helper is
``table_exists`` from ``tools.db.storage``, re-exported into that module's
namespace). Because the import precedes the ``try``, ``/evidence`` returned HTTP
500 on *every* request on main.

This suite proves:
  * ``/evidence`` renders 200 for an authenticated user against the empty
    conftest schema (honest empty state, no degraded banner).
  * a mid-request DB fault now surfaces the shared degraded banner
    (``role="alert"``) and emits a logged warning, per the degraded-state
    pattern (``docs/dev/degraded-state-pattern.md``), instead of a silent page.

Maintainer notes mirror nav-misc-02:
  * ``icdev_logger`` sets ``propagate = False``; attach a capture handler to the
    ``icdev.dashboard`` logger directly (caplog cannot see it).
  * ``tools.*`` is a shim over ``icdev.tools.*``; monkeypatch targets resolve
    through ``importlib.import_module`` per the shim-aware convention.
"""
from __future__ import annotations

import importlib
import logging
import os

import pytest


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


def _messages(handler: _CaptureHandler) -> str:
    return "\n".join(r.getMessage() for r in handler.records)


def _boom(*_a, **_k):
    raise RuntimeError("simulated evidence DB outage")


# ===========================================================================
# 1. Healthy path — empty conftest schema renders 200 with no banner.
#    This is the direct regression guard: the import used to 500 here.
# ===========================================================================

def test_evidence_page_renders_200_empty_state(client, dashboard_logs):
    resp = client.get("/evidence")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    # Page shell rendered, not an error page.
    assert "Compliance Evidence Collection" in html
    # Honest empty state — no degraded banner, no warning logged.
    assert 'role="alert"' not in html
    assert dashboard_logs.records == []


# ===========================================================================
# 2. Degraded path — a mid-request DB fault surfaces the banner + logs.
# ===========================================================================

def test_evidence_page_db_outage_renders_banner_and_logs(client, dashboard_logs, monkeypatch):
    ec_mod = importlib.import_module("tools.compliance.evidence_collector")
    monkeypatch.setattr(ec_mod, "_get_connection", _boom)

    resp = client.get("/evidence")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert 'role="alert"' in html
    assert "temporarily unavailable" in html.lower()

    msgs = _messages(dashboard_logs)
    assert "evidence_page" in msgs

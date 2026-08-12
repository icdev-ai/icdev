# CUI // SP-CTI
"""ECR-ONB V&V: onboarding_state persistence + API."""
from __future__ import annotations

import sqlite3
import uuid
from pathlib import Path
from unittest.mock import patch

import pytest

# ---------------------------------------------------------------------------
# In-memory schema (mirrors migration 215)
# ---------------------------------------------------------------------------

_SCHEMA = """
CREATE TABLE IF NOT EXISTS user_preferences (
    user_id          TEXT PRIMARY KEY,
    tenant_id        TEXT NOT NULL DEFAULT 'default',
    onboarding_state TEXT NOT NULL DEFAULT '{}',
    updated_at       TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);
"""


def _make_db(tmp_path):
    conn = sqlite3.connect(str(tmp_path / "test_onb.db"))
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    conn.commit()
    return conn


def _make_cursor_wrapper(conn):
    """Return a context-manager that mimics get_canvas_connection().

    tools/auth/onboarding.py uses get_canvas_connection, not get_connection:
    user_preferences is a canvas table with no classification/tenant_id
    columns, so the global RLS predicate get_connection attaches would raise
    UndefinedColumn on every query (CLAUDE.md canvas rule). These tests kept
    patching the old name and every one of them errored at patch time with
    AttributeError before reaching an assertion.
    """
    class _CM:
        def __enter__(self):
            return conn

        def __exit__(self, *_):
            pass

    return _CM()


# ---------------------------------------------------------------------------
# Tests: module functions
# ---------------------------------------------------------------------------


def test_get_onboarding_state_defaults(tmp_path):
    """get_onboarding_state returns default structure for unknown user."""
    conn = _make_db(tmp_path)
    cm = _make_cursor_wrapper(conn)

    with patch("tools.auth.onboarding.get_canvas_connection", return_value=cm):
        import tools.auth.onboarding as mod
        state = mod.get_onboarding_state("user-unknown")

    assert state["completed"] is False
    assert state["current_step"] == 0
    assert state["steps_done"] == []
    assert state["llm_configured"] is False
    assert state["canvas_enabled"] is None
    assert state["first_iqe_run"] is False
    assert state["dismissed_at"] is None
    conn.close()


def test_state_persists(tmp_path):
    """Acceptance: state written by update_onboarding_state is readable back."""
    conn = _make_db(tmp_path)
    cm = _make_cursor_wrapper(conn)

    with patch("tools.auth.onboarding.get_canvas_connection", return_value=cm):
        import tools.auth.onboarding as mod

        uid = f"user-{uuid.uuid4()}"
        mod.update_onboarding_state(uid, current_step=2, llm_configured=True)
        retrieved = mod.get_onboarding_state(uid)

    assert retrieved["current_step"] == 2
    assert retrieved["llm_configured"] is True
    assert retrieved["completed"] is False
    conn.close()


def test_update_onboarding_state_merges(tmp_path):
    """update_onboarding_state merges fields without overwriting untouched ones."""
    conn = _make_db(tmp_path)
    cm = _make_cursor_wrapper(conn)

    with patch("tools.auth.onboarding.get_canvas_connection", return_value=cm):
        import tools.auth.onboarding as mod

        uid = f"user-{uuid.uuid4()}"
        mod.update_onboarding_state(uid, current_step=1, steps_done=["welcome"])
        mod.update_onboarding_state(uid, current_step=2, llm_configured=True)
        state = mod.get_onboarding_state(uid)

    assert state["current_step"] == 2
    assert state["steps_done"] == ["welcome"]
    assert state["llm_configured"] is True
    conn.close()


def test_update_ignores_unknown_keys(tmp_path):
    """update_onboarding_state silently drops keys not in the allowed schema."""
    conn = _make_db(tmp_path)
    cm = _make_cursor_wrapper(conn)

    with patch("tools.auth.onboarding.get_canvas_connection", return_value=cm):
        import tools.auth.onboarding as mod

        uid = f"user-{uuid.uuid4()}"
        result = mod.update_onboarding_state(uid, hacker_field="injected")

    assert "hacker_field" not in result
    conn.close()


def test_mark_onboarding_complete(tmp_path):
    """mark_onboarding_complete sets completed=True and records dismissed_at."""
    conn = _make_db(tmp_path)
    cm = _make_cursor_wrapper(conn)

    with patch("tools.auth.onboarding.get_canvas_connection", return_value=cm):
        import tools.auth.onboarding as mod

        uid = f"user-{uuid.uuid4()}"
        result = mod.mark_onboarding_complete(uid)

    assert result["completed"] is True
    assert result["dismissed_at"] is not None
    conn.close()


def test_completed_user_flag(tmp_path):
    """Completed users have completed=True in their persisted state."""
    conn = _make_db(tmp_path)
    cm = _make_cursor_wrapper(conn)

    with patch("tools.auth.onboarding.get_canvas_connection", return_value=cm):
        import tools.auth.onboarding as mod

        uid = f"user-{uuid.uuid4()}"
        mod.mark_onboarding_complete(uid)
        state = mod.get_onboarding_state(uid)

    assert state["completed"] is True
    conn.close()


def test_patch_updates_individual_fields(tmp_path):
    """PATCH semantics: only specified fields change; others stay."""
    conn = _make_db(tmp_path)
    cm = _make_cursor_wrapper(conn)

    with patch("tools.auth.onboarding.get_canvas_connection", return_value=cm):
        import tools.auth.onboarding as mod

        uid = f"user-{uuid.uuid4()}"
        mod.update_onboarding_state(uid, canvas_enabled="network")
        mod.update_onboarding_state(uid, first_iqe_run=True)
        state = mod.get_onboarding_state(uid)

    assert state["canvas_enabled"] == "network"
    assert state["first_iqe_run"] is True
    assert state["completed"] is False
    conn.close()


# ---------------------------------------------------------------------------
# Tests: Flask routes
# ---------------------------------------------------------------------------


@pytest.fixture()
def flask_app(tmp_path):
    """Minimal Flask app with onboarding blueprint and patched DB."""
    from flask import Flask
    from tools.auth.blueprint import onboarding_bp

    app = Flask("test_onb")
    app.secret_key = "test-secret"
    app.register_blueprint(onboarding_bp)

    conn = _make_db(tmp_path)
    cm = _make_cursor_wrapper(conn)

    with patch("tools.auth.onboarding.get_canvas_connection", return_value=cm):
        yield app, conn

    conn.close()


def test_route_get_returns_defaults(flask_app):
    """GET /api/onboarding/state returns default state for unauthenticated user."""
    app, _ = flask_app
    with app.test_client() as client:
        resp = client.get("/api/onboarding/state")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["completed"] is False
    assert "current_step" in data


def test_route_patch_updates_state(flask_app):
    """PATCH /api/onboarding/state updates individual fields."""
    app, _ = flask_app
    with app.test_client() as client:
        resp = client.patch(
            "/api/onboarding/state",
            json={"current_step": 3, "llm_configured": True},
        )
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["current_step"] == 3
    assert data["llm_configured"] is True


def test_route_patch_persists_for_get(tmp_path):
    """PATCH followed by GET returns the persisted value (same session)."""
    from flask import Flask
    from tools.auth.blueprint import onboarding_bp

    app = Flask("test_onb_persist")
    app.secret_key = "test-secret"
    app.register_blueprint(onboarding_bp)

    conn = _make_db(tmp_path)
    cm = _make_cursor_wrapper(conn)

    with patch("tools.auth.onboarding.get_canvas_connection", return_value=cm):
        with app.test_client() as client:
            with client.session_transaction() as sess:
                sess["user_id"] = "route-test-user"

            client.patch(
                "/api/onboarding/state",
                json={"steps_done": ["welcome", "llm"]},
            )
            resp = client.get("/api/onboarding/state")

    data = resp.get_json()
    assert data["steps_done"] == ["welcome", "llm"]
    conn.close()


# ---------------------------------------------------------------------------
# Tests: wizard template
# ---------------------------------------------------------------------------


def test_wizard_include_present():
    """Acceptance: _onboarding_wizard.html exists and base.html includes it."""

    base_dirs = [
        Path("tools/dashboard/templates"),
        Path("icdev/tools/dashboard/templates"),
    ]
    for base_dir in base_dirs:
        wizard = base_dir / "includes" / "_onboarding_wizard.html"
        assert wizard.exists(), f"Missing: {wizard}"

        base_html = base_dir / "base.html"
        assert base_html.exists(), f"Missing: {base_html}"
        content = base_html.read_text(encoding="utf-8")
        assert "_onboarding_wizard.html" in content, (
            f"{base_html} does not include _onboarding_wizard.html"
        )
        assert "body_end" in content, f"{base_html} missing {{% block body_end %}}"

    wizard_content = (base_dirs[0] / "includes" / "_onboarding_wizard.html").read_text(encoding="utf-8")
    assert "onb-backdrop" in wizard_content
    assert "onboarding/state" in wizard_content
    assert "onb-step-0" in wizard_content
    assert "onb-step-1" in wizard_content
    assert "onb-step-2" in wizard_content
    assert "onbSkip" in wizard_content
    assert "onbNext" in wizard_content
    assert "onbBack" in wizard_content


# ---------------------------------------------------------------------------
# Tests: onboarding.js static file + wizard API routes
# ---------------------------------------------------------------------------


def test_wizard_api_routes(flask_app):
    """Acceptance: onboarding API routes return expected shapes and state persists."""

    # 1. onboarding.js must exist in both static trees
    for js_path in [
        Path("tools/dashboard/static/js/onboarding.js"),
        Path("icdev/tools/dashboard/static/js/onboarding.js"),
    ]:
        assert js_path.exists(), f"Missing: {js_path}"
        src = js_path.read_text(encoding="utf-8")
        assert "ICDEV.Onboarding" in src, f"{js_path} missing ICDEV.Onboarding class"
        assert "init" in src
        assert "markComplete" in src
        assert "testLlmConnection" in src
        assert "enableCanvas" in src
        assert "runIqeExample" in src

    # 2. base.html files reference onboarding.js
    for base_path in [
        Path("tools/dashboard/templates/base.html"),
        Path("icdev/tools/dashboard/templates/base.html"),
    ]:
        content = base_path.read_text(encoding="utf-8")
        assert "onboarding.js" in content, f"{base_path} does not include onboarding.js"

    # 3. GET /api/onboarding/state returns 200 with required keys
    app, _ = flask_app
    with app.test_client() as client:
        resp = client.get("/api/onboarding/state")
        assert resp.status_code == 200
        data = resp.get_json()
        required_keys = {"completed", "current_step", "steps_done", "llm_configured",
                         "canvas_enabled", "first_iqe_run", "dismissed_at"}
        assert required_keys.issubset(data.keys()), f"Missing keys: {required_keys - data.keys()}"
        assert data["completed"] is False
        assert data["current_step"] == 0

        # 4. PATCH completed=true marks wizard done
        patch_resp = client.patch(
            "/api/onboarding/state",
            json={"completed": True},
        )
        assert patch_resp.status_code == 200
        patch_data = patch_resp.get_json()
        assert patch_data["completed"] is True

        # 5. Subsequent GET reflects persisted state (same session/anonymous user)
        get_resp = client.get("/api/onboarding/state")
        assert get_resp.status_code == 200
        assert get_resp.get_json()["completed"] is True

        # 6. PATCH with unknown key is silently ignored
        safe_resp = client.patch(
            "/api/onboarding/state",
            json={"__proto__": "injected", "current_step": 2},
        )
        assert safe_resp.status_code == 200
        safe_data = safe_resp.get_json()
        assert "__proto__" not in safe_data
        assert safe_data["current_step"] == 2

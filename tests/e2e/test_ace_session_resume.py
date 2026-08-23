# CUI // SP-CTI
"""E2E: ACE session resume — close tab, reopen, assert message count unchanged.

Covers:
  - GET /coworker/<id>/resume?token=<token>  returns conversation_history
  - Missing / bad token returns 400 / 404
  - Message count persists across simulated tab-close + reopen
  - Resume button present in rendered HTML when a session exists

Usage:
    pytest tests/e2e/test_ace_session_resume.py -v --noconftest
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import uuid
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

# Force SQLite for ACE DB in tests
os.environ["ICDEV_STORAGE_BACKEND"] = "sqlite"
os.environ.setdefault("ICDEV_ACE_DB_URL", "")

from icdev.tools.ace.db.init_db import SCHEMA  # noqa: E402


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #

@pytest.fixture()
def ace_db(tmp_path):
    """In-memory SQLite DB with the full ACE schema."""
    db_path = tmp_path / "ace_resume_e2e.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(SCHEMA)
    conn.commit()
    return conn, db_path


@pytest.fixture()
def instance_id(ace_db):
    conn, _ = ace_db
    iid = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO ace_instances (id, name, role_id) VALUES (?, ?, ?)",
        (iid, "resume-e2e-test", "ai_developer"),
    )
    conn.commit()
    return iid


@pytest.fixture()
def seeded_session(ace_db, instance_id):
    """Seed ace_sessions with 3 conversation turns; return (token, history)."""
    conn, _ = ace_db
    token = str(uuid.uuid4())
    sid = str(uuid.uuid4())
    history = [
        {"role": "user",      "content": "Hello ACE, start the task."},
        {"role": "assistant", "content": "Understood. Starting work now."},
        {"role": "user",      "content": "Please summarize progress."},
    ]
    conn.execute(
        """INSERT INTO ace_sessions
               (session_id, instance_id, conversation_history, resume_token, turn_count)
           VALUES (?, ?, ?, ?, ?)""",
        (sid, instance_id, json.dumps(history), token, len(history)),
    )
    conn.commit()
    return token, history


@pytest.fixture()
def seeded_messages(ace_db, instance_id):
    """Seed 2 ace_messages rows; return count."""
    conn, _ = ace_db
    for i in range(2):
        conn.execute(
            "INSERT INTO ace_messages (id, instance_id, message_type, role, content) "
            "VALUES (?, ?, ?, ?, ?)",
            (str(uuid.uuid4()), instance_id, "info", "assistant", f"Test message {i + 1}"),
        )
    conn.commit()
    return 2


@pytest.fixture()
def flask_app(ace_db, monkeypatch):
    """Flask test app with ACE blueprint; DB calls routed to the test DB."""
    conn, db_path = ace_db

    import icdev.tools.ace.blueprint as bp_mod

    def _fake_db():
        c = sqlite3.connect(str(db_path))
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA foreign_keys=ON")
        return c

    monkeypatch.setattr(bp_mod, "_db", _fake_db)

    # Prevent init_db from running (schema already set up in ace_db fixture).
    bp_mod._state["db_ready"] = True

    from flask import Flask
    from icdev.tools.ace.blueprint import ace_bp

    app = Flask(__name__)
    app.config["TESTING"] = True
    app.register_blueprint(ace_bp)
    return app


# --------------------------------------------------------------------------- #
# Tests: resume endpoint
# --------------------------------------------------------------------------- #

def test_resume_missing_token_returns_400(flask_app, instance_id):
    """GET /coworker/<id>/resume with no ?token= must return 400."""
    with flask_app.test_client() as client:
        r = client.get(f"/coworker/{instance_id}/resume")
    assert r.status_code == 400
    data = r.get_json()
    assert "token" in data.get("error", "")


def test_resume_wrong_token_returns_404(flask_app, instance_id):
    """GET /coworker/<id>/resume?token=bad must return 404."""
    with flask_app.test_client() as client:
        r = client.get(f"/coworker/{instance_id}/resume?token=nonexistent-token")
    assert r.status_code == 404
    data = r.get_json()
    assert "not found" in data.get("error", "")


def test_resume_token_mismatch_instance_returns_404(flask_app, ace_db, seeded_session):
    """Token from a different instance_id must not resolve."""
    token, _ = seeded_session
    other_id = str(uuid.uuid4())  # instance that doesn't own this token
    with flask_app.test_client() as client:
        r = client.get(f"/coworker/{other_id}/resume?token={token}")
    assert r.status_code == 404


def test_resume_valid_token_returns_history(flask_app, seeded_session, instance_id):
    """Valid token returns conversation_history with correct length and content."""
    token, expected = seeded_session
    with flask_app.test_client() as client:
        r = client.get(f"/coworker/{instance_id}/resume?token={token}")
    assert r.status_code == 200
    data = r.get_json()
    assert "conversation_history" in data
    history = data["conversation_history"]
    assert len(history) == len(expected)
    assert history[0]["role"] == "user"
    assert history[0]["content"] == "Hello ACE, start the task."
    assert history[1]["role"] == "assistant"
    assert data["turn_count"] == len(expected)


# --------------------------------------------------------------------------- #
# Tests: message persistence ("close tab, reopen")
# --------------------------------------------------------------------------- #

def test_message_count_persists_across_reload(flask_app, seeded_messages, instance_id):
    """Simulate close-tab + reopen: /api/ace/<id>/messages count must not drop.

    The ace_messages rows are persisted in DB; two separate requests to the
    messages endpoint must return the same count — equivalent to a user closing
    the browser tab and reopening /coworker/<id>.
    """
    with flask_app.test_client() as client:
        # First 'visit' — open tab
        r1 = client.get(f"/api/ace/{instance_id}/messages")
        assert r1.status_code == 200
        count1 = r1.get_json()["count"]
        assert count1 == seeded_messages

        # Second 'visit' — close tab + reopen
        r2 = client.get(f"/api/ace/{instance_id}/messages")
        assert r2.status_code == 200
        count2 = r2.get_json()["count"]

    assert count2 >= count1, f"Message count dropped on reload: {count1} → {count2}"
    assert count2 == seeded_messages


# --------------------------------------------------------------------------- #
# Tests: resume button appears in rendered HTML
# --------------------------------------------------------------------------- #

def _captured_render(monkeypatch):
    """Capture render_template's kwargs instead of rendering.

    This harness is a bare ``Flask()`` with no dashboard template folder, so
    ``coworker/instance.html`` (which extends ``base.html``) can NEVER render
    here -- and since qa-fail-5f7cf03a0b0a4351 a page route answers that with a
    500 HTML page instead of a JSON 200. The earlier version of these two tests
    hedged on exactly that fallback ("at minimum assert 200"), which is how the
    route passed for weeks while never sending ``resume_token`` at all. The
    rendered page itself is asserted in tests/test_ace_instance_page_render.py
    through the real dashboard app; here the contract is the DATA the route
    hands the template.
    """
    import icdev.tools.ace.blueprint as bp_mod

    captured: dict = {}

    def _fake_render(template, **ctx):
        captured["template"] = template
        captured.update(ctx)
        return "rendered"

    monkeypatch.setattr(bp_mod, "render_template", _fake_render)
    return captured


def test_instance_detail_passes_resume_token_when_session_exists(
    flask_app, seeded_session, seeded_messages, instance_id, monkeypatch
):
    """The route must hand the most recent session's resume_token to the template."""
    token, _ = seeded_session
    captured = _captured_render(monkeypatch)
    with flask_app.test_client() as client:
        r = client.get(f"/coworker/{instance_id}")
    assert r.status_code == 200
    assert captured["template"] == "coworker/instance.html"
    assert captured["resume_token"] == token


def test_instance_detail_passes_none_resume_token_without_session(flask_app, instance_id, monkeypatch):
    """No session -> resume_token is an explicit None (never an unset Jinja Undefined)."""
    captured = _captured_render(monkeypatch)
    with flask_app.test_client() as client:
        r = client.get(f"/coworker/{instance_id}")
    assert r.status_code == 200
    assert "resume_token" in captured
    assert captured["resume_token"] is None

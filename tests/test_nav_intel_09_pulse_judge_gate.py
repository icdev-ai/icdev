# CUI // SP-CTI
"""Judge-verdict publish gate for the Pulse blog (nav-intel-09, "block red").

The LLM judge maps each post to a FAR 15.305 color rating stored on
``pulse_posts.judge_color`` (blue/purple/green/yellow/red). Product decision: a
RED verdict — or no verdict at all (judge never ran / errored) — HARD-BLOCKS
publishing. GREEN/YELLOW (and the higher purple/blue) publish normally. An admin
may force-publish over the block with a documented reason, recorded in the
append-only ``pulse_publish_audit`` table.

Covered surfaces:
  * pure gate logic (``evaluate_publish_gate``);
  * the publish endpoint (409 on RED / absent; 403 on non-admin force; publishes
    on GREEN/YELLOW and on audited admin force → audit row written);
  * the batch / scheduler auto-publish path (``publish_all_approved`` skips RED
    and no-verdict posts, publishes cleared ones).

Harness mirrors tools/dashboard/auth.py::_auth_before_request (session user_id
-> g.current_user) over a Flask test client, with get_connection() patched to an
isolated SQLite DB (patched in both tools.* and icdev.tools.* module objects —
the shim gives them distinct identities for from-imports).
"""
from __future__ import annotations

import importlib
import sqlite3
from datetime import datetime, timezone

import pytest

# --- isolated pulse schema (subset of tools/pulse/db.py PULSE_SCHEMA) ---------
_SCHEMA = """
CREATE TABLE pulse_posts (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    slug TEXT UNIQUE NOT NULL,
    status TEXT NOT NULL DEFAULT 'draft',
    body_markdown TEXT,
    readability_score REAL,
    wp_post_id TEXT,
    judge_color TEXT,
    judge_composite REAL,
    judge_combined REAL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    published_at TEXT
);
CREATE TABLE pulse_publish_audit (
    id TEXT PRIMARY KEY,
    post_id TEXT NOT NULL,
    action TEXT NOT NULL,
    verdict TEXT,
    reviewer TEXT,
    reason TEXT NOT NULL,
    tenant_id TEXT,
    created_at TEXT NOT NULL
);
"""

_USERS = {
    "editor": {"id": "editor", "role": "reviewer", "tenant_id": "t1"},
    "admin": {"id": "admin", "role": "admin", "tenant_id": "t1"},
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _seed_post(db_path, post_id, judge_color, status="approved"):
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "INSERT INTO pulse_posts (id, title, slug, status, body_markdown, "
        "readability_score, judge_color, created_at, updated_at) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        (
            post_id,
            f"Title {post_id}",
            f"slug-{post_id}",
            status,
            "Body text.",
            80.0,
            judge_color,
            _now(),
            _now(),
        ),
    )
    conn.commit()
    conn.close()


@pytest.fixture
def db_path(tmp_path, monkeypatch):
    """Isolated SQLite pulse DB, patched into get_connection() across shims."""
    path = tmp_path / "pulse_gate.db"
    conn = sqlite3.connect(str(path))
    conn.executescript(_SCHEMA)
    conn.commit()
    conn.close()

    def _get_connection(*_a, **_kw):
        from tools.db.storage import StorageConnection

        raw = sqlite3.connect(str(path), check_same_thread=False)
        raw.row_factory = sqlite3.Row
        return StorageConnection(raw, "sqlite")

    for mod_name in ("tools.db.storage", "icdev.tools.db.storage"):
        try:
            mod = importlib.import_module(mod_name)
        except ImportError:
            continue
        monkeypatch.setattr(mod, "get_connection", _get_connection, raising=False)
    # tools.pulse.db imported get_connection by name at module load.
    for mod_name in ("tools.pulse.db", "icdev.tools.pulse.db"):
        try:
            mod = importlib.import_module(mod_name)
        except ImportError:
            continue
        monkeypatch.setattr(mod, "get_connection", _get_connection, raising=False)
    return path


@pytest.fixture
def client(db_path, monkeypatch):
    """Flask test client with the pulse_api blueprint + simulated auth."""
    from flask import Flask, g, session

    from tools.dashboard.api import pulse as pulse_mod

    # Neutralize side effects the publish route triggers after the gate passes.
    monkeypatch.setattr(
        "tools.pulse.engine.exporter.export_both",
        lambda pid: {"mdx": "x.mdx", "html": "x.html"},
        raising=False,
    )

    app = Flask(__name__)
    app.secret_key = "test-secret"
    app.register_blueprint(pulse_mod.pulse_api)

    @app.before_request
    def _fake_auth():  # mirrors _auth_before_request
        g.current_user = _USERS.get(session.get("user_id"))

    return app.test_client()


def _login(client, user_id):
    with client.session_transaction() as sess:
        sess["user_id"] = user_id


def _publish(client, post_id, body=None):
    return client.post(
        f"/api/pulse/posts/{post_id}/publish",
        json=(body if body is not None else {"auto_push": False}),
    )


# --- pure gate logic ---------------------------------------------------------

def test_gate_red_blocks():
    from tools.pulse.publish_gate import evaluate_publish_gate

    g = evaluate_publish_gate({"judge_color": "red"})
    assert g["blocked"] is True and g["verdict"] == "red"


@pytest.mark.parametrize("color", ["green", "yellow", "purple", "blue", "GREEN"])
def test_gate_non_red_clears(color):
    from tools.pulse.publish_gate import evaluate_publish_gate

    g = evaluate_publish_gate({"judge_color": color})
    assert g["blocked"] is False and g["cleared"] is True


@pytest.mark.parametrize("post", [{}, {"judge_color": ""}, {"judge_color": None}])
def test_gate_absent_verdict_blocks_fail_closed(post):
    from tools.pulse.publish_gate import evaluate_publish_gate

    g = evaluate_publish_gate(post)
    assert g["blocked"] is True and g["verdict"] is None


# --- publish endpoint --------------------------------------------------------

def test_publish_endpoint_red_returns_409(client, db_path):
    _seed_post(db_path, "p-red", "red")
    _login(client, "editor")
    resp = _publish(client, "p-red")
    assert resp.status_code == 409
    data = resp.get_json()
    assert data["blocked"] is True and data["verdict"] == "red"


def test_publish_endpoint_absent_verdict_returns_409(client, db_path):
    _seed_post(db_path, "p-none", None)
    _login(client, "editor")
    resp = _publish(client, "p-none")
    assert resp.status_code == 409
    assert resp.get_json()["blocked"] is True


def test_publish_endpoint_green_publishes(client, db_path):
    _seed_post(db_path, "p-green", "green")
    _login(client, "editor")
    resp = _publish(client, "p-green")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["status"] == "published" and data["forced"] is False


def test_publish_endpoint_yellow_publishes(client, db_path):
    _seed_post(db_path, "p-yellow", "yellow")
    _login(client, "editor")
    resp = _publish(client, "p-yellow")
    assert resp.status_code == 200
    assert resp.get_json()["status"] == "published"


def test_force_publish_non_admin_forbidden(client, db_path):
    _seed_post(db_path, "p-red2", "red")
    _login(client, "editor")  # reviewer, not admin
    resp = _publish(
        client, "p-red2", {"auto_push": False, "force_publish": True, "force_reason": "ship it"}
    )
    assert resp.status_code == 403
    assert resp.get_json()["blocked"] is True


def test_force_publish_admin_requires_reason(client, db_path):
    _seed_post(db_path, "p-red3", "red")
    _login(client, "admin")
    resp = _publish(client, "p-red3", {"auto_push": False, "force_publish": True, "force_reason": ""})
    assert resp.status_code == 409


def test_force_publish_admin_publishes_and_audits(client, db_path):
    _seed_post(db_path, "p-red4", "red")
    _login(client, "admin")
    resp = _publish(
        client,
        "p-red4",
        {"auto_push": False, "force_publish": True, "force_reason": "editorial exception #42"},
    )
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["status"] == "published" and data["forced"] is True

    # Audit row written (append-only, NIST AU).
    conn = sqlite3.connect(str(db_path))
    rows = conn.execute(
        "SELECT post_id, action, verdict, reviewer, reason FROM pulse_publish_audit"
    ).fetchall()
    conn.close()
    assert len(rows) == 1
    assert rows[0][0] == "p-red4"
    assert rows[0][1] == "force_publish"
    assert rows[0][2] == "red"
    assert rows[0][3] == "admin"
    assert "editorial exception #42" in rows[0][4]

    # Post actually flipped to published.
    conn = sqlite3.connect(str(db_path))
    status = conn.execute("SELECT status FROM pulse_posts WHERE id='p-red4'").fetchone()[0]
    conn.close()
    assert status == "published"


# --- batch / scheduler auto-publish -----------------------------------------

def test_scheduler_skips_red_and_absent_publishes_cleared(db_path, monkeypatch):
    """publish_all_approved skips RED + no-verdict posts, publishes cleared ones."""
    _seed_post(db_path, "b-red", "red")
    _seed_post(db_path, "b-none", None)
    _seed_post(db_path, "b-green", "green")
    _seed_post(db_path, "b-yellow", "yellow")

    from tools.pulse.engine import wordpress_publisher as wp

    published = []

    def _fake_publish_post(post_id, as_draft=False, force=False):
        published.append(post_id)
        return {"status": "ok", "post_id": post_id}

    monkeypatch.setattr(wp, "publish_post", _fake_publish_post)

    result = wp.publish_all_approved()

    assert result["skipped"] == 2
    skipped_ids = {s["post_id"] for s in result["skipped_details"]}
    assert skipped_ids == {"b-red", "b-none"}
    # Only cleared posts reached the real publisher.
    assert set(published) == {"b-green", "b-yellow"}


def test_wordpress_publish_post_blocks_red_without_creds(db_path, monkeypatch):
    """The primitive fail-closes on RED even when WP creds are absent."""
    _seed_post(db_path, "w-red", "red")
    from tools.pulse.engine import wordpress_publisher as wp

    monkeypatch.setattr(wp, "WP_PASSWORD", "", raising=False)
    result = wp.publish_post("w-red")
    assert result["status"] == "blocked" and result["verdict"] == "red"

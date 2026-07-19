# CUI // SP-CTI
"""nav-intel-09 — pulse LLM judge RED verdict hard-gates publishing.

User decision 2026-07-19: the judge is no longer advisory — a RED verdict
blocks publish. Judge-not-run or judge-errored is treated as not-cleared
(fail closed) with a "run judge first" message. YELLOW and better stay
publishable (only RED blocks). Admins may force-publish with a reason; the
override is recorded in the append-only pulse_publish_audit table
(migration 281), mirroring the citation_guard pattern.

Covers:
  - gate logic (tools/pulse/publish_gate.py): not-run / red / yellow / green /
    latest-eval-wins / post-column fallback / fail-closed on lookup error
  - API path (POST /api/pulse/posts/<id>/publish): RED -> 409; force override
    admin-only + reason required + audited; GREEN publishes
  - publisher ("scheduler"/batch) path: wordpress_publisher.publish_post and
    hostinger_publisher.publish_post return blocked for RED without force

Fixture conventions mirror tests/test_nav_sec_06_mutation_rbac.py (fake-auth
X-Test-Role blueprint app + ICDEV_DB_PATH temp SQLite).
"""
from __future__ import annotations

import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

_NO_BYPASS_VARS = (
    "ICDEV_AUTH_BYPASS",
    "ICDEV_DASHBOARD_API_KEY",
    "ICDEV_DASHBOARD_DEV_AUTOLOGIN",
)


# ── seeding helpers (all through the storage layer, never raw sqlite3) ────────

def _seed_post(post_id: str, status: str = "approved", judge_color: str | None = None):
    from tools.pulse.db import init_db, insert_row

    init_db()
    data = {
        "id": post_id,
        "title": f"Post {post_id}",
        "slug": f"slug-{post_id}-{uuid.uuid4().hex[:6]}",
        "status": status,
        "body_markdown": "# Hello\n\nBody text.",
    }
    insert_row("posts", data)
    if judge_color is not None:
        from tools.pulse.db import update_row

        update_row("posts", post_id, {"judge_color": judge_color})


def _insert_eval(post_id: str, color: str, created_at: str | None = None):
    from tools.db.storage import get_connection
    from tools.writing.llm_judge import init_judge_db

    init_judge_db()
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO llm_judge_evaluations "
            "(id, post_id, content_type, composite_score, color_rating, created_at) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            (
                f"judge-{uuid.uuid4().hex[:12]}",
                post_id,
                "blog",
                1.0 if color == "red" else 4.0,
                color,
                created_at or datetime.now(timezone.utc).isoformat(),
            ),
        )
        conn.commit()


def _audit_rows(post_id: str) -> list[dict]:
    from tools.db.storage import get_connection
    from tools.pulse.publish_gate import ensure_audit_table

    ensure_audit_table()
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM pulse_publish_audit WHERE post_id = %s", (post_id,)
        ).fetchall()
        return [dict(r) for r in rows]


@pytest.fixture()
def pulse_db(tmp_path, monkeypatch):
    """Isolated SQLite DB for the storage layer (backend forced by conftest)."""
    monkeypatch.setenv("ICDEV_DB_PATH", str(tmp_path / "pulse.db"))
    yield


# ============================================================================
# Gate logic — tools/pulse/publish_gate.py
# ============================================================================


class TestCheckPublishGate:
    def test_judge_never_ran_blocks_fail_closed(self, pulse_db):
        from tools.pulse.publish_gate import check_publish_gate

        _seed_post("p-norun")
        gate = check_publish_gate("p-norun")
        assert gate["cleared"] is False
        assert gate["verdict"] is None
        assert "judge" in gate["reason"].lower()

    def test_red_verdict_blocks(self, pulse_db):
        from tools.pulse.publish_gate import check_publish_gate

        _seed_post("p-red")
        _insert_eval("p-red", "red")
        gate = check_publish_gate("p-red")
        assert gate["cleared"] is False
        assert gate["verdict"] == "red"
        assert "red" in gate["reason"].lower()

    @pytest.mark.parametrize("color", ["yellow", "green", "purple", "blue"])
    def test_non_red_verdicts_clear(self, pulse_db, color):
        # Only RED blocks (user decision) — YELLOW stays publishable.
        from tools.pulse.publish_gate import check_publish_gate

        pid = f"p-{color}"
        _seed_post(pid)
        _insert_eval(pid, color)
        gate = check_publish_gate(pid)
        assert gate["cleared"] is True, gate
        assert gate["verdict"] == color

    def test_latest_evaluation_wins(self, pulse_db):
        from tools.pulse.publish_gate import check_publish_gate

        _seed_post("p-latest")
        _insert_eval("p-latest", "green", created_at="2026-07-01T00:00:00+00:00")
        _insert_eval("p-latest", "red", created_at="2026-07-18T00:00:00+00:00")
        gate = check_publish_gate("p-latest")
        assert gate["cleared"] is False
        assert gate["verdict"] == "red"

    def test_falls_back_to_post_judge_color(self, pulse_db):
        # Posts judged before llm_judge_evaluations rows existed still gate
        # off pulse_posts.judge_color.
        from tools.pulse.publish_gate import check_publish_gate

        _seed_post("p-fb-red", judge_color="red")
        _seed_post("p-fb-green", judge_color="green")
        assert check_publish_gate("p-fb-red")["cleared"] is False
        assert check_publish_gate("p-fb-green")["cleared"] is True

    def test_lookup_error_blocks_fail_closed(self, pulse_db, monkeypatch):
        import tools.pulse.publish_gate as pg

        def _boom(post_id):
            raise RuntimeError("db down")

        monkeypatch.setattr(pg, "get_latest_judge_verdict", _boom)
        gate = pg.check_publish_gate("p-any")
        assert gate["cleared"] is False
        assert "judge" in gate["reason"].lower()

    def test_log_publish_override_writes_audit_row(self, pulse_db):
        from tools.pulse.publish_gate import log_publish_override

        _seed_post("p-audit")
        audit_id = log_publish_override(
            "p-audit", actor="u-admin", reason="marketing deadline", verdict="red"
        )
        rows = _audit_rows("p-audit")
        assert len(rows) == 1
        assert rows[0]["id"] == audit_id
        assert rows[0]["action"] == "force_publish"
        assert rows[0]["actor"] == "u-admin"
        assert rows[0]["reason"] == "marketing deadline"
        assert rows[0]["judge_verdict"] == "red"


# ============================================================================
# API path — POST /api/pulse/posts/<id>/publish
# ============================================================================


def _fake_auth_app(register):
    from flask import Flask, g, request, session

    app = Flask(__name__)
    app.config["TESTING"] = True
    app.config["SECRET_KEY"] = "test-secret"
    app.config["PROPAGATE_EXCEPTIONS"] = False

    @app.before_request
    def _fake_auth():
        role = request.headers.get("X-Test-Role")
        if role:
            g.current_user = {"id": "u-session", "role": role, "tenant_id": "t"}
            session["user_id"] = "u-session"

    register(app)
    return app


@pytest.fixture()
def pulse_client(pulse_db, monkeypatch):
    for var in _NO_BYPASS_VARS:
        monkeypatch.delenv(var, raising=False)

    from tools.dashboard.api.pulse import pulse_api

    # Keep the handler's post-gate side effects local: no artifact export, no
    # WordPress push (auto_push=False is also sent in each request body).
    import tools.pulse.engine.exporter as exporter_mod

    monkeypatch.setattr(exporter_mod, "export_both", lambda pid: {"stubbed": True})

    app = _fake_auth_app(lambda a: a.register_blueprint(pulse_api))
    with app.test_client() as c:
        yield c


def _publish(client, post_id, role=None, body=None):
    headers = {"X-Test-Role": role} if role else {}
    return client.post(
        f"/api/pulse/posts/{post_id}/publish",
        json={"auto_push": False, **(body or {})},
        headers=headers,
    )


class TestPublishEndpointGate:
    def test_red_blocks_with_409(self, pulse_client):
        _seed_post("api-red")
        _insert_eval("api-red", "red")
        resp = _publish(pulse_client, "api-red", role="reviewer")
        assert resp.status_code == 409, resp.get_data(as_text=True)
        data = resp.get_json()
        assert data["blocked"] is True
        assert data["judge_verdict"] == "red"
        assert data["force_available"] is False  # reviewer is not admin

    def test_judge_not_run_blocks_with_409(self, pulse_client):
        _seed_post("api-norun")
        resp = _publish(pulse_client, "api-norun", role="admin")
        assert resp.status_code == 409
        data = resp.get_json()
        assert data["blocked"] is True
        assert data["judge_verdict"] is None
        assert "judge" in data["reason"].lower()
        assert data["force_available"] is True  # admin may force

    def test_judge_error_blocks_with_409(self, pulse_client, monkeypatch):
        import tools.pulse.publish_gate as pg

        _seed_post("api-err")

        def _boom(post_id):
            raise RuntimeError("judge backend unreachable")

        monkeypatch.setattr(pg, "get_latest_judge_verdict", _boom)
        resp = _publish(pulse_client, "api-err", role="admin")
        assert resp.status_code == 409
        assert resp.get_json()["blocked"] is True

    def test_green_publishes(self, pulse_client):
        _seed_post("api-green")
        _insert_eval("api-green", "green")
        resp = _publish(pulse_client, "api-green", role="reviewer")
        assert resp.status_code == 200, resp.get_data(as_text=True)
        data = resp.get_json()
        assert data["status"] == "published"
        assert data["forced"] is False

        from tools.pulse.db import get_row

        assert get_row("posts", "api-green")["status"] == "published"

    def test_yellow_publishes(self, pulse_client):
        _seed_post("api-yellow")
        _insert_eval("api-yellow", "yellow")
        resp = _publish(pulse_client, "api-yellow", role="reviewer")
        assert resp.status_code == 200, resp.get_data(as_text=True)

    def test_force_requires_admin(self, pulse_client):
        _seed_post("api-f-role")
        _insert_eval("api-f-role", "red")
        resp = _publish(
            pulse_client, "api-f-role", role="reviewer",
            body={"force_publish": True, "force_reason": "deadline"},
        )
        assert resp.status_code == 403

    def test_force_requires_reason(self, pulse_client):
        _seed_post("api-f-reason")
        _insert_eval("api-f-reason", "red")
        resp = _publish(
            pulse_client, "api-f-reason", role="admin", body={"force_publish": True}
        )
        assert resp.status_code == 400

    def test_force_publishes_and_audits(self, pulse_client):
        _seed_post("api-f-ok")
        _insert_eval("api-f-ok", "red")
        resp = _publish(
            pulse_client, "api-f-ok", role="admin",
            body={"force_publish": True, "force_reason": "AO-approved exception"},
        )
        assert resp.status_code == 200, resp.get_data(as_text=True)
        data = resp.get_json()
        assert data["status"] == "published"
        assert data["forced"] is True

        rows = _audit_rows("api-f-ok")
        assert len(rows) == 1
        assert rows[0]["actor"] == "u-session"  # session user, not body-spoofable
        assert rows[0]["reason"] == "AO-approved exception"
        assert rows[0]["judge_verdict"] == "red"
        assert rows[0]["source"] == "api_publish"

    def test_anonymous_is_401(self, pulse_client):
        resp = _publish(pulse_client, "any")
        assert resp.status_code == 401


# ============================================================================
# Publisher ("scheduler"/batch) paths
# ============================================================================


class TestPublisherGate:
    def test_wordpress_publish_blocked_on_red(self, pulse_db):
        from tools.pulse.engine import wordpress_publisher as wp

        _seed_post("wp-red")
        _insert_eval("wp-red", "red")
        result = wp.publish_post("wp-red")
        assert result["status"] == "blocked"
        assert result["judge_verdict"] == "red"

    def test_wordpress_publish_blocked_when_judge_not_run(self, pulse_db):
        from tools.pulse.engine import wordpress_publisher as wp

        _seed_post("wp-norun")
        result = wp.publish_post("wp-norun")
        assert result["status"] == "blocked"
        assert "judge" in result["message"].lower()

    def test_wordpress_green_passes_gate(self, pulse_db, monkeypatch):
        from tools.pulse.engine import wordpress_publisher as wp

        # Empty WP_PASSWORD keeps the (now post-gate) credential check as a
        # safe early exit — the point is the gate no longer blocks.
        monkeypatch.setattr(wp, "WP_PASSWORD", "", raising=False)
        _seed_post("wp-green")
        _insert_eval("wp-green", "green")
        result = wp.publish_post("wp-green")
        assert result["status"] != "blocked"

    def test_wordpress_force_requires_reason(self, pulse_db, monkeypatch):
        from tools.pulse.engine import wordpress_publisher as wp

        monkeypatch.setattr(wp, "WP_PASSWORD", "", raising=False)
        _seed_post("wp-f-reason")
        _insert_eval("wp-f-reason", "red")
        result = wp.publish_post("wp-f-reason", force=True)
        assert result["status"] == "error"
        assert "force_reason" in result["message"]
        assert _audit_rows("wp-f-reason") == []

    def test_wordpress_force_with_reason_audits_and_passes_gate(self, pulse_db, monkeypatch):
        from tools.pulse.engine import wordpress_publisher as wp

        monkeypatch.setattr(wp, "WP_PASSWORD", "", raising=False)
        _seed_post("wp-f-ok")
        _insert_eval("wp-f-ok", "red")
        result = wp.publish_post(
            "wp-f-ok", force=True, force_reason="AO-approved exception", actor="ops"
        )
        assert result["status"] != "blocked"
        rows = _audit_rows("wp-f-ok")
        assert len(rows) == 1
        assert rows[0]["actor"] == "ops"
        assert rows[0]["source"] == "wordpress_publisher"

    def test_hostinger_publish_blocked_on_red(self, pulse_db):
        from tools.pulse.engine import hostinger_publisher as hp

        _seed_post("hp-red")
        _insert_eval("hp-red", "red")
        result = hp.publish_post("hp-red")
        assert result["status"] == "blocked"
        assert result["judge_verdict"] == "red"

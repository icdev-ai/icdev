# CUI // SP-CTI
"""nav-comp-05 — SRE API role gating + fail-loud DORA metrics.

Two classes of P1 defect in ``tools/dashboard/api/sre.py``:

  1. Every state-changing endpoint (incident create/triage/escalate/resolve/
     postmortem/close, runbook create/execute/match, SLO create/measure, and
     the alert / SLO-breach chain processors) relied on global auth only — a
     lowest-privilege ``developer`` could create incidents and execute runbooks
     (which run shell steps). Each POST is now hard-gated with the shared
     dashboard ``@require_role`` decorator (401 anonymous, 403 wrong role,
     allowed for ``admin``/``isso``/``component_admin``).

  2. The DORA scorecard degraded dishonestly: Lead Time used SQLite-only
     ``julianday``/``datetime(...)`` SQL that always raised on PostgreSQL and
     was swallowed to a tautological constant 1.0h; and DB errors on the deploy
     / CFR / MTTR counts fell through to 0, which the rating bands then reported
     as "Elite". It now computes Lead Time from real pipeline timestamps in
     Python, and any un-assessable metric (DB error, or genuinely no data) is
     reported as "Not Assessed" — never a fabricated favorable rating.

Fixture conventions mirror ``tests/test_nav_sec_06_mutation_rbac.py`` (bare
Flask app + fake-auth ``X-Test-Role`` header for blueprint-only role tests).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import tools.dashboard.api.sre as sre_mod  # noqa: E402


# ── all state-changing endpoints (must be role-gated) ──────────────────────────
MUTATING_ROUTES = [
    ("POST", "/api/sre/slos"),
    ("POST", "/api/sre/slos/s1/measure"),
    ("POST", "/api/sre/incidents"),
    ("POST", "/api/sre/incidents/i1/triage"),
    ("POST", "/api/sre/incidents/i1/escalate"),
    ("POST", "/api/sre/incidents/i1/resolve"),
    ("POST", "/api/sre/incidents/i1/postmortem"),
    ("POST", "/api/sre/incidents/i1/close"),
    ("POST", "/api/sre/runbooks"),
    ("POST", "/api/sre/runbooks/rb1/execute"),
    ("POST", "/api/sre/runbooks/match"),
    ("POST", "/api/sre/chain/process-alert"),
    ("POST", "/api/sre/chain/slo-breach"),
]


def _fake_auth_app():
    """Bare Flask app with the sre blueprint and a fake-auth before_request that
    sets ``g.current_user`` from an ``X-Test-Role`` header (mirrors nav-sec-06)."""
    from flask import Flask, g, request, session

    app = Flask(__name__)
    app.config["TESTING"] = True
    app.config["SECRET_KEY"] = "test-secret"
    app.config["PROPAGATE_EXCEPTIONS"] = False

    @app.before_request
    def _fake_auth():
        role = request.headers.get("X-Test-Role")
        if role:
            g.current_user = {"id": "u-session", "username": "u-session", "role": role, "tenant_id": "t"}
            session["user_id"] = "u-session"

    app.register_blueprint(sre_mod.sre_api)
    return app


@pytest.fixture()
def client():
    with _fake_auth_app().test_client() as c:
        yield c


def _dispatch(client, method, path, headers=None):
    return client.open(
        path, method=method, json={}, content_type="application/json", headers=headers or {}
    )


# ── deny cases (mandatory) ─────────────────────────────────────────────────────

@pytest.mark.parametrize("method,path", MUTATING_ROUTES)
def test_mutating_anonymous_is_401(client, method, path):
    resp = _dispatch(client, method, path)
    assert resp.status_code == 401, f"{method} {path} -> {resp.status_code}"


@pytest.mark.parametrize("method,path", MUTATING_ROUTES)
def test_mutating_developer_is_403(client, method, path):
    resp = _dispatch(client, method, path, {"X-Test-Role": "developer"})
    assert resp.status_code == 403, f"{method} {path} -> {resp.status_code}"


# ── allow case: an authorized operator role clears the gate ───────────────────

@pytest.mark.parametrize("role", ["admin", "isso", "component_admin"])
@pytest.mark.parametrize("method,path", MUTATING_ROUTES)
def test_mutating_authorized_role_not_gated(client, role, method, path):
    # The handler may 200/400/404/500 depending on backing data — the point is
    # that @require_role does NOT reject an authorized operator (never 401/403).
    resp = _dispatch(client, method, path, {"X-Test-Role": role})
    assert resp.status_code not in (401, 403), f"{role} {method} {path} -> {resp.status_code}"


def test_reads_stay_open_for_authenticated_developer(client):
    # GET surfaces are reads — they must NOT require an operator role.
    for path in ("/api/sre/slos", "/api/sre/incidents", "/api/sre/runbooks", "/api/sre/dashboard"):
        resp = client.get(path, headers={"X-Test-Role": "developer"})
        assert resp.status_code not in (401, 403), f"GET {path} -> {resp.status_code}"


# ── attribution: actor comes from the session, never the request body ─────────

def test_actor_resolves_from_session_not_body():
    app = _fake_auth_app()
    from flask import g

    with app.test_request_context("/api/sre/chain/process-alert", method="POST",
                                  json={"actor": "spoofed-attacker"}):
        g.current_user = {"id": "u1", "username": "alice", "role": "admin"}
        assert sre_mod._actor() == "alice"
    # No identity ⇒ 'system', never a body-supplied value.
    with app.test_request_context("/api/sre/chain/process-alert", method="POST",
                                  json={"actor": "spoofed-attacker"}):
        assert sre_mod._actor() == "system"


# ── DORA metrics ───────────────────────────────────────────────────────────────

def _seed_dora_db(db_path):
    """Create the three source tables the DORA endpoint reads and seed known
    rows so the computed metrics are hand-verifiable."""
    from datetime import datetime, timezone, timedelta
    from tools.db.storage import get_connection

    conn = get_connection(db_path=db_path)
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS audit_trail (
            id INTEGER PRIMARY KEY AUTOINCREMENT, event_type TEXT, action TEXT,
            details TEXT, created_at TEXT
        );
        CREATE TABLE IF NOT EXISTS sre_incidents (
            id TEXT PRIMARY KEY, status TEXT, mttr_seconds INTEGER, resolved_at TEXT
        );
        CREATE TABLE IF NOT EXISTS ci_pipeline_runs (
            id TEXT PRIMARY KEY, status TEXT, created_at TEXT, completed_at TEXT
        );
        """
    )
    now = datetime.now(timezone.utc)
    now_iso = now.isoformat()

    # 10 successful deploys, 1 failed deploy → CFR = 10.0%
    for i in range(10):
        conn.execute(
            "INSERT INTO audit_trail (event_type, action, created_at) VALUES (%s, %s, %s)",
            ("deployment_initiated", "deploy", now_iso),
        )
    conn.execute(
        "INSERT INTO audit_trail (event_type, action, created_at) VALUES (%s, %s, %s)",
        ("deployment_failed", "deploy", now_iso),
    )

    # 2 resolved incidents, mttr 1000s + 3000s → avg 2000s (Elite)
    for idx, secs in enumerate((1000, 3000)):
        conn.execute(
            "INSERT INTO sre_incidents (id, status, mttr_seconds, resolved_at) VALUES (%s, %s, %s, %s)",
            (f"inc-{idx}", "resolved", secs, now_iso),
        )

    # 2 completed pipeline runs, durations 1h + 3h → avg 2.0h lead time
    for idx, dur_h in enumerate((1, 3)):
        started = (now - timedelta(hours=dur_h)).isoformat()
        conn.execute(
            "INSERT INTO ci_pipeline_runs (id, status, created_at, completed_at) VALUES (%s, %s, %s, %s)",
            (f"run-{idx}", "completed", started, now_iso),
        )
    conn.commit()
    conn.close()


def test_dora_seeded_values(tmp_path, monkeypatch):
    db_path = str(tmp_path / "dora.db")
    _seed_dora_db(db_path)

    from tools.db.storage import get_connection
    monkeypatch.setattr(sre_mod, "get_connection", lambda *a, **k: get_connection(db_path=db_path))

    app = _fake_auth_app()
    resp = app.test_client().get("/api/sre/dora?days=30", headers={"X-Test-Role": "admin"})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    d = resp.get_json()

    # Deploy frequency: 10 / 30 = 0.33 deploys/day (High)
    assert d["deploy_frequency"]["total_deploys"] == 10
    assert d["deploy_frequency"]["value"] == 0.33
    assert d["deploy_frequency"]["rating"] == "High"

    # Change failure rate: 1 / 10 = 10.0% (Medium)
    assert d["change_failure_rate"]["failed_deploys"] == 1
    assert d["change_failure_rate"]["value"] == 10.0
    assert d["change_failure_rate"]["rating"] == "Medium"

    # MTTR: (1000 + 3000) / 2 = 2000s (Elite)
    assert d["mttr"]["incidents_resolved"] == 2
    assert d["mttr"]["value"] == 2000
    assert d["mttr"]["rating"] == "Elite"

    # Lead time: (1h + 3h) / 2 = 2.0h real Python-computed delta (High)
    assert d["lead_time"]["samples"] == 2
    assert d["lead_time"]["value"] == 2.0
    assert d["lead_time"]["rating"] == "High"
    # Never the old tautological constant.
    assert d["lead_time"]["value"] != 1.0

    assert d["overall_rating"] == "High"
    assert d["metrics_assessed"] == 4


class _RaisingConn:
    """Stand-in connection whose every query fails — simulates a DB outage."""

    def execute(self, *a, **k):
        raise RuntimeError("simulated DB failure")

    def close(self):
        pass


def test_dora_db_failure_is_not_assessed_never_elite(monkeypatch):
    monkeypatch.setattr(sre_mod, "get_connection", lambda *a, **k: _RaisingConn())

    app = _fake_auth_app()
    resp = app.test_client().get("/api/sre/dora?days=30", headers={"X-Test-Role": "admin"})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    d = resp.get_json()

    for key in ("deploy_frequency", "lead_time", "change_failure_rate", "mttr"):
        assert d[key]["rating"] == "Not Assessed", (key, d[key])
        assert d[key].get("error") is True, (key, d[key])
        # A DB failure must never be laundered into a favorable rating or the
        # old tautological lead-time constant.
        assert d[key]["rating"] != "Elite"
        assert d[key].get("value") != 1.0

    assert d["overall_rating"] == "Not Assessed"
    assert d["metrics_assessed"] == 0


def test_no_sqlite_only_date_sql_in_module():
    """Source scan: no SQLite-only ``julianday`` / ``datetime('now'`` remains in
    executable SQL. Comment lines (documenting the fix) are excluded."""
    src = Path(sre_mod.__file__).read_text(encoding="utf-8")
    code_lines = []
    for line in src.splitlines():
        stripped = line.lstrip()
        if stripped.startswith("#"):
            continue
        code_lines.append(line)
    code = "\n".join(code_lines)
    assert "julianday" not in code, "SQLite-only julianday() still present in module SQL"
    assert "datetime('now'" not in code, "SQLite-only datetime('now') still present in module SQL"
    assert 'datetime("now"' not in code

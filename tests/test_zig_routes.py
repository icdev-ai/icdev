# CUI // SP-CTI
"""Functional route coverage for the NSA ZIG (Zero Trust Implementation Guide)
surface of the Security Design Canvas — shx-test-01.

The existing suites cover auth wiring (test_sdc_auth_meta), presented-key API
semantics (test_sdc_auth_apikey), the ingest size/XML defenses
(test_zig_ingest_route), and generic error handling (test_sc_blueprint_errors).
This suite covers the *functional* behavior of the ZIG read endpoints, mutation
happy paths, bad-input 4xx branches, and page rendering that those suites do not.

Fixture pattern mirrors test_zig_ingest_route: a Flask test client against
``create_security_blueprint()`` with ICDEV_AUTH_BYPASS, plus an authenticated
principal in ``g.current_user`` — ICDEV_AUTH_BYPASS alone only satisfies
``sc_login_required``; the ZIG *mutation* routes are additionally gated by
``@require_role(*_ZIG_MUTATION_ROLES)`` (nav-sec-05), which reads
``g.current_user`` and is normally populated by the dashboard's before_request
hook that this bare test app does not install. The gate itself is asserted in
section 4 rather than merely satisfied. The canvas DB is
redirected to a per-test scratch SQLite file (conftest forces sqlite), seeded by
``init_db()``. ``init_db()``'s SCHEMA does not create ``zig_targets`` (that table
ships only in the canvas migration 001_security_canvas_core.sql), so the fixture
creates it explicitly for the targets happy-path test.

Two behaviors were originally *locked in as observed* rather than asserted as
desirable. Both have since been fixed by shx-hyg-08 and are now asserted as
correct, not merely recorded:
  * PATCH /api/zig/capabilities/<unknown-id> checks rowcount and returns
    404 ok=False (it used to return a silent 200 ok=True).
  * POST /api/zig/assess runs the GLOBAL assessment and returns 200 (it used to
    500, calling ``run_zig_assessment(target_id=...)`` with an arg it lacks).
"""
from __future__ import annotations

import pytest

# zig_targets DDL — canvas migration 001_security_canvas_core.sql. init_db()'s
# inline SCHEMA omits it, so the scratch DB needs it created for targets tests.
_ZIG_TARGETS_DDL = """
CREATE TABLE IF NOT EXISTS zig_targets (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    description     TEXT,
    system_type     TEXT DEFAULT 'general',
    classification  TEXT DEFAULT 'CUI',
    status          TEXT DEFAULT 'active',
    pillar_focus    TEXT DEFAULT '',
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at      TEXT DEFAULT CURRENT_TIMESTAMP
);
"""


@pytest.fixture()
def app(tmp_path, monkeypatch):
    """Flask app with the security_canvas blueprint against a scratch canvas DB."""
    from pathlib import Path

    import tools.security_canvas.db.init_db as idb

    monkeypatch.setenv("ICDEV_SECURITY_ENABLED", "true")
    # Force the canvas onto its SQLite scratch DB regardless of ambient env.
    monkeypatch.setenv("SC_STORAGE_BACKEND", "sqlite")
    monkeypatch.setattr(idb, "_SC_BACKEND", "sqlite", raising=False)
    monkeypatch.setattr(idb, "DB_PATH", Path(tmp_path) / "security_canvas.db")

    idb.init_db()

    # Create zig_targets (not part of init_db's SCHEMA) for the targets tests.
    conn = idb.get_connection()
    try:
        conn.execute(_ZIG_TARGETS_DDL)
        conn.commit()
    finally:
        conn.close()

    from flask import Flask

    from tools.security_canvas.blueprint import create_security_blueprint

    flask_app = Flask(__name__, template_folder="../tools/dashboard/templates")
    flask_app.config.update(TESTING=True, SECRET_KEY="test-secret", WTF_CSRF_ENABLED=False)

    bp = create_security_blueprint()
    assert bp is not None, "create_security_blueprint() returned None"
    flask_app.register_blueprint(bp, url_prefix="/security")
    return flask_app


@pytest.fixture(autouse=True)
def _bypass_auth(monkeypatch):
    monkeypatch.setenv("ICDEV_AUTH_BYPASS", "true")
    monkeypatch.delenv("ICDEV_DASHBOARD_API_KEY", raising=False)


def _login_as(flask_app, role="admin", user_id="u-test"):
    """Install the authenticated principal the dashboard normally supplies.

    ``require_role`` (nav-sec-05) reads ``g.current_user``, which the dashboard's
    before_request hook sets. This bare test app registers only the canvas
    blueprint, so without this the gated routes 401 on every caller. Pass
    ``role=None`` to stay anonymous and exercise the 401 branch.

    Returns the mutable principal dict so a test can switch roles on one app
    without registering a second before_request handler (whose effect would
    otherwise depend on registration order).
    """
    from flask import g

    principal = {"id": user_id, "role": role}

    @flask_app.before_request
    def _set_current_user():  # pragma: no cover - trivial fixture wiring
        if principal["role"] is not None:
            g.current_user = {"id": principal["id"], "role": principal["role"]}

    return principal


@pytest.fixture()
def client(app):
    # "admin" is in _ZIG_MUTATION_ROLES; the gate is asserted in section 4.
    _login_as(app, role="admin")
    with app.test_client() as c:
        yield c


@pytest.fixture()
def client_as(app):
    """Factory for a client bound to a specific role (or anonymous, role=None)."""
    def _make(role):
        _login_as(app, role=role)
        return app.test_client()

    return _make


@pytest.fixture()
def seed(app):
    """A sample capability + activity + pillar drawn from the live seed."""
    import tools.security_canvas.db.init_db as idb

    conn = idb.get_connection()
    try:
        cap = conn.execute(
            "SELECT id, pillar_slug, phase FROM zig_capabilities ORDER BY id LIMIT 1"
        ).fetchone()
        act = conn.execute(
            "SELECT id, capability_id, phase FROM zig_activities ORDER BY id LIMIT 1"
        ).fetchone()
    finally:
        conn.close()
    return {
        "cap_id": cap["id"],
        "cap_pillar": cap["pillar_slug"],
        "act_id": act["id"],
        "act_cap": act["capability_id"],
        "act_phase": act["phase"],
    }


def _db():
    import tools.security_canvas.db.init_db as idb

    return idb.get_connection()


# ── 1. Read endpoints ───────────────────────────────────────────────────────

def test_pillars_lists_seven_seeded(client):
    resp = client.get("/security/api/zig/pillars")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["ok"] is True
    assert len(body["pillars"]) == 7
    # aggregate carries the canonical maturity shape
    assert set(body["aggregate"]) >= {"score", "maturity_level", "fy2027_readiness_pct"}


def test_pillar_detail_shape(client, seed):
    resp = client.get(f"/security/api/zig/pillars/{seed['cap_pillar']}")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["ok"] is True
    assert body["pillar"]["slug"] == seed["cap_pillar"]
    assert "score" in body
    assert isinstance(body["capabilities"], list) and body["capabilities"]


def test_pillar_detail_unknown_slug_404(client):
    resp = client.get("/security/api/zig/pillars/not-a-pillar")
    assert resp.status_code == 404
    assert resp.get_json()["ok"] is False


def test_capabilities_filter_by_pillar(client, seed):
    resp = client.get(f"/security/api/zig/capabilities?pillar={seed['cap_pillar']}")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["ok"] is True
    assert body["count"] == len(body["capabilities"]) > 0
    # every returned capability belongs to the requested pillar
    assert all(c["pillar_slug"] == seed["cap_pillar"] for c in body["capabilities"])


def test_activities_filter_by_capability_and_phase(client, seed):
    resp = client.get(
        f"/security/api/zig/activities?capability={seed['act_cap']}&phase={seed['act_phase']}"
    )
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["ok"] is True
    assert body["count"] == len(body["activities"]) > 0
    for a in body["activities"]:
        assert a["capability_id"] == seed["act_cap"]
        assert a["phase"] == seed["act_phase"]


def test_maturity_aggregate_shape(client):
    resp = client.get("/security/api/zig/maturity")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["ok"] is True
    assert set(body["aggregate"]) >= {"score", "maturity_level", "fy2027_readiness_pct"}
    assert len(body["pillar_scores"]) == 7
    assert "fy2027" in body


def test_phases_shape(client):
    resp = client.get("/security/api/zig/phases")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["ok"] is True
    assert isinstance(body["phases"], list) and len(body["phases"]) == 3
    slugs = {p["slug"] for p in body["phases"]}
    assert slugs == {"discovery", "phase1", "phase2"}
    for p in body["phases"]:
        assert {"total", "complete", "in_progress", "not_started", "pct_complete"} <= set(p)


def test_roadmap_shape(client):
    resp = client.get("/security/api/zig/roadmap")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["ok"] is True
    assert "milestones" in body
    assert "overall_pct_complete" in body
    assert body["fy2027_deadline"] and body["fy2032_deadline"]


# ── 2. Mutation happy paths ─────────────────────────────────────────────────

def test_patch_capability_status_updates_db(client, seed):
    cap_id = seed["cap_id"]
    resp = client.patch(
        f"/security/api/zig/capabilities/{cap_id}",
        json={"implementation_status": "in_progress", "evidence_note": "wip"},
    )
    assert resp.status_code == 200
    body = resp.get_json()
    assert body == {"ok": True, "id": cap_id, "implementation_status": "in_progress"}

    conn = _db()
    try:
        row = conn.execute(
            "SELECT implementation_status, evidence_note FROM zig_capabilities WHERE id=?",
            (cap_id,),
        ).fetchone()
    finally:
        conn.close()
    assert row["implementation_status"] == "in_progress"
    assert row["evidence_note"] == "wip"


def test_activity_complete_creates_completion_row(client, seed):
    act_id = seed["act_id"]
    resp = client.patch(
        f"/security/api/zig/activities/{act_id}/complete",
        json={"status": "complete", "evidence_note": "verified"},
    )
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["ok"] is True
    assert body["activity_id"] == act_id
    assert body["status"] == "complete"

    conn = _db()
    try:
        row = conn.execute(
            "SELECT status, evidence_note, completed_at FROM zig_activity_completions "
            "WHERE activity_id=?",
            (act_id,),
        ).fetchone()
    finally:
        conn.close()
    assert row is not None
    assert row["status"] == "complete"
    assert row["evidence_note"] == "verified"
    assert row["completed_at"]  # completion timestamp set for 'complete'


def test_create_target_persists_row(client):
    resp = client.post(
        "/security/api/zig/targets",
        json={"id": "shx-test-app", "name": "SHX Test App", "system_type": "web-app"},
    )
    assert resp.status_code == 201
    body = resp.get_json()
    assert body == {"ok": True, "id": "shx-test-app"}

    conn = _db()
    try:
        row = conn.execute(
            "SELECT name, system_type, classification, status FROM zig_targets WHERE id=?",
            ("shx-test-app",),
        ).fetchone()
    finally:
        conn.close()
    assert row is not None
    assert row["name"] == "SHX Test App"
    assert row["system_type"] == "web-app"
    # defaults applied by the route
    assert row["classification"] == "CUI"
    assert row["status"] == "active"


def test_target_activity_update_returns_200(client, seed):
    """cnr-zig-02: PATCH /api/zig/targets/<id>/activities/<act_id> threads
    target_id into set_activity_status and returns 200. Previously the tracker
    signature had no target_id, so this raised TypeError -> 500 on every call.
    Exercises the REAL route + tracker (no mocks) — the path the strong suite
    left untested."""
    client.post(
        "/security/api/zig/targets",
        json={"id": "tgt-patch", "name": "Patch Target"},
    )
    act_id = seed["act_id"]
    resp = client.patch(
        f"/security/api/zig/targets/tgt-patch/activities/{act_id}",
        json={"status": "complete", "evidence_note": "done"},
    )
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["ok"] is True
    assert body["target_id"] == "tgt-patch"
    assert body["status"] == "complete"

    conn = _db()
    try:
        row = conn.execute(
            "SELECT target_id, status FROM zig_activity_completions "
            "WHERE activity_id=? AND target_id=?",
            (act_id, "tgt-patch"),
        ).fetchone()
    finally:
        conn.close()
    assert row is not None
    assert row["target_id"] == "tgt-patch"
    assert row["status"] == "complete"


def test_target_completion_isolated_from_icdev_self(client, seed):
    """cnr-zig-02: a completion recorded for an external target must NOT appear
    under the platform self target (they were previously collapsed onto
    'icdev-self' because the INSERT omitted target_id)."""
    client.post(
        "/security/api/zig/targets",
        json={"id": "tgt-iso", "name": "Isolated Target"},
    )
    act_id = seed["act_id"]
    client.patch(
        f"/security/api/zig/targets/tgt-iso/activities/{act_id}",
        json={"status": "complete"},
    )

    conn = _db()
    try:
        self_row = conn.execute(
            "SELECT 1 FROM zig_activity_completions "
            "WHERE activity_id=? AND target_id=?",
            (act_id, "icdev-self"),
        ).fetchone()
    finally:
        conn.close()
    assert self_row is None


def test_assess_route_runs_global_and_persists(client):
    """FIXED (shx-hyg-08): the plain /assess route now runs the GLOBAL ZIG
    assessment (run_zig_assessment() takes no args) and persists pillar scores
    to zig_maturity_scores. Target-scoped assessment lives at
    POST /api/zig/targets/<id>/assess."""
    resp = client.post("/security/api/zig/assess", json={})
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["ok"] is True
    assert "pillar_scores" in body
    assert "aggregate" in body

    conn = _db()
    try:
        n = conn.execute("SELECT COUNT(*) AS c FROM zig_maturity_scores").fetchone()["c"]
    finally:
        conn.close()
    assert n > 0


def test_assess_route_rejects_target_id_400(client):
    """A target_id in the body is rejected with a 400 that points callers to the
    target-scoped route, so a global run is never mistaken for a scoped one."""
    resp = client.post("/security/api/zig/assess", json={"target_id": "icdev-self"})
    assert resp.status_code == 400
    body = resp.get_json()
    assert body["ok"] is False
    assert "target" in body["error"]


# ── 2b. Non-repudiation: evidence/assessment writes are audited (cnr-zig-03) ──


def _audit_actions(entity_id=None):
    """Return the list of sc_audit action strings, optionally filtered by entity_id.

    Reads the REAL sc_audit table written by the route handlers (no mocks)."""
    conn = _db()
    try:
        if entity_id is not None:
            rows = conn.execute(
                "SELECT action FROM sc_audit WHERE entity_id=?", (entity_id,)
            ).fetchall()
        else:
            rows = conn.execute("SELECT action FROM sc_audit").fetchall()
        return [r["action"] for r in rows]
    finally:
        conn.close()


def test_capability_status_change_is_audited(client, seed):
    cap_id = seed["cap_id"]
    resp = client.patch(
        f"/security/api/zig/capabilities/{cap_id}",
        json={"implementation_status": "in_progress", "evidence_note": "wip"},
    )
    assert resp.status_code == 200
    assert "zig_capability_status_change" in _audit_actions(cap_id)


def test_activity_completion_is_audited(client, seed):
    act_id = seed["act_id"]
    resp = client.patch(
        f"/security/api/zig/activities/{act_id}/complete",
        json={"status": "complete", "evidence_note": "verified"},
    )
    assert resp.status_code == 200
    assert "zig_activity_completion" in _audit_actions(act_id)


def test_target_activity_completion_is_audited(client, seed):
    client.post(
        "/security/api/zig/targets",
        json={"id": "tgt-audit", "name": "Audit Target"},
    )
    act_id = seed["act_id"]
    resp = client.patch(
        f"/security/api/zig/targets/tgt-audit/activities/{act_id}",
        json={"status": "complete", "evidence_note": "e"},
    )
    assert resp.status_code == 200
    assert "zig_activity_completion" in _audit_actions(f"tgt-audit:{act_id}")


def test_global_assessment_run_is_audited(client):
    resp = client.post("/security/api/zig/assess", json={})
    assert resp.status_code == 200
    assert "zig_assessment_run" in _audit_actions("icdev-self")


def test_target_assessment_run_is_audited(client):
    client.post(
        "/security/api/zig/targets",
        json={"id": "tgt-assess", "name": "Assess Target"},
    )
    resp = client.post("/security/api/zig/targets/tgt-assess/assess", json={})
    assert resp.status_code == 200
    assert "zig_assessment_run" in _audit_actions("tgt-assess")


# ── 3. Bad input 4xx ────────────────────────────────────────────────────────

def test_patch_capability_invalid_status_400(client, seed):
    resp = client.patch(
        f"/security/api/zig/capabilities/{seed['cap_id']}",
        json={"implementation_status": "totally-invalid"},
    )
    assert resp.status_code == 400
    assert resp.get_json()["ok"] is False


def test_patch_unknown_capability_id_returns_404(client):
    """FIXED (shx-hyg-08): the handler now checks rowcount after the UPDATE, so
    an unknown id yields 404 ok=False instead of a silent 200."""
    resp = client.patch(
        "/security/api/zig/capabilities/does-not-exist-999",
        json={"implementation_status": "planned"},
    )
    assert resp.status_code == 404
    body = resp.get_json()
    assert body["ok"] is False
    assert "unknown capability id" in body["error"]

    # No phantom row was created.
    conn = _db()
    try:
        row = conn.execute(
            "SELECT id FROM zig_capabilities WHERE id=?", ("does-not-exist-999",)
        ).fetchone()
    finally:
        conn.close()
    assert row is None


def test_create_target_missing_fields_400(client):
    resp = client.post("/security/api/zig/targets", json={"name": "no id given"})
    assert resp.status_code == 400
    assert resp.get_json()["ok"] is False


def test_ingest_unknown_source_type_400(client):
    resp = client.post(
        "/security/api/zig/targets/shx-test-app/ingest",
        json={"source_type": "not-a-source", "payload": "x"},
    )
    assert resp.status_code == 400
    body = resp.get_json()
    assert body["ok"] is False
    assert "unknown source_type" in body["error"]


# ── 4. Page routes render 200 under session auth ────────────────────────────

@pytest.mark.parametrize(
    "path",
    [
        "/security/zig/",
        "/security/zig/phase",
        "/security/zig/assessment",
        "/security/zig/roadmap",
        "/security/zig/portfolio",
    ],
)
def test_zig_page_routes_render_200(client, path):
    resp = client.get(path)
    assert resp.status_code == 200


def test_zig_pillar_detail_page_renders_200(client, seed):
    resp = client.get(f"/security/zig/pillar/{seed['cap_pillar']}")
    assert resp.status_code == 200


# ── 5. RBAC: ZIG mutation routes are role-gated (nav-sec-05) ────────────────
#
# The happy-path tests above authenticate as "admin", which SATISFIES this gate.
# Satisfying a gate does not prove it exists, so these assert it directly: the
# mutating endpoints must reject an anonymous caller and an authenticated but
# unauthorized one. Without these, dropping @require_role would leave the whole
# suite green.

def _mutation_calls(client, cap_id):
    """The two @require_role-gated ZIG endpoints, as (label, response) pairs."""
    return [
        (
            "PATCH capability status",
            client.patch(
                f"/security/api/zig/capabilities/{cap_id}",
                json={"implementation_status": "planned"},
            ),
        ),
        (
            "POST global assess",
            client.post("/security/api/zig/assess", json={}),
        ),
    ]


def test_zig_mutation_routes_reject_anonymous_401(client_as, seed):
    """No g.current_user at all -> 401, even though ICDEV_AUTH_BYPASS is set.

    ICDEV_AUTH_BYPASS only clears sc_login_required; require_role is a second,
    independent gate and must not be satisfied by the bypass.
    """
    anon = client_as(role=None)
    for label, resp in _mutation_calls(anon, seed["cap_id"]):
        assert resp.status_code == 401, f"{label} did not 401 for anonymous"


def test_zig_mutation_routes_reject_unauthorized_role_403(client_as, seed):
    """Authenticated but not a security-officer role -> 403."""
    from tools.security_canvas.blueprint import _ZIG_MUTATION_ROLES

    assert "developer" not in _ZIG_MUTATION_ROLES  # premise of this test
    dev = client_as(role="developer")
    for label, resp in _mutation_calls(dev, seed["cap_id"]):
        assert resp.status_code == 403, f"{label} did not 403 for role=developer"


def test_zig_mutation_routes_allow_every_declared_role(app, seed):
    """Each role in _ZIG_MUTATION_ROLES is actually accepted.

    Derived from the constant rather than hardcoded, so adding a role to the
    tuple without wiring it up fails here instead of silently passing.
    """
    from tools.security_canvas.blueprint import _ZIG_MUTATION_ROLES

    assert _ZIG_MUTATION_ROLES, "gate declares no authorized roles"
    principal = _login_as(app, role=_ZIG_MUTATION_ROLES[0])
    client = app.test_client()
    for role in _ZIG_MUTATION_ROLES:
        principal.update(role=role, id=f"u-{role}")
        resp = client.patch(
            f"/security/api/zig/capabilities/{seed['cap_id']}",
            json={"implementation_status": "planned"},
        )
        assert resp.status_code not in (401, 403), (
            f"role {role!r} is declared authorized but was rejected "
            f"with {resp.status_code}"
        )

# CUI // SP-CTI
"""penta-aca-07 — FORGE Academy route smoke suite.

Authenticated test-client smoke over EVERY Academy route in the blueprint's
url_map (parametrized). The contract: **no route may 500.** Redirects (301/302),
guard denials (401/403), and not-found/bad-input (404/400) are all acceptable —
only a 5xx server error is a failure.

Page routes render dashboard templates that require the full dashboard context
(nav_tree, ROLE_VIEWS, 30+ canvas flags injected by tools/dashboard/app.py). That
context is a property of the *dashboard app*, not the Academy blueprint, and
re-creating it here would test the harness rather than the views. So we patch
``render_template`` to a sentinel: every page view's real Python (DB access,
guards, redirects, lookups) still runs — a bug there still 500s — but we don't
couple the test to base.html's evolving global set. API/JSON routes run for real.

Covers explicitly: the /academy hub, the missions listing (shows batch-1
missions), and the /api/academy/health endpoint (penta-aca-06).
"""

from __future__ import annotations

import re

import pytest
from flask import Flask, g


# ---------------------------------------------------------------------------
# App fixture: real migrated DB + seeded catalog, admin user, patched templates
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def _seeded():
    from apps.forge_academy import content_loader, db

    db.migrate()
    content_loader.seed_mission_catalog()


@pytest.fixture()
def academy(_seeded, monkeypatch):
    """(client, captures) — Academy blueprint on a bare app, admin user set,
    render_template patched to a sentinel that records (template, context)."""
    import apps.forge_academy.blueprint as bp_mod

    captures: list[tuple[str, dict]] = []

    def _fake_render(template_name, **context):
        captures.append((template_name, context))
        return f"<rendered:{template_name}>"

    monkeypatch.setattr(bp_mod, "render_template", _fake_render)

    app = Flask(__name__)
    app.config["TESTING"] = True
    # A live server turns an unhandled view exception into a 500 RESPONSE. With
    # TESTING=True Flask would instead re-raise it into the test, so force response
    # semantics — the smoke's contract is "no route returns 5xx".
    app.config["PROPAGATE_EXCEPTIONS"] = False

    @app.before_request
    def _set_user():
        g.current_user = {"id": "admin1", "role": "admin", "email": "admin@test.local"}

    app.register_blueprint(bp_mod.bp)
    return app.test_client(), captures


# ---------------------------------------------------------------------------
# Build the parametrized route list from the live url_map
# ---------------------------------------------------------------------------

def _dummy_for(token: str) -> str:
    """Substitute a converter token like '<int:guild_id>' or '<slug>'."""
    inner = token[1:-1]  # strip < >
    conv, _, name = inner.partition(":") if ":" in inner else ("", "", inner)
    if conv == "int":
        return "1"
    if name in ("cert_key",):
        return "foundation"
    if name in ("rest",) or conv == "path":
        return "missions"
    return "x"


def _fill(rule: str) -> str:
    return re.sub(r"<[^>]+>", lambda m: _dummy_for(m.group(0)), rule)


def _routes():
    """All (method, url, endpoint) tuples to smoke, derived from the url_map."""
    from apps.forge_academy.blueprint import bp

    app = Flask(__name__)
    app.register_blueprint(bp)
    out = []
    for rule in app.url_map.iter_rules():
        if rule.endpoint == "static":
            continue
        methods = (rule.methods or set()) - {"HEAD", "OPTIONS"}
        url = _fill(rule.rule)
        # Prefer GET when allowed; otherwise take the first declared method.
        method = "GET" if "GET" in methods else sorted(methods)[0]
        out.append((method, url, rule.endpoint))
    return out


_ROUTES = _routes()
_ACCEPTABLE = {200, 301, 302, 303, 304, 400, 401, 403, 404, 409, 422, 503}

# penta-fix-02: api_step_design_assess no longer 500s (verify_step args un-swapped
# + evidence-as-string handled), so the previous _KNOWN_BROKEN_500 xfail carve-out
# is gone — every route is held to the hard "no 5xx" contract.


@pytest.mark.parametrize(
    "method,url,endpoint",
    _ROUTES,
    ids=[f"{m}:{e}" for m, url, e in _ROUTES],
)
def test_route_never_500(academy, method, url, endpoint):
    client, _ = academy
    if method == "GET":
        resp = client.get(url)
    else:
        # POST/PUT etc: send an empty JSON body; input-validation 400s are fine.
        resp = client.open(url, method=method, json={})
    assert resp.status_code < 500, (
        f"{method} {url} ({endpoint}) returned {resp.status_code} "
        f"(body: {resp.get_data(as_text=True)[:300]})"
    )
    assert resp.status_code in _ACCEPTABLE or resp.status_code < 400, (
        f"{method} {url} ({endpoint}) unexpected status {resp.status_code}"
    )


# ---------------------------------------------------------------------------
# Explicit anchors: hub, missions listing, health
# ---------------------------------------------------------------------------

def test_hub_renders(academy):
    client, captures = academy
    resp = client.get("/academy")
    assert resp.status_code == 200
    templates = [t for t, _ in captures]
    assert "forge_academy/page.html" in templates


def test_missions_listing_shows_missions(academy):
    client, captures = academy
    captures.clear()
    # Browse as a SWE so the swe-tagged batch-1 missions are in range (the listing
    # narrows by the requested role).
    resp = client.get("/academy/missions?role=swe")
    assert resp.status_code == 200
    # The view passes the mission list into the template context; assert it is
    # non-empty and includes a known batch-1 (penta-aca-04) mission slug.
    ctx = next((c for t, c in captures if t == "forge_academy/missions.html"), None)
    assert ctx is not None, f"missions.html not rendered; captured: {[t for t, _ in captures]}"
    missions = ctx.get("missions") or ctx.get("all_missions") or []
    slugs = {m.get("slug") for m in missions}
    assert missions, "missions listing rendered with no missions"
    assert "m-cortex-01-unified-ai-layer" in slugs, (
        f"expected batch-1 mission in listing; got {sorted(s for s in slugs if s)[:10]}"
    )


def test_health_endpoint_ok(academy):
    client, _ = academy
    resp = client.get("/api/academy/health")
    assert resp.status_code == 200, resp.get_data(as_text=True)
    body = resp.get_json()
    assert body["initialized"] is True
    assert body["error"] is None
    assert body["mission_count"] > 0


def test_design_assess_positive_pass(academy, monkeypatch):
    """penta-fix-02: a crafted design-assess payload that verifies as passing
    returns 200 with the route's response contract. Guards the two merged bugs:
    the (user_id, step_type) arg order and evidence-as-string handling."""
    import apps.forge_academy.blueprint as bp_mod
    import apps.forge_academy.db as db_mod
    import apps.forge_academy.gamification as gam_mod
    import apps.forge_academy.verifier as verifier_mod

    client, _ = academy

    def _fake_verify(user_id, step_type, data):
        # The route MUST pass (user_id, step_type, data) in that order.
        assert step_type == "aadc_design_compliant", (user_id, step_type)
        assert data["design_id"] == "design-123"
        # evidence is a STRING (never a dict) — the contract the route must honor.
        return {"passed": True, "score": 95, "evidence": "Design passes: 95/100",
                "failed_checks": []}

    monkeypatch.setattr(verifier_mod, "verify_step", _fake_verify)
    monkeypatch.setattr(bp_mod, "_fa_user",
                        lambda: {"id": 1, "role": "admin", "email": "a@test.local"})
    monkeypatch.setattr(bp_mod, "_fa_email", lambda: "a@test.local")
    monkeypatch.setattr(db_mod, "complete_step", lambda *a, **k: None)
    monkeypatch.setattr(db_mod, "user_progress_summary", lambda *a, **k: {"steps_completed": 3})
    monkeypatch.setattr(gam_mod, "award_step_xp", lambda *a, **k: {"xp": 100, "achievements": []})
    monkeypatch.setattr(gam_mod, "check_step_achievements", lambda *a, **k: [])

    resp = client.post("/api/academy/step/design-assess", json={
        "step_id": 1,
        "design_id": "design-123",
        "required_checks": ["c1", "c2"],
        "min_score": 70,
    })
    assert resp.status_code == 200, resp.get_data(as_text=True)
    body = resp.get_json()
    assert body["passed"] is True
    assert body["score"] == 95
    assert body["checks_passed"] == ["c1", "c2"]  # required_checks minus failed ([])
    assert body["failed_checks"] == []
    assert body["evidence"] == "Design passes: 95/100"


def test_missions_listing_includes_batch2_when_present(academy):
    """Forward-compatible: if penta-aca-05 (batch 2) is merged, its missions show
    up too. Skips cleanly on origin/main where batch 2 is not yet merged."""
    from apps.forge_academy.content_loader import BUILTIN_MISSIONS

    slugs = {m["slug"] for m in BUILTIN_MISSIONS}
    if "m-foundry-01-capability-pipeline" not in slugs:
        pytest.skip("batch 2 (penta-aca-05) not present on this branch")
    client, captures = academy
    captures.clear()
    client.get("/academy/missions?role=swe")
    ctx = next((c for t, c in captures if t == "forge_academy/missions.html"), None)
    listed = {m.get("slug") for m in (ctx.get("missions") or ctx.get("all_missions") or [])}
    assert "m-foundry-01-capability-pipeline" in listed

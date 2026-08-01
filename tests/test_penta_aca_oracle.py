# CUI // SP-CTI
"""penta-aca-06 — FORGE Academy oracle wiring + RBAC parity + coach sanitization + fail-loud init.

Four independently-verifiable fixes:

  1. Oracle reflex wiring — the dead ``forge_academy_oracle`` reflex is deleted
     and replaced by ``academy_oracle_reflex``, which IS registered in
     ``REFLEX_NAMES`` + ``genesis_config.yaml`` and runs the in-app 7-lens
     ``AcademyOracleRunner``.
  2. RBAC parity — ``/academy/oracle`` + ``/academy/org-readiness`` (and their
     ``/api`` counterparts) are restricted to the org-leadership tier; a
     non-privileged authenticated user gets 403, an unauthenticated caller 401.
  3. Coach-hint sanitization — ``_md_to_html`` allowlist-sanitizes its output, so
     ``<script>`` in an LLM coach hint renders inert.
  4. Fail-loud init — a mission-seed failure logs at ERROR and sets a health flag
     instead of silently serving an empty catalog.
"""

from __future__ import annotations

import importlib
import logging
from pathlib import Path

import pytest
import yaml
from flask import Flask, g

REPO_ROOT = Path(__file__).resolve().parents[1]


# ===========================================================================
# Part 1 — Oracle reflex wiring
# ===========================================================================

def test_dead_reflex_deleted():
    assert not (REPO_ROOT / "tools" / "genesis" / "reflexes" / "forge_academy_oracle.py").exists()
    assert not (REPO_ROOT / "icdev" / "tools" / "genesis" / "reflexes" / "forge_academy_oracle.py").exists()


def test_new_reflex_registered_in_reflex_names():
    from tools.genesis.daemon import REFLEX_NAMES
    assert "academy_oracle_reflex" in REFLEX_NAMES
    # The dead one must not linger.
    assert "forge_academy_oracle" not in REFLEX_NAMES


def test_new_reflex_config_block_present_and_valid():
    cfg = yaml.safe_load((REPO_ROOT / "args" / "genesis_config.yaml").read_text(encoding="utf-8"))
    block = cfg.get("reflexes", {}).get("academy_oracle_reflex")
    assert isinstance(block, dict), "academy_oracle_reflex missing from genesis_config.yaml"
    required = {"enabled", "risk_tier", "schedule", "description", "success_metric"}
    assert not (required - set(block)), f"missing keys: {sorted(required - set(block))}"
    assert block["enabled"] is True
    assert block["schedule"] == "every 6h"


def test_new_reflex_importable_with_run():
    mod = importlib.import_module("tools.genesis.reflexes.academy_oracle_reflex")
    assert callable(getattr(mod, "run", None))


def test_runner_has_seven_lenses():
    from apps.forge_academy.oracle.runner import _LENSES
    assert len(_LENSES) == 7


def test_reflex_run_executes_seven_lenses_against_db():
    """run() drives the full 7-lens AcademyOracleRunner and persists without error."""
    from tools.genesis.reflexes.academy_oracle_reflex import run

    result = run({}, None)
    assert result["success"] is True, result.get("error")
    assert result["lenses_run"] == 7
    # Persisting is idempotent/deduped, so >= 0; the key point is no crash and
    # the fa_oracle_* tables were exercised.
    assert result["persisted"] >= 0
    assert "convergence_events" in result


def test_reflex_dry_run_does_not_persist():
    from tools.genesis.reflexes.academy_oracle_reflex import run

    result = run({"dry_run": True}, None)
    assert result["success"] is True
    assert result["dry_run"] is True
    assert result["lenses_run"] == 7
    assert result["persisted"] == 0


# ===========================================================================
# Part 2 — RBAC parity
# ===========================================================================

@pytest.fixture()
def academy_client():
    """Bare Flask app with the Academy blueprint + a settable g.current_user."""
    from apps.forge_academy.blueprint import bp

    app = Flask(__name__)
    app.config["TESTING"] = True
    holder: dict = {"user": None}

    @app.before_request
    def _set_user():
        g.current_user = holder["user"]

    app.register_blueprint(bp)
    return app.test_client(), holder


_PROTECTED_API = "/api/academy/oracle/predictions"
_PROTECTED_PAGES = ["/academy/oracle", "/academy/org-readiness"]


def test_api_denies_non_privileged_role(academy_client):
    client, holder = academy_client
    holder["user"] = {"id": "u1", "role": "developer"}
    assert client.get(_PROTECTED_API).status_code == 403


def test_api_denies_unauthenticated(academy_client):
    client, holder = academy_client
    holder["user"] = None
    assert client.get(_PROTECTED_API).status_code == 401


@pytest.mark.parametrize("path", _PROTECTED_PAGES)
def test_pages_deny_non_privileged_role(academy_client, path):
    client, holder = academy_client
    holder["user"] = {"id": "u1", "role": "co"}
    assert client.get(path).status_code == 403


def test_api_allows_admin(academy_client, monkeypatch):
    """Admin passes the gate; body isolated from DB so we test only the guard."""
    import apps.forge_academy.blueprint as bp_mod
    import apps.forge_academy.oracle.db as odb

    monkeypatch.setattr(bp_mod, "_ensure_init", lambda: None)
    monkeypatch.setattr(odb, "list_predictions", lambda **kw: [])

    client, holder = academy_client
    holder["user"] = {"id": "admin1", "role": "admin"}
    resp = client.get(_PROTECTED_API)
    assert resp.status_code == 200
    assert resp.get_json()["predictions"] == []


@pytest.mark.parametrize("role,allowed", [
    ("admin", True), ("pm", True), ("isso", True),
    ("developer", False), ("co", False), ("cor", False), ("", False),
])
def test_require_org_intel_role_matrix(role, allowed):
    """Unit-level allow/deny matrix for the org-intel decorator."""
    from apps.forge_academy.auth import require_org_intel

    app = Flask(__name__)

    @app.route("/probe")
    @require_org_intel
    def probe():
        return "ok"

    @app.before_request
    def _set():
        g.current_user = {"id": "x", "role": role}

    client = app.test_client()
    resp = client.get("/probe")
    if allowed:
        assert resp.status_code == 200
    else:
        assert resp.status_code == 403


# ===========================================================================
# Part 3 — Coach-hint HTML sanitization
# ===========================================================================

def test_md_to_html_neutralizes_script():
    from apps.forge_academy.content_loader import _md_to_html

    out = _md_to_html("Hello <script>alert('xss')</script> world")
    assert "<script>" not in out
    assert "&lt;script&gt;" in out  # escaped → inert
    assert "Hello" in out and "world" in out


def test_md_to_html_strips_javascript_href():
    from apps.forge_academy.content_loader import _md_to_html

    out = _md_to_html("[click](javascript:alert(1))")
    assert "javascript:" not in out


def test_md_to_html_keeps_legit_markdown():
    from apps.forge_academy.content_loader import _md_to_html

    out = _md_to_html("# Heading\n\n**bold** and `code`\n\n- item")
    assert "<strong>bold</strong>" in out
    assert "<code>code</code>" in out
    assert "<li>item</li>" in out


def test_coach_hint_route_returns_sanitized_html(monkeypatch):
    import apps.forge_academy.blueprint as bp_mod
    import apps.forge_academy.ai_coach as coach_mod

    monkeypatch.setattr(bp_mod, "_fa_user", lambda: {"id": 1, "role": "developer"})
    # No update_user_xp patch: the route used to deduct XP the moment a hint was
    # read, and aca-int-06 made the submit-time multiplier the single pricing
    # mechanism, so the name is no longer imported here. Patching it raised
    # AttributeError and failed this test for reasons having nothing to do with
    # sanitisation. Posting without a step_id keeps the DB out of the request.
    monkeypatch.setattr(coach_mod, "get_hint", lambda **kw: "Try <script>steal()</script> this")

    app = Flask(__name__)
    app.config["TESTING"] = True

    @app.before_request
    def _u():
        g.current_user = {"id": 1, "role": "developer"}

    app.register_blueprint(bp_mod.bp)
    resp = app.test_client().post("/api/academy/coach/hint", json={"question": "q"})
    assert resp.status_code == 200
    hint = resp.get_json()["hint"]
    assert "<script>" not in hint
    assert "&lt;script&gt;" in hint


# ===========================================================================
# Part 4 — Fail-loud mission-seed
# ===========================================================================

@pytest.fixture()
def reset_init():
    """Snapshot/restore the blueprint init globals around a test."""
    import apps.forge_academy.blueprint as bp_mod
    saved_flag = bp_mod._initialized
    saved_health = dict(bp_mod._INIT_HEALTH)
    bp_mod._initialized = False
    bp_mod._INIT_HEALTH.update({"initialized": False, "error": None, "mission_count": 0})
    yield bp_mod
    bp_mod._initialized = saved_flag
    bp_mod._INIT_HEALTH.clear()
    bp_mod._INIT_HEALTH.update(saved_health)


def test_seed_failure_logs_error_and_sets_flag(reset_init, monkeypatch, caplog):
    bp_mod = reset_init
    monkeypatch.setattr(bp_mod, "migrate", lambda: None)

    def _boom():
        raise RuntimeError("seed exploded")

    monkeypatch.setattr(bp_mod, "seed_mission_catalog", _boom)

    with caplog.at_level(logging.ERROR, logger="apps.forge_academy.blueprint"):
        bp_mod._ensure_init()

    assert any("init/seed failed" in r.message.lower() or "seed exploded" in r.getMessage()
               for r in caplog.records if r.levelno >= logging.ERROR)
    health = bp_mod.get_init_health()
    assert health["initialized"] is False
    assert health["error"]
    # Not marked initialized → next request retries instead of serving empty.
    assert bp_mod._initialized is False


def test_empty_catalog_after_seed_is_fatal(reset_init, monkeypatch, caplog):
    bp_mod = reset_init
    monkeypatch.setattr(bp_mod, "migrate", lambda: None)
    monkeypatch.setattr(bp_mod, "seed_mission_catalog", lambda: None)
    monkeypatch.setattr(bp_mod, "_mission_count", lambda: 0)

    with caplog.at_level(logging.ERROR, logger="apps.forge_academy.blueprint"):
        bp_mod._ensure_init()

    assert bp_mod.get_init_health()["initialized"] is False
    assert bp_mod.get_init_health()["error"]
    assert bp_mod._initialized is False


def test_successful_init_sets_healthy_flag(reset_init, monkeypatch):
    bp_mod = reset_init
    monkeypatch.setattr(bp_mod, "migrate", lambda: None)
    monkeypatch.setattr(bp_mod, "seed_mission_catalog", lambda: None)
    monkeypatch.setattr(bp_mod, "_mission_count", lambda: 12)

    bp_mod._ensure_init()

    health = bp_mod.get_init_health()
    assert health["initialized"] is True
    assert health["error"] is None
    assert health["mission_count"] == 12
    assert bp_mod._initialized is True

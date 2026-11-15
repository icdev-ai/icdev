# CUI // SP-CTI
"""Helpers for the ACE event log and dispatch tests (icx-ace-01).

The ``ace_env`` fixture (tests/ace/conftest.py) gives a test its own SQLite platform DB (the declared-step ledger and
the workflow engine, via ``wf_db``) and its own ACE canvas DB (``ace_events``,
``ace_event_results``, ``ace_dispatch_grants``), two roles of its own instead of
the shipped catalog, a NOVA trust table it controls, and a controller that
records launches instead of starting a team. Nothing else is faked: the event
log, the dispatcher, the grants and the declared steps all run for real.
"""
from __future__ import annotations

PM = {"id": "u-pm", "role": "pm"}
DEV = {"id": "u-dev", "role": "developer"}
VIEWER = {"id": "u-viewer", "role": "viewer"}
SERVICE = {"id": "svc-key", "role": "service"}

TOPIC = "task.completed"


def make_role(role_id: str, *, listen=(TOPIC,), auto=(), reason="routine reflection"):
    from icdev.tools.ace.role_loader import RoleTemplate

    data = {"role_id": role_id, "display_name": role_id.replace("_", " ").title(),
            "steps": ["analyze", "report"], "trust_tier": "yellow", "tool_permissions": [],
            "communication": {"listen_topics": list(listen), "emit_topics": ["status.updated"]}}
    if auto:
        data["auto_dispatch"] = {"eligible_topics": list(auto), "reason": reason}
    return RoleTemplate.from_dict(data)


def grant_auto(role_id: str, topic: str = TOPIC, *, actor=PM, max_per_day=5, days=7, now=None):
    """Turn auto on through the person's declared step, as the route does."""
    from datetime import datetime, timedelta, timezone

    from icdev.tools.ace import auto_dispatch
    from icdev.tools.workflow_hitl import declared

    now = now or datetime.now(timezone.utc)
    payload = auto_dispatch.check_grant(
        role_id, topic, reason="steady and reviewed",
        expires_at=(now + timedelta(days=days)).isoformat(), max_per_day=max_per_day, now=now)
    step = declared.take_step(auto_dispatch.TEMPLATE_ID, surface=auto_dispatch.GRANT_SURFACE,
                              decision="grant", subject=f"{role_id}:{topic}",
                              reason=payload["reason"], actor=actor, payload=payload)
    return auto_dispatch.record_grant(payload, actor=actor, step_id=step["step_id"])


def isolated_client(user, *blueprints):
    """A Flask client with the dashboard's rendering context and ``user`` signed in
    (the convention of tests/test_icx_cnf_07_page_decisions.py)."""
    from flask import Flask, g

    from tools.dashboard.app import app as _dashboard_app

    app = Flask(__name__, template_folder=_dashboard_app.template_folder,
                static_folder=_dashboard_app.static_folder)
    app.config.update(_dashboard_app.config)
    app.jinja_env.filters.update(_dashboard_app.jinja_env.filters)
    app.jinja_env.globals.update(_dashboard_app.jinja_env.globals)
    app.template_context_processors[None].extend(
        _dashboard_app.template_context_processors.get(None, []))
    app.secret_key = "test-secret"
    app.config["TESTING"] = True

    @app.before_request
    def _sign_in():
        g.current_user = dict(user) if user else None
        g.security_context = {"tenant_id": "default", "user_id": (user or {}).get("id", ""),
                              "classification": "CUI"}

    for bp in blueprints:
        app.register_blueprint(bp)
    return app.test_client()

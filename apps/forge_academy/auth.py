# CUI // SP-CTI
"""FORGE Academy — authorization helpers.

The Academy child app is registered on the main dashboard Flask app, whose
``_auth_before_request`` hook (tools/dashboard/auth.py) populates
``flask.g.current_user`` for authenticated callers. Most Academy surfaces are
per-user (a learner sees only their own XP/missions) and stay open to any
authenticated user.

Two surfaces are different: the Oracle (``/academy/oracle`` + ``/api/academy/oracle/*``)
and Org Readiness (``/academy/org-readiness`` + ``/api/academy/org-readiness``)
expose ORG-WIDE intelligence — cohort risk, skill gaps, readiness scores across
every learner. Those must be restricted to the org-leadership tier, mirroring
how sibling canvases gate org-wide views. ``require_org_intel`` enforces that.
"""

from __future__ import annotations

from functools import wraps

from flask import abort, g, redirect, request, url_for

# Org-leadership tier permitted to view org-wide Academy intelligence. Values are
# a subset of tools/dashboard/auth.py::VALID_DASHBOARD_ROLES. "admin" always
# passes. Non-privileged roles (developer, co, cor, …) are denied.
ORG_INTEL_ROLES = frozenset({"admin", "pm", "isso"})


def _current_user() -> object | None:
    return getattr(g, "current_user", None)


def _user_role(user: object) -> str:
    if isinstance(user, dict):
        return str(user.get("role") or "")
    return str(getattr(user, "role", "") or "")


def _deny_unauthenticated():
    """401 for API/JSON callers; redirect to login for browser page requests."""
    if request.is_json or request.path.startswith("/api/"):
        abort(401)
    try:
        return redirect(url_for("login_page"))
    except Exception:
        # login endpoint not registered (e.g. blueprint mounted standalone in a
        # test harness) — fall back to a hard 401 rather than raising BuildError.
        abort(401)


def require_org_intel(view):
    """Restrict a route to the org-leadership tier (admin/pm/isso).

    Unauthenticated → 401 (API) or login redirect (page). Authenticated but
    outside ORG_INTEL_ROLES → 403. Mirrors the dashboard g.current_user auth
    contract so it works both under the global dashboard hook and when the
    blueprint is mounted standalone in tests.
    """

    @wraps(view)
    def wrapper(*args, **kwargs):
        user = _current_user()
        if user is None:
            return _deny_unauthenticated()
        if _user_role(user) not in ORG_INTEL_ROLES:
            abort(403)
        return view(*args, **kwargs)

    return wrapper

# CUI // SP-CTI
"""AI GameDay — authentication + authorization helpers.

The AI GameDay child app is registered on the main dashboard Flask app, whose
``_auth_before_request`` hook (tools/dashboard/auth.py) populates
``flask.g.current_user`` for authenticated callers. The decorators below mirror
that model so every GameDay route enforces authentication explicitly — this
matters for defense-in-depth and for test harnesses that mount the blueprint
without the global dashboard hook.

- ``login_required`` — any authenticated user (player, facilitator, admin).
- ``require_facilitator`` — authenticated AND satisfies the component
  registry's ``min_il`` / ``default_roles`` policy for the ``gameday`` entry
  (args/component_registry.yaml). Applied to facilitator/admin operations
  (session lifecycle, inject dispatch, scenario/template authoring, AI League
  start).
"""

from __future__ import annotations

from functools import wraps

from flask import abort, g, redirect, request, url_for


# ---------------------------------------------------------------------------
# Authentication / authorization decorators (dashboard g.current_user model)
# ---------------------------------------------------------------------------

# IL dominance order — mirrors tools/security/canvas_access.py::_IL_ORDER.
_IL_ORDER = {"IL2": 2, "IL4": 4, "IL5": 5, "IL6": 6}


def _current_user() -> object | None:
    return getattr(g, "current_user", None)


def _deny():
    """401 for API/JSON callers, redirect to login for browser page requests."""
    if request.is_json or request.path.startswith("/api/"):
        abort(401)
    try:
        return redirect(url_for("login_page"))
    except Exception:
        # login endpoint not registered (e.g. blueprint mounted standalone) —
        # fall back to a hard 401 rather than raising a BuildError.
        abort(401)


def _user_attr(user: object, key: str) -> object | None:
    if isinstance(user, dict):
        return user.get(key)
    return getattr(user, key, None)


def login_required(view):
    """Require any authenticated user. Mirrors the dashboard auth contract."""

    @wraps(view)
    def wrapper(*args, **kwargs):
        if _current_user() is None:
            return _deny()
        return view(*args, **kwargs)

    return wrapper


def _gameday_access_policy() -> tuple[str, list[str]]:
    """Return (min_il, roles) from the component registry ``gameday`` entry.

    Falls back to the registry defaults documented in
    args/component_registry.yaml (min_il=IL4, default_roles=[]) when the
    registry is unavailable.
    """
    try:
        from tools.config.component_registry import get_registry

        comp = get_registry().get("gameday")
        if comp is not None:
            return (
                str(getattr(comp, "min_il", "IL4") or "IL4"),
                list(getattr(comp, "default_roles", []) or []),
            )
    except Exception:
        pass
    return ("IL4", [])


def _il_satisfies(min_il: str, user_il: object) -> bool:
    """True when the user's impact level dominates ``min_il``.

    When the authenticated user carries no impact-level attribute we do not
    block (authentication is already enforced), matching the lenient posture of
    tools/security/canvas_access.py::guard_component_access.
    """
    if not user_il:
        return True
    return _IL_ORDER.get(str(user_il).upper(), 0) >= _IL_ORDER.get(str(min_il).upper(), 4)


def require_facilitator(view):
    """Authenticated + registry min_il/roles gate for facilitator/admin ops."""

    @wraps(view)
    def wrapper(*args, **kwargs):
        user = _current_user()
        if user is None:
            return _deny()
        min_il, roles = _gameday_access_policy()
        if not _il_satisfies(min_il, _user_attr(user, "impact_level")):
            abort(403)
        role = str(_user_attr(user, "role") or "")
        if roles and role != "admin" and role not in roles:
            abort(403)
        return view(*args, **kwargs)

    return wrapper

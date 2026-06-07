# [TEMPLATE: CUI // SP-CTI]
"""Dashboard middleware — whole-page CLI bridge toggle.

Reads a per-page preference for the local Claude Code CLI bridge from either
the ``icdev_cli_bridge`` cookie or the ``X-ICDEV-CLI-Bridge`` header and seeds
the router's context-scoped override (``cli_bridge_override``) for the entire
request lifetime. Every ``LLMRouter.invoke()`` that fires while rendering the
page — across any canvas AI engine endpoint — then honors the toggle, even when
``ICDEV_CLI_BRIDGE=1`` is set globally.

The override is reset in ``teardown_request`` so request-scoped state never
leaks into the next request served by the same worker thread.

Accepted values (case-insensitive):
    on / true / 1 / yes  → force-enable the bridge
    off / false / 0 / no → force-disable (bypass) the bridge
    anything else / absent → no override (defer to env + auto-detect)

Usage:
    from tools.dashboard.api.cli_bridge_api import register_cli_bridge
    register_cli_bridge(app)
"""

from typing import Optional

from tools.logging.icdev_logger import get_logger

logger = get_logger("icdev.dashboard.cli_bridge")

# Cookie + header the front-end sets to express the per-page toggle.
COOKIE_NAME = "icdev_cli_bridge"
HEADER_NAME = "X-ICDEV-CLI-Bridge"

_TRUTHY = ("true", "1", "yes", "on")
_FALSEY = ("false", "0", "no", "off")

# Key under which the reset token is stashed on flask.g for teardown.
_G_TOKEN_KEY = "_cli_bridge_override_token"


def parse_toggle(value: Optional[str]) -> Optional[bool]:
    """Map a raw cookie/header string to an override tri-state.

    Returns ``True`` / ``False`` for recognized values, ``None`` otherwise
    (unset, empty, or unrecognized → no override).
    """
    if value is None:
        return None
    normalized = value.strip().lower()
    if normalized in _TRUTHY:
        return True
    if normalized in _FALSEY:
        return False
    return None


def register_cli_bridge(app) -> None:
    """Register the whole-page CLI bridge toggle middleware on a Flask app.

    Seeds the router override in ``before_request`` (header takes precedence
    over cookie) and clears it in ``teardown_request``.
    """
    from flask import g, jsonify, request

    from tools.llm.cli_bridge.activate import (
        cli_bridge_override,
        reset_cli_bridge_override,
    )

    @app.before_request
    def _seed_cli_bridge_override():  # noqa: ANN202
        # Header wins over cookie so an explicit per-request API call can
        # override the page-level cookie preference.
        raw = request.headers.get(HEADER_NAME)
        if raw is None:
            raw = request.cookies.get(COOKIE_NAME)
        value = parse_toggle(raw)
        if value is None:
            return  # no override — leave the ContextVar at its default
        g.__dict__[_G_TOKEN_KEY] = cli_bridge_override(value)
        logger.debug("CLI bridge override seeded for request: %s", value)

    @app.teardown_request
    def _reset_cli_bridge_override(exc=None):  # noqa: ANN001, ANN202
        token = g.__dict__.pop(_G_TOKEN_KEY, None)
        if token is not None:
            reset_cli_bridge_override(token)

    @app.route("/api/cli-bridge/status", methods=["GET"])
    def _cli_bridge_status():  # noqa: ANN202
        return jsonify(cli_bridge_status())


def _last_provider_served():
    """Return (provider, model_id, logged_at) of the most recent AI interaction.

    Reads one row from the append-only ``ai_telemetry`` table. Best-effort and
    never raises — any failure (missing table, no DB, empty) returns all-None so
    the status pill simply shows no last-provider label.
    """
    try:
        from tools.db.storage import get_connection

        conn = get_connection()
        try:
            cur = conn.execute(
                "SELECT provider, model_id, logged_at FROM ai_telemetry "
                "ORDER BY logged_at DESC LIMIT 1"
            )
            row = cur.fetchone()
        finally:
            conn.close()
        if not row:
            return None, None, None
        # Row may be a tuple or a mapping depending on backend/cursor factory.
        try:
            return row[0], row[1], row[2]
        except (KeyError, TypeError):
            return row["provider"], row["model_id"], row["logged_at"]
    except Exception:  # pragma: no cover - telemetry is observability-only
        return None, None, None


def cli_bridge_status() -> dict:
    """Build the status payload for the navbar CLI-bridge indicator.

    ``enabled`` honors the per-request context override (the cookie/header seeded
    by :func:`register_cli_bridge`'s ``before_request`` hook), so polling this
    endpoint after the per-page toggle flips reflects the new effective state.

    Dot ``state``:
        ``active``  → bridge enabled AND the CLI is headless-capable (green)
        ``missing`` → bridge enabled but the CLI binary is not resolvable (amber)
        ``off``     → bridge disabled for this page (grey)
    """
    try:
        from tools.llm.cli_bridge.activate import cli_bridge_enabled

        enabled = bool(cli_bridge_enabled())
    except Exception:  # pragma: no cover - defensive
        enabled = False

    try:
        from tools.llm.cli_bridge.capability import is_cli_headless_capable

        available = bool(is_cli_headless_capable())
    except Exception:  # pragma: no cover - defensive
        available = False

    if not enabled:
        state = "off"
    elif available:
        state = "active"
    else:
        state = "missing"

    provider, model_id, logged_at = _last_provider_served()

    import os

    return {
        "enabled": enabled,
        "available": available,
        "state": state,
        "env": os.environ.get("ICDEV_CLI_BRIDGE", ""),
        "cookie_name": COOKIE_NAME,
        "last_provider": provider,
        "last_model": model_id,
        "last_served_at": logged_at,
    }

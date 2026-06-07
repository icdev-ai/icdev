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
    from flask import g, request

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

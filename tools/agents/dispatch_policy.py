# CUI // SP-CTI
"""Refuse to dispatch an adapter whose Claude route would be metered (omx-guard-07-d3).

Under ``anthropic_billing: subscription_only`` (``tools/llm/anthropic_billing.py``)
the runner may only spawn an adapter whose declared ``claude_route``
(omx-adapt-04, ``args/agent_capabilities.yaml``) is one of :data:`ALLOWED_ROUTES`:

* ``native_subscription`` -- the backend is Claude Code on its claude.ai login;
* ``cli_bridge`` -- the harness reaches Claude through ``icdev claude-bridge``;
* ``none`` -- the backend has no Claude route, so it cannot bill Claude at all
  (the same reading ``capability_matrix._probe_claude_route`` takes).

Any other value -- ``api_key``, ``metered``, a typo -- is refused: an
unrecognised route is not a subscription route. An adapter object may carry
its own ``claude_route`` attribute, which wins over the YAML claim.
"""
from __future__ import annotations

from typing import Any, Optional

from tools.logging.icdev_logger import get_logger

logger = get_logger(__name__)

ALLOWED_ROUTES = ("native_subscription", "cli_bridge", "none")


class DispatchRefused(RuntimeError):
    """The billing policy forbids spawning this adapter."""


def adapter_route(adapter: Any) -> str:
    """The adapter's ``claude_route``: its own attribute, else the declared claim."""
    own = getattr(adapter, "claude_route", None)
    if isinstance(own, str):
        return str(own).strip().lower() or "none"
    from tools.agents import capability_matrix  # noqa: PLC0415 -- imports registry

    return capability_matrix.declared_route(getattr(adapter, "name", ""))


def refusal_reason(adapter: Any, mode: Optional[str] = None) -> str:
    """Why *adapter* may not be dispatched under the billing policy, or ``""``."""
    from tools.llm import anthropic_billing  # noqa: PLC0415

    mode = mode or anthropic_billing.billing_mode()
    if mode != anthropic_billing.SUBSCRIPTION_ONLY:
        return ""
    route = adapter_route(adapter)
    if route in ALLOWED_ROUTES:
        return ""
    return (f"anthropic_billing=subscription_only refuses {getattr(adapter, 'name', '?')}: "
            f"claude_route={route!r} is metered (allowed: {list(ALLOWED_ROUTES)})")


def enforce(adapter: Any, task_id: str = "", mode: Optional[str] = None) -> None:
    """Raise :class:`DispatchRefused` (after logging why) for a metered route."""
    reason = refusal_reason(adapter, mode)
    if reason:
        logger.error("dispatch refused%s: %s", f" for {task_id}" if task_id else "", reason)
        raise DispatchRefused(reason)

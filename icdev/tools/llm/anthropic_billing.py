# CUI // SP-CTI
"""Anthropic billing policy (omx-guard-07) -- may anything pay for Claude by the token?

The operator decided (2026-10-11) that every Claude call ICDEV makes, or that a
harness it launches makes, is paid by the claude.ai SUBSCRIPTION: natively, or
through the local ``claude`` CLI bridge. A metered Anthropic API key is never
used. This module is the ONE reader of that declared setting,
``args/guard_anthropic.yaml``; the seams that enforce it (spawn-env stripping,
dispatch refusal, router resolution, the doctor check) ask :func:`billing_mode`
rather than parsing the file themselves.

The policy fails CLOSED: a missing or unreadable file, a missing key or a value
outside :data:`VALID_MODES` resolves to :data:`SUBSCRIPTION_ONLY`, and the
reason is logged and returned in ``warnings``.

    icdev config get anthropic_billing
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from icdev.core.paths import config_path

KEY = "anthropic_billing"
SUBSCRIPTION_ONLY = "subscription_only"
ALLOW_METERED = "allow_metered"
VALID_MODES = (SUBSCRIPTION_ONLY, ALLOW_METERED)
DEFAULT_MODE = SUBSCRIPTION_ONLY

POLICY_RELPATH = "args/guard_anthropic.yaml"


def policy_path() -> Path:
    return config_path(POLICY_RELPATH, anchor=__file__)


def load_policy(path: Optional[Path] = None) -> Dict[str, Any]:
    """The validated policy: ``{"anthropic_billing", "source", "warnings"}``.

    ``source`` is the file the value came from, or ``"default"`` when the
    default was used.
    """
    path = path or policy_path()
    warnings: List[str] = []
    data: Any = {}
    try:
        import yaml  # noqa: PLC0415

        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception as exc:  # noqa: BLE001 -- the default is the documented policy
        warnings.append(f"{path.name} unreadable ({type(exc).__name__}); using {DEFAULT_MODE}")
        data = {}
    if not isinstance(data, dict):
        warnings.append(f"{path.name} is not a mapping; using {DEFAULT_MODE}")
        data = {}

    mode = DEFAULT_MODE
    source = "default"
    raw = data.get(KEY)
    if raw is None:
        if data:
            warnings.append(f"{KEY} not set in {path.name}; using {DEFAULT_MODE}")
    elif isinstance(raw, str) and raw.strip().lower() in VALID_MODES:
        mode = raw.strip().lower()
        source = str(path)
    else:
        warnings.append(f"ignored {KEY}={raw!r}: valid values are {list(VALID_MODES)}; "
                        f"using {DEFAULT_MODE}")

    if warnings:
        from tools.logging.icdev_logger import get_logger  # noqa: PLC0415

        log = get_logger(__name__)
        for warning in warnings:
            log.warning("anthropic billing policy: %s", warning)
    return {KEY: mode, "source": source, "warnings": warnings}


def billing_mode(path: Optional[Path] = None) -> str:
    return load_policy(path)[KEY]


def subscription_only(path: Optional[Path] = None) -> bool:
    """True unless the operator explicitly declared ``allow_metered``."""
    return billing_mode(path) == SUBSCRIPTION_ONLY

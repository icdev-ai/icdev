# CUI // SP-CTI
"""Notification routing-rules engine (crx-not-01).

Evaluates ``(severity x component x tenant) -> channels`` routing rules at
SEND time. Rules live in ``args/notification_routing.yaml`` so behaviour can be
changed without touching code (FORGE Args layer).

This is the small, stable contract consumed by crx-gen-02 and DMX
(dmx-loop-01). The public surface is intentionally tiny:

    resolve_channels(severity, component=None, tenant_id=None, default=None) -> list[str]
    load_rules(path=None) -> dict          # (mostly for tests / introspection)

Channel names returned here are the adapter keys understood by
``tools/notifications/gateway.py`` (slack, teams, email, telegram, webhook...).
Resolution never delivers anything itself — callers pass the result to the
gateway (or their own dispatcher).
"""

from __future__ import annotations

import pathlib
from typing import Any, Iterable

import yaml

# Repo-root/args resolution: this file lives at <root>/tools/notifications/,
# so parent.parent.parent == <root>. Works for both the canonical tools/ tree
# and the mirrored icdev/tools/ tree (each resolves to its own args/).
_CONFIG_PATH = (
    pathlib.Path(__file__).resolve().parent.parent.parent / "args" / "notification_routing.yaml"
)

_WILDCARD = "*"


def _config_path() -> pathlib.Path:
    return _CONFIG_PATH


def load_rules(path: str | pathlib.Path | None = None) -> dict:
    """Load and return the routing config dict; ``{}`` on any failure.

    Kept side-effect free and re-read on each call so operators can edit the
    YAML without a process restart. Callers that need caching should wrap this.
    """
    p = pathlib.Path(path) if path else _config_path()
    try:
        with open(p, encoding="utf-8") as fh:
            return yaml.safe_load(fh) or {}
    except Exception:
        return {}


def _as_set(value: Any) -> set[str] | None:
    """Normalize a rule dimension to a lowercase set, or ``None`` for wildcard.

    A dimension that is omitted, ``None``, ``"*"``, or ``["*"]`` matches
    anything and is represented as ``None``.
    """
    if value is None:
        return None
    if isinstance(value, str):
        if value.strip() == _WILDCARD or value.strip() == "":
            return None
        return {value.strip().lower()}
    if isinstance(value, Iterable):
        items = {str(v).strip().lower() for v in value if str(v).strip()}
        if not items or _WILDCARD in items:
            return None
        return items
    return {str(value).strip().lower()}


def _dimension_matches(rule_value: Any, send_value: str | None) -> bool:
    """True when a single rule dimension matches the send-time value."""
    allowed = _as_set(rule_value)
    if allowed is None:  # wildcard / unspecified
        return True
    if send_value is None:
        return False
    return str(send_value).strip().lower() in allowed


def resolve_channels(
    severity: str | None,
    component: str | None = None,
    tenant_id: str | None = None,
    default: list[str] | None = None,
    config: dict | None = None,
) -> list[str]:
    """Resolve the ordered, de-duplicated channel list for a notification.

    Args:
        severity:  Alert severity (e.g. ``critical``/``high``/``medium``/``info``).
        component: Originating component/domain (e.g. ``security``, ``kanban``).
        tenant_id: Tenant slug/id for tenant-scoped routing.
        default:   Explicit fallback channels; overrides ``default_channels``
                   from the YAML when provided.
        config:    Pre-loaded config dict (skips disk read); mainly for tests.

    Returns:
        Channel names in rule order, first-seen wins, duplicates removed. When
        no rule matches, the YAML ``default_channels`` (or ``default`` arg) is
        returned. Never raises — resolution failures degrade to the fallback.
    """
    cfg = config if config is not None else load_rules()
    rules = cfg.get("rules") or []

    ordered: list[str] = []
    seen: set[str] = set()

    for rule in rules:
        if not isinstance(rule, dict):
            continue
        if not (
            _dimension_matches(rule.get("severity"), severity)
            and _dimension_matches(rule.get("component"), component)
            and _dimension_matches(rule.get("tenant"), tenant_id)
        ):
            continue
        for ch in rule.get("channels") or []:
            key = str(ch).strip()
            if key and key.lower() not in seen:
                seen.add(key.lower())
                ordered.append(key)

    if ordered:
        return ordered

    fallback = default if default is not None else (cfg.get("default_channels") or [])
    out: list[str] = []
    seen.clear()
    for ch in fallback:
        key = str(ch).strip()
        if key and key.lower() not in seen:
            seen.add(key.lower())
            out.append(key)
    return out

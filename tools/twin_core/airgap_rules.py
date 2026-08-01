# CUI // SP-CTI — Air-gap validation rules for twin simulations (twx-fed-01)
"""Shared air-gap validation rules consumed by canvas twins (NDC/IDC/PDC).

Sequoia/SHIFT **Pattern 2**: a twin simulation targeting a classified /
air-gapped environment must confirm the design has NO public-internet
dependency — no public egress, no external API deps, packages only from internal
mirrors, container images only from an internal registry. Each violation is
emitted with severity ``deployment_blocker`` (normalizes to the canonical
``blocker``) in the twx-core-01 :mod:`tools.twin_core.schema`.

Design: **config-driven** (``args/twin_airgap_rules.yaml``) and **generic** over
any target shape. The evaluator harvests every string value from a design graph
or an IaC plan, extracts host / image / package / egress references, and matches
them against the per-rule denied lists (internal allowlist wins). This one module
serves NDC intent validation, IDC's pre-apply gate, and the PDC pre-merge twin.

``query-as-compliance false-confidence`` guard: rules are **deny-by-match**, not
allow-by-absence — a design with zero references legitimately trips nothing, but
the negative-test fixtures assert each rule DOES fire on a known-bad design, so a
silently-broken matcher cannot masquerade as "compliant".
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Iterable

from tools.logging.icdev_logger import get_logger
from tools.twin_core.schema import canonical_violation

logger = get_logger("icdev.twin_core.airgap")

_DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[2] / "args" / "twin_airgap_rules.yaml"
_CONFIG_CACHE: dict | None = None

# Host-ish token: registry/index/api hostnames and image refs (host[:port]/path).
_HOST_RE = re.compile(r"(?:https?://)?([a-z0-9.\-]+\.[a-z]{2,}(?::\d+)?)", re.IGNORECASE)


def load_rules(path: str | Path | None = None, *, force: bool = False) -> dict:
    """Load (and cache) the air-gap rule config. Missing file → disabled config."""
    global _CONFIG_CACHE
    if _CONFIG_CACHE is not None and path is None and not force:
        return _CONFIG_CACHE
    cfg_path = Path(path) if path else _DEFAULT_CONFIG_PATH
    try:
        import yaml

        with open(cfg_path, encoding="utf-8") as fh:
            cfg = yaml.safe_load(fh) or {}
    except FileNotFoundError:
        logger.warning("air-gap rule config not found at %s — rules disabled", cfg_path)
        cfg = {"enabled": False, "rules": []}
    except Exception as exc:  # noqa: BLE001
        logger.error("failed to load air-gap rules (%s) — rules disabled", exc)
        cfg = {"enabled": False, "rules": []}
    if path is None:
        _CONFIG_CACHE = cfg
    return cfg


def _iter_strings(obj: Any) -> Iterable[str]:
    """Recursively yield every string value in a nested dict/list structure."""
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for v in obj.values():
            yield from _iter_strings(v)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            yield from _iter_strings(v)


def _is_allowlisted(value: str, allow_suffixes: list[str]) -> bool:
    low = value.lower()
    return any(sfx.lower() in low for sfx in allow_suffixes)


def _extract_hosts(text: str) -> set[str]:
    return {m.group(1).lower() for m in _HOST_RE.finditer(text)}


def evaluate_airgap(
    target: Any,
    *,
    source_canvas: str | None = None,
    config: dict | None = None,
    active: bool | None = None,
) -> list[dict]:
    """Return canonical air-gap violations for ``target`` (a graph or IaC plan).

    Args:
        target: any nested structure (design graph, plan_json, resource list).
        source_canvas: canvas key stamped onto each violation's provenance.
        config: pre-loaded rule config (defaults to args/twin_airgap_rules.yaml).
        active: force-enable/disable regardless of environment. When ``None``,
            rules run whenever the config's ``enabled`` is true (evaluation is
            environment-agnostic — a twin can validate an air-gap TARGET even
            from a connected build host; use :func:`is_airgap_environment` to
            gate on the actual runtime).

    Every violation carries ``severity='deployment_blocker'`` (→ ``blocker``) and
    ``method='airgap-rule'``.
    """
    cfg = config if config is not None else load_rules()
    if active is False or (active is None and not cfg.get("enabled", False)):
        return []
    severity = cfg.get("severity", "deployment_blocker")
    allow_suffixes = (cfg.get("allowlist", {}) or {}).get("internal_host_suffixes", []) or []

    strings = list(_iter_strings(target))
    hosts_seen: set[str] = set()
    for s in strings:
        hosts_seen |= _extract_hosts(s)

    violations: list[dict] = []
    seen_keys: set[tuple] = set()

    for rule in cfg.get("rules", []) or []:
        rid = rule.get("id", "airgap-rule")
        category = rule.get("category", "security")
        recommendation = rule.get("recommendation", "Remove the public dependency for air-gapped deployment.")

        # 1. Host-suffix denials (registries, package indexes, external APIs).
        for host in sorted(hosts_seen):
            if _is_allowlisted(host, allow_suffixes):
                continue
            for suffix in rule.get("denied_host_suffixes", []) or []:
                if host == suffix.lower() or host.endswith("." + suffix.lower()) or host.endswith(suffix.lower()):
                    key = (rid, "host", host)
                    if key in seen_keys:
                        continue
                    seen_keys.add(key)
                    violations.append(canonical_violation(
                        severity, category,
                        recommendation,
                        title=f"Public dependency '{host}' violates {rid}",
                        rule_id=rid, source_canvas=source_canvas, method="airgap-rule",
                        detail=host,
                    ))
                    break

        # 2. Literal egress markers (0.0.0.0/0, internet, igw, ...).
        for marker in rule.get("denied_markers", []) or []:
            m_low = marker.lower()
            hit = next((s for s in strings if m_low in s.lower()), None)
            if hit is not None:
                key = (rid, "marker", m_low)
                if key in seen_keys:
                    continue
                seen_keys.add(key)
                violations.append(canonical_violation(
                    severity, category,
                    recommendation,
                    title=f"Public-egress marker '{marker}' violates {rid}",
                    rule_id=rid, source_canvas=source_canvas, method="airgap-rule",
                    detail=marker,
                ))

    return violations


def is_airgap_environment() -> bool:
    """True when the RUNTIME is detected as air-gapped (tools/airgap detector).

    Callers gate whether to enforce (vs merely report) air-gap blockers on the
    real environment; ``evaluate_airgap`` itself is environment-agnostic so a
    twin can validate an air-gap *target* from a connected host.
    """
    try:
        from tools.airgap.detector import is_airgap

        return bool(is_airgap())
    except Exception:  # noqa: BLE001
        return False

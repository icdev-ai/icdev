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


def _emulator_runtime_image_violations(
    target: Any,
    rule: dict,
    *,
    severity: str,
    source_canvas: str | None,
    runtime_image_probe: Any = None,
    registry_config: Any = None,
) -> list[dict]:
    """Blockers for emulator base images that would be PULLED at run time (flx-airgap-02).

    THE DEFECT NO STRING MATCHER CAN SEE. Every other rule here is
    deny-by-match over text the target CONTAINS. floci's container-backed
    services (Lambda, RDS, ElastiCache, OpenSearch, MSK, ECS/EC2/EKS) do not
    carry their runtimes inside the floci image — each starts a separate
    container from a base image floci resolves at RUN TIME, on first use, from
    the public internet. A config declaring a Lambda contains no image
    reference at all, so there is no string to deny. This derives the required
    set from the declared services instead and asks whether the local cache
    already holds it.

    THE FOUR OUTCOMES ARE KEPT APART, because they need different repairs:

      ``blocked``        a required image is PROVEN absent (or present at a
                         different digest). A run-time pull WILL be attempted.
                         Emitted at the config's ``severity``
                         (``deployment_blocker`` -> ``blocker``). THE FINDING.
      ``indeterminate``  a container-backed service is declared whose variant
                         could not be resolved — a Lambda naming no runtime, an
                         RDS naming no engine. We cannot say which image it
                         needs, and guessing fabricates either a blocker or a
                         clean bill. Emitted at ``unmeasured_severity``.
      ``unmeasured``     the daemon could not be asked at all. PROVES NOTHING,
                         so it must not block every CI runner and reviewer
                         laptop — that is how a gate earns a ``|| true``.
                         Emitted at ``unmeasured_severity``, never dropped.
      ``satisfied``      every required image is already cached. NO violation,
                         which is the negative direction the tests assert.

    ``runtime_image_probe`` is injectable so a caller (and a test) can state a
    cache rather than depend on whatever this host happens to hold.
    """
    rid = rule.get("id", "airgap-emulator-runtime-images")
    category = rule.get("category", "security")
    recommendation = rule.get(
        "recommendation",
        "Pre-populate the local image cache before disconnecting the host.",
    )
    unmeasured_severity = rule.get("unmeasured_severity", "medium")

    try:
        from tools.cloud import runtime_images
    except Exception as exc:  # noqa: BLE001
        logger.warning("runtime-image declaration unavailable (%s) — reporting unmeasured", exc)
        return [canonical_violation(
            unmeasured_severity, category, recommendation,
            title=f"Emulator runtime images could not be evaluated ({rid})",
            rule_id=f"{rid}-unmeasured", source_canvas=source_canvas, method="airgap-rule",
            detail=f"runtime_images module unavailable: {exc}",
        )]

    report = runtime_images.evaluate(
        target, prober=runtime_image_probe, registry_config=registry_config
    )
    state = report.get("state")

    if state == runtime_images.STATE_SATISFIED:
        return []

    if state == runtime_images.STATE_BLOCKED:
        out = []
        for row in list(report.get("missing", [])) + list(report.get("mismatched", [])):
            out.append(canonical_violation(
                severity, category, recommendation,
                title=(
                    f"Emulator base image '{row['ref']}' is not cached locally and "
                    f"would be PULLED at run time, violating {rid}"
                ),
                rule_id=rid, source_canvas=source_canvas, method="airgap-rule",
                detail=f"{row['ref']} ({row['state']}: {row.get('basis')})",
            ))
        return out

    if state == runtime_images.STATE_INDETERMINATE:
        return [canonical_violation(
            unmeasured_severity, category, recommendation,
            title=(
                f"Cannot determine which base image these declare "
                f"({', '.join(report.get('variant_undetermined', []))}) — {rid}"
            ),
            rule_id=f"{rid}-unmeasured", source_canvas=source_canvas, method="airgap-rule",
            detail=report.get("reason"),
        )]

    # unmeasured — never a clean bill of health, and never a blocker either.
    return [canonical_violation(
        unmeasured_severity, category, recommendation,
        title=f"Local image cache could not be read — {rid} is UNMEASURED, not clean",
        rule_id=f"{rid}-unmeasured", source_canvas=source_canvas, method="airgap-rule",
        detail=report.get("reason"),
    )]


def evaluate_airgap(
    target: Any,
    *,
    source_canvas: str | None = None,
    config: dict | None = None,
    active: bool | None = None,
    runtime_image_probe: Any = None,
    registry_config: Any = None,
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
        runtime_image_probe: injectable per-image cache prober for the
            ``emulator_runtime_images`` rule (flx-airgap-02). ``None`` asks the
            local docker daemon. Every OTHER rule here is a pure string match
            over ``target`` and is unaffected by it.
        registry_config: injectable registry declaration for the same rule
            (flx-airgap-03). ``None`` reads ``args/floci_registry.yaml``. An
            uncached image served by an INTERNAL mirror pulls internally and is
            not an air-gap violation; one with no mirror, or a mirror this
            system's own allowlist does not call internal, still is. Same rule,
            same meaning of "run-time pull" — see
            :func:`tools.cloud.floci_registry.pull_origin`.

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

        # 0. Derive-then-check: an emulator dependency that is ABSENT from the
        #    config. See _emulator_runtime_image_violations for why no string
        #    matcher can see it. This rule contributes no host/marker matching.
        if rule.get("emulator_runtime_images"):
            violations.extend(
                _emulator_runtime_image_violations(
                    target,
                    rule,
                    severity=severity,
                    source_canvas=source_canvas,
                    runtime_image_probe=runtime_image_probe,
                    registry_config=registry_config,
                )
            )
            continue

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

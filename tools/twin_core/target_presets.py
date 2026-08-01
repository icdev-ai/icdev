# CUI // SP-CTI — Twin target-environment presets + service-parity evaluator (twx-fed-02)
"""Run a twin simulation *against a target environment* (Sequoia Patterns 1+5).

A **preset** (``args/twin_target_presets.yaml``) bundles a service catalog +
CSP/region/classification + compliance framework + network constraints. Given a
design (graph or IaC plan) and a preset, :func:`evaluate_target`:

1. flags every referenced CSP service that is **not available in the target**
   (region scope / classification) — canonical ``service_parity`` violations;
2. engages the twx-fed-01 air-gap rules when the preset's
   ``network_constraints.airgap`` is true;
3. **staleness guard** (docs risk #1): warns when the preset or the service
   catalog was reviewed more than ``staleness_warn_days`` (default 180) ago.

EXTENDS existing infrastructure — it does NOT duplicate:
- Service facts come from ``context/cloud/csp_service_registry.json`` (the
  existing 5-CSP registry the csp_monitor already maintains).
- Air-gap checks reuse :mod:`tools.twin_core.airgap_rules` (twx-fed-01).

PUBLIC-DATA-ONLY: the in-repo catalog carries only public availability facts.
Set ``ICDEV_CSP_CATALOG_PATH`` to a customer-side catalog to override at runtime
(sensitive/classified-region catalogs are never committed to this public repo).
"""
from __future__ import annotations

import json
import os
import re
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from tools.logging.icdev_logger import get_logger
from tools.twin_core.schema import canonical_violation

logger = get_logger("icdev.twin_core.target_presets")

_REPO_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_PRESETS_PATH = _REPO_ROOT / "args" / "twin_target_presets.yaml"
_DEFAULT_STALENESS_DAYS = 180

_PRESETS_CACHE: dict | None = None
_CATALOG_CACHE: dict | None = None

_WORD_RE = re.compile(r"[a-z0-9_]+", re.IGNORECASE)


def load_presets(path: str | Path | None = None, *, force: bool = False) -> dict:
    """Load (and cache) the preset config. Missing file → empty presets."""
    global _PRESETS_CACHE
    if _PRESETS_CACHE is not None and path is None and not force:
        return _PRESETS_CACHE
    cfg_path = Path(path) if path else _DEFAULT_PRESETS_PATH
    try:
        import yaml

        with open(cfg_path, encoding="utf-8") as fh:
            cfg = yaml.safe_load(fh) or {}
    except FileNotFoundError:
        logger.warning("target presets not found at %s", cfg_path)
        cfg = {"presets": {}}
    if path is None:
        _PRESETS_CACHE = cfg
    return cfg


def list_presets(path: str | Path | None = None) -> list[str]:
    return sorted((load_presets(path).get("presets", {}) or {}).keys())


def get_preset(name: str, path: str | Path | None = None) -> dict | None:
    return (load_presets(path).get("presets", {}) or {}).get(name)


def load_service_catalog(path: str | Path | None = None, *, force: bool = False) -> dict:
    """Load the CSP service catalog. Resolution order:
    explicit ``path`` → ``ICDEV_CSP_CATALOG_PATH`` env (customer-side) →
    the ``service_catalog`` field of the preset config → repo default.
    """
    global _CATALOG_CACHE
    if _CATALOG_CACHE is not None and path is None and not force:
        return _CATALOG_CACHE
    resolved: Path
    if path:
        resolved = Path(path)
    elif os.getenv("ICDEV_CSP_CATALOG_PATH"):
        resolved = Path(os.environ["ICDEV_CSP_CATALOG_PATH"])
    else:
        rel = (load_presets().get("service_catalog") or "context/cloud/csp_service_registry.json")
        resolved = _REPO_ROOT / rel
    try:
        with open(resolved, encoding="utf-8") as fh:
            cat = json.load(fh)
    except FileNotFoundError:
        logger.warning("service catalog not found at %s", resolved)
        cat = {"services": {}, "_metadata": {}}
    if path is None:
        _CATALOG_CACHE = cat
    return cat


def _iter_strings(obj: Any) -> Iterable[str]:
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for v in obj.values():
            yield from _iter_strings(v)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            yield from _iter_strings(v)


def _referenced_services(target: Any, csp_services: dict) -> set[str]:
    """Best-effort: which catalog service keys does the design reference?

    Matches a service when its catalog key (e.g. ``eks``) or a distinctive token
    of its display_name (e.g. ``kubernetes``) appears as a whole word in any
    harvested string. Also matches IaC resource types (``aws_eks_cluster`` →
    ``eks``) by token overlap.
    """
    tokens: set[str] = set()
    for s in _iter_strings(target):
        tokens |= {t.lower() for t in _WORD_RE.findall(s)}
    if not tokens:
        return set()
    referenced: set[str] = set()
    for key, meta in csp_services.items():
        key_l = key.lower()
        if key_l in tokens:
            referenced.add(key)
            continue
        # distinctive display-name tokens (len>=4, skip generic words)
        name_tokens = {t.lower() for t in _WORD_RE.findall(meta.get("display_name", ""))
                       if len(t) >= 4 and t.lower() not in _GENERIC}
        if name_tokens & tokens:
            referenced.add(key)
    return referenced


_GENERIC = {"service", "managed", "amazon", "azure", "google", "cloud", "oracle", "elastic"}


def _available_in_target(meta: dict, preset: dict) -> bool:
    """Is this service available in the preset's region/scope?"""
    scope = preset.get("region_scope", "government")
    region = preset.get("region")
    # government scope requires govcloud_available.
    if scope == "government" and not meta.get("govcloud_available", False):
        return False
    regions = (meta.get("regions", {}) or {}).get(scope, []) or []
    # If the catalog lists explicit regions for this scope, require membership;
    # otherwise fall back to the scope-level availability flag above.
    if region and regions:
        return region in regions
    return True


def catalog_review_date(catalog: dict) -> date | None:
    raw = (catalog.get("_metadata", {}) or {}).get("last_updated")
    return _parse_date(raw)


def _parse_date(raw) -> date | None:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00")).date()
    except (ValueError, TypeError):
        return None


def _age_days(d: date | None) -> int | None:
    if d is None:
        return None
    return (datetime.now(timezone.utc).date() - d).days


def evaluate_target(
    target: Any,
    preset: str | dict,
    *,
    source_canvas: str | None = None,
    presets_path: str | Path | None = None,
    catalog_path: str | Path | None = None,
) -> list[dict]:
    """Return canonical violations for running ``target`` against ``preset``.

    ``preset`` may be a preset name or a preset dict. Emits ``service_parity``
    blockers for unavailable services, air-gap deployment blockers (fed-01) when
    the preset is air-gapped, and a ``compliance`` warning when the preset or
    catalog is stale.
    """
    pcfg = load_presets(presets_path)
    if isinstance(preset, str):
        p = get_preset(preset, presets_path)
        preset_name = preset
        if p is None:
            logger.warning("unknown target preset %r", preset)
            return []
    else:
        p = preset
        preset_name = p.get("display_name", "custom")
    csp = p.get("csp", "aws")
    catalog = load_service_catalog(catalog_path)
    csp_services = (catalog.get("services", {}) or {}).get(csp, {}) or {}

    violations: list[dict] = []

    # 1. Staleness guard (preset + catalog).
    warn_days = int(pcfg.get("staleness_warn_days", _DEFAULT_STALENESS_DAYS))
    preset_age = _age_days(_parse_date(p.get("reviewed_at")))
    catalog_age = _age_days(catalog_review_date(catalog))
    stale_parts = []
    if preset_age is not None and preset_age > warn_days:
        stale_parts.append(f"preset reviewed {preset_age}d ago")
    if catalog_age is not None and catalog_age > warn_days:
        stale_parts.append(f"catalog reviewed {catalog_age}d ago")
    if stale_parts:
        violations.append(canonical_violation(
            "medium", "compliance",
            f"Re-review the target catalog/preset (> {warn_days}d): {', '.join(stale_parts)}",
            title=f"Stale target data for {preset_name}",
            rule_id="target-staleness", source_canvas=source_canvas, method="target-preset",
        ))

    # 2. Service availability in the target.
    for key in sorted(_referenced_services(target, csp_services)):
        meta = csp_services.get(key, {})
        if not _available_in_target(meta, p):
            violations.append(canonical_violation(
                "deployment_blocker", "service_parity",
                f"Replace '{meta.get('display_name', key)}' with a service available in {preset_name}, "
                f"or select a different target region.",
                title=f"Service '{meta.get('display_name', key)}' not available in {preset_name}",
                rule_id="service-not-available", target_csp=_csp_to_target(csp, p),
                source_canvas=source_canvas, method="target-preset", detail=key,
            ))

    # 3. Air-gap constraints (reuse fed-01).
    if (p.get("network_constraints", {}) or {}).get("airgap"):
        from tools.twin_core.airgap_rules import evaluate_airgap

        violations.extend(evaluate_airgap(target, source_canvas=source_canvas, active=True))

    return violations


def _csp_to_target(csp: str, preset: dict) -> str | None:
    scope = preset.get("region_scope")
    if csp == "aws":
        return "aws_govcloud" if scope == "government" else None
    if csp == "azure":
        return "azure_gov" if scope == "government" else None
    if csp in ("gcp", "oci", "ibm"):
        return csp
    return None

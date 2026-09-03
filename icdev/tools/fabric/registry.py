# CUI // SP-CTI
"""Fabric registry (rmf-fab-01): N named fabrics, each carrying a classification LABEL.

A *fabric* is an enclave instance that HAS a classification; it is not itself
a classification level. This module reads ``args/fabric_registry.yaml`` (schema
plus a SYNTHETIC fixture -- the repository is public and names no real
enclave), applies a PRIVATE overlay named by ``ICDEV_FABRIC_REGISTRY_PATH``
per fabric, validates every entry fail-closed, and hands the result to the
posture roll-up (``tools/fabric/posture.py``, rmf-fab-02) through
:func:`load_registry`.

THREE RULES, each executable here and pinned by ``tests/fabric/test_registry.py``:

* ``classification`` is a LABEL from ``args/classification_profiles.yaml``,
  never a banner. ``"CUI // SP-CTI"`` is refused with the label it should have
  been (``cui_sp_cti``). The banner is DERIVED at read time from the profile;
  nothing stores it. Dominance, rank and egress restriction come from
  ``icdev.core.sensitivity`` -- the one sensitivity seam -- and are never
  re-derived here.
* The in-repo file is a fixture. It must carry ``fixture: synthetic``, every
  fabric in it is ``synthetic: true``, and :func:`load_registry` EXCLUDES
  synthetic fabrics unless asked, reporting how many it excluded. A fixture that
  lights up a dashboard is fabricated coverage (rmf-disc-02's rule for
  ``ni_devices.source``), so a default deployment's posture panel reads
  "no fabrics declared" beside the count of fixtures it declined to render.
* The overlay lives OUTSIDE the repository. A path that resolves inside the
  repo root is refused (``overlay_inside_repo``): an in-tree overlay is one
  ``git add`` away from publishing the fleet. A configured overlay that does
  not exist is refused too -- an operator set it, and silently loading the
  fixture instead would render the wrong fleet under a green light.

Cross-fabric traversal is declared SEPARATELY (``traversals:``), never on a
fabric. Direction (``upward`` / ``downward`` / ``lateral`` / ``unranked``) is
derived from ``sensitivity.rank``; a DOWNWARD traversal without a named
``guard`` is refused.

Required controls per fabric reuse
``tools.compliance.crosswalk_engine.get_controls_for_impact_level``; an impact
level the crosswalk does not cover (IL2) reports ``no_crosswalk_for_impact_level``
with ``count: None``, never 0.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from icdev.core import sensitivity  # noqa: E402
from icdev.core.paths import repo_root  # noqa: E402

OVERLAY_ENV = "ICDEV_FABRIC_REGISTRY_PATH"
DEFAULT_RELPATH = Path("args") / "fabric_registry.yaml"
PROFILES_RELPATH = Path("args") / "classification_profiles.yaml"
SCHEMA_VERSION = 1

#: What an estate may be READ from. ``synthetic`` and ``topology_ingest`` are
#: deliberately absent: a fixture and a drawing are never authoritative.
INVENTORY_SOURCES = ("netbox", "csv", "discovery")
#: The methods the discovery reflex and the network ingesters implement.
DISCOVERY_ADAPTERS = ("ping", "snmp", "ssh", "netbox", "csv")
TRAVERSAL_KINDS = ("cross_domain_solution", "data_diode", "manual_transfer", "direct")
DIRECTIONS = ("upward", "downward", "lateral", "unranked")

SOURCE_FIXTURE = "fixture"
SOURCE_OVERLAY = "overlay"

_FABRIC_FIELDS = (
    "key",
    "display_name",
    "classification",
    "impact_level",
    "authoritative_inventory_source",
    "discovery_adapters",
)


class FabricRegistryError(ValueError):
    """The registry was refused. ``refusals`` lists EVERY defect, not the first."""

    def __init__(self, refusals: List[Dict[str, Any]]):
        self.refusals = list(refusals)
        lines = [
            f"{r.get('where')}: {r['reason']}"
            + (f" ({r['hint']})" if r.get("hint") else "")
            for r in self.refusals
        ]
        super().__init__("fabric registry refused: " + "; ".join(lines))


# ---------------------------------------------------------------------------
# Profiles (the label vocabulary) and the domain's impact levels
# ---------------------------------------------------------------------------

def _read_yaml(path: Path) -> Dict[str, Any]:
    import yaml  # noqa: PLC0415

    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise FabricRegistryError([
            {"where": str(path), "reason": "document_not_a_mapping", "hint": None}
        ])
    return data


def load_profiles(root: Optional[Path] = None) -> Dict[str, Dict[str, Any]]:
    """The ``profiles:`` mapping of args/classification_profiles.yaml, keyed by LABEL."""
    base = Path(root) if root else repo_root()
    data = _read_yaml(base / PROFILES_RELPATH)
    profiles = data.get("profiles") or {}
    return {str(k): (v or {}) for k, v in profiles.items() if isinstance(v, dict)}


def _banner_index(profiles: Dict[str, Dict[str, Any]]) -> Dict[str, str]:
    """banner text (lowercased) -> label, for the refusal hint."""
    out: Dict[str, str] = {}
    for label, prof in profiles.items():
        text = str(((prof.get("banner") or {}).get("text")) or "").strip().lower()
        if text:
            out[text] = label
    return out


def domain_impact_levels() -> Optional[List[str]]:
    """The impact levels icdev_domain.yaml declares; None when no domain is readable."""
    try:
        from icdev.core.domain import load_domain  # noqa: PLC0415

        levels = list(load_domain().sensitivity.levels or ())
        return [str(x).upper() for x in levels] or None
    except Exception:  # noqa: BLE001 - an unreadable domain declares nothing
        return None


def classify_label(value: Any, profiles: Dict[str, Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Return None when ``value`` is a declared LABEL, else a refusal dict.

    A banner is anything spelled as a marking rather than a profile key: the
    exact banner text of a profile, anything containing ``//``, or a value that
    only becomes a label after normalisation (``"CUI"`` is the cui banner, not
    the cui label). The hint names the label the writer meant.
    """
    if not isinstance(value, str) or not value.strip():
        return {"reason": "classification_missing", "hint": "one of " + ", ".join(sorted(profiles))}
    if value in profiles:
        return None
    banners = _banner_index(profiles)
    lowered = value.strip().lower()
    if lowered in banners:
        return {"reason": "banner_not_label", "hint": f"use the label {banners[lowered]!r}"}
    normalised = sensitivity.normalise(value)
    if "//" in value or normalised in profiles:
        hint = f"use the label {normalised!r}" if normalised in profiles else "a banner is not a label"
        return {"reason": "banner_not_label", "hint": hint}
    return {"reason": "unknown_label", "hint": "one of " + ", ".join(sorted(profiles))}


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def _validate_fabric(
    raw: Any,
    *,
    profiles: Dict[str, Dict[str, Any]],
    levels: Optional[List[str]],
    where: str,
) -> List[Dict[str, Any]]:
    refusals: List[Dict[str, Any]] = []

    def refuse(reason: str, hint: Optional[str] = None, field_name: Optional[str] = None) -> None:
        refusals.append({"where": where, "field": field_name, "reason": reason, "hint": hint})

    if not isinstance(raw, dict):
        refuse("fabric_not_a_mapping")
        return refusals

    key = raw.get("key")
    if not isinstance(key, str) or not key.strip():
        refuse("key_missing", field_name="key")
    if not isinstance(raw.get("display_name"), str) or not raw["display_name"].strip():
        refuse("display_name_missing", field_name="display_name")

    label_refusal = classify_label(raw.get("classification"), profiles)
    if label_refusal:
        refuse(label_refusal["reason"], label_refusal["hint"], field_name="classification")

    il = raw.get("impact_level")
    if not isinstance(il, str) or not il.strip():
        refuse("impact_level_missing", field_name="impact_level")
    else:
        il_up = il.upper()
        if levels is not None and il_up not in levels:
            refuse("impact_level_not_declared_by_domain", "one of " + ", ".join(levels), "impact_level")
        elif not label_refusal:
            admitted = [str(x).upper() for x in (profiles[raw["classification"]].get("impact_levels") or [])]
            if admitted and il_up not in admitted:
                refuse(
                    "impact_level_not_in_profile",
                    f"{raw['classification']!r} admits " + ", ".join(admitted),
                    "impact_level",
                )

    src = raw.get("authoritative_inventory_source")
    if src is not None and src not in INVENTORY_SOURCES:
        hint = "a fixture or a drawing is never authoritative" if src in ("synthetic", "topology_ingest") else None
        refuse("inventory_source_unknown", hint or ("one of " + ", ".join(INVENTORY_SOURCES)), "authoritative_inventory_source")

    adapters = raw.get("discovery_adapters")
    if adapters is None:
        adapters = []
    if not isinstance(adapters, list):
        refuse("discovery_adapters_not_a_list", field_name="discovery_adapters")
    else:
        for a in adapters:
            if a not in DISCOVERY_ADAPTERS:
                refuse("discovery_adapter_unknown", f"{a!r}; one of " + ", ".join(DISCOVERY_ADAPTERS), "discovery_adapters")
    return refusals


def _direction(from_label: Optional[str], to_label: Optional[str]) -> str:
    try:
        a, b = sensitivity.rank(from_label), sensitivity.rank(to_label)
    except Exception:  # noqa: BLE001 - no readable domain: nothing is ranked
        return "unranked"
    if a is None or b is None:
        return "unranked"
    if a > b:
        return "downward"
    if a < b:
        return "upward"
    return "lateral"


def _validate_traversal(
    raw: Any, *, fabrics: Dict[str, Dict[str, Any]], where: str
) -> List[Dict[str, Any]]:
    refusals: List[Dict[str, Any]] = []

    def refuse(reason: str, hint: Optional[str] = None) -> None:
        refusals.append({"where": where, "field": None, "reason": reason, "hint": hint})

    if not isinstance(raw, dict):
        refuse("traversal_not_a_mapping")
        return refusals
    src, dst = raw.get("from"), raw.get("to")
    for end, name in ((src, "from"), (dst, "to")):
        if end not in fabrics:
            refuse("traversal_endpoint_undeclared", f"{name}={end!r}")
    if src is not None and src == dst:
        refuse("traversal_to_self")
    kind = raw.get("kind")
    if kind not in TRAVERSAL_KINDS:
        refuse("traversal_kind_unknown", f"{kind!r}; one of " + ", ".join(TRAVERSAL_KINDS))
    if src in fabrics and dst in fabrics:
        direction = _direction(fabrics[src].get("classification"), fabrics[dst].get("classification"))
        guard = raw.get("guard")
        if direction == "downward" and not (isinstance(guard, str) and guard.strip()):
            refuse(
                "downward_traversal_unguarded",
                f"{src} ({fabrics[src].get('classification')}) -> {dst} "
                f"({fabrics[dst].get('classification')}) names no guard",
            )
    return refusals


# ---------------------------------------------------------------------------
# The registry
# ---------------------------------------------------------------------------

@dataclass
class FabricRegistry:
    fabrics: List[Dict[str, Any]] = field(default_factory=list)
    traversals: List[Dict[str, Any]] = field(default_factory=list)
    source: Dict[str, Any] = field(default_factory=dict)

    def get(self, key: str) -> Optional[Dict[str, Any]]:
        for f in self.fabrics:
            if f["key"] == key:
                return f
        return None

    def real(self) -> List[Dict[str, Any]]:
        return [f for f in self.fabrics if not f.get("synthetic")]

    def synthetic(self) -> List[Dict[str, Any]]:
        return [f for f in self.fabrics if f.get("synthetic")]

    def to_dict(self, *, include_synthetic: bool = True) -> Dict[str, Any]:
        fabrics = self.fabrics if include_synthetic else self.real()
        excluded = 0 if include_synthetic else len(self.synthetic())
        reason = None
        if not fabrics:
            if excluded:
                reason = (
                    f"only synthetic fixture fabrics declared ({excluded} excluded); "
                    f"set {OVERLAY_ENV} to a private overlay outside the repository"
                )
            else:
                reason = "no fabrics declared"
        return {
            "schema_version": SCHEMA_VERSION,
            "fabrics": fabrics,
            "traversals": list(self.traversals),
            "source": dict(self.source),
            "fabric_count_declared": len(self.fabrics),
            "synthetic_excluded": excluded,
            "reason": reason,
        }


def _resolve_overlay(overlay_path: Optional[str], root: Path) -> Optional[Path]:
    raw = overlay_path if overlay_path is not None else os.environ.get(OVERLAY_ENV, "")
    raw = (raw or "").strip()
    if not raw:
        return None
    p = Path(raw).expanduser()
    try:
        resolved = p.resolve()
    except OSError:
        resolved = p.absolute()
    root_resolved = root.resolve()
    if resolved == root_resolved or root_resolved in resolved.parents:
        raise FabricRegistryError([
            {
                "where": str(p),
                "field": None,
                "reason": "overlay_inside_repo",
                "hint": f"{OVERLAY_ENV} must name a file OUTSIDE {root_resolved} -- the repository is public",
            }
        ])
    if not resolved.is_file():
        raise FabricRegistryError([
            {"where": str(p), "field": None, "reason": "overlay_missing", "hint": f"{OVERLAY_ENV} names a file that does not exist"}
        ])
    return resolved


def _view(raw: Dict[str, Any], *, profiles: Dict[str, Dict[str, Any]], source: str, synthetic: bool) -> Dict[str, Any]:
    prof = profiles.get(raw["classification"], {})
    label = raw["classification"]
    try:
        egress = sensitivity.is_egress_restricted(label)
        rank = sensitivity.rank(label)
    except Exception:  # noqa: BLE001 - no readable domain: fail closed on egress, unranked
        egress, rank = True, None
    return {
        "key": raw["key"],
        "display_name": raw["display_name"],
        "classification": label,
        "classification_display": prof.get("display_name") or label,
        # DERIVED from the profile at read time. Never a stored field.
        "banner": ((prof.get("banner") or {}).get("text")) or None,
        "impact_level": str(raw["impact_level"]).upper(),
        "egress_restricted": egress,
        "sensitivity_rank": rank,
        "authoritative_inventory_source": raw.get("authoritative_inventory_source"),
        "discovery_adapters": list(raw.get("discovery_adapters") or []),
        "synthetic": synthetic,
        "source": source,
    }


def load(
    base_path: Optional[str | os.PathLike[str]] = None,
    overlay_path: Optional[str] = None,
    *,
    root: Optional[str | os.PathLike[str]] = None,
) -> FabricRegistry:
    """Load base + overlay, validate fail-closed, derive views.

    ``overlay_path=None`` reads ``ICDEV_FABRIC_REGISTRY_PATH``; ``""`` disables
    the overlay for this call. Raises :class:`FabricRegistryError` carrying
    EVERY refusal.
    """
    root_p = Path(root) if root else repo_root()
    base = Path(base_path) if base_path else root_p / DEFAULT_RELPATH
    profiles = load_profiles(root_p)
    levels = domain_impact_levels()
    refusals: List[Dict[str, Any]] = []

    base_doc = _read_yaml(base)
    base_in_repo = False
    try:
        base_in_repo = root_p.resolve() in base.resolve().parents
    except OSError:
        base_in_repo = False
    base_is_fixture = str(base_doc.get("fixture") or "").strip().lower() == "synthetic"
    if base_in_repo and not base_is_fixture:
        refusals.append({
            "where": str(base),
            "field": "fixture",
            "reason": "in_repo_registry_must_be_fixture",
            "hint": "declare `fixture: synthetic`; real fabrics go in the overlay named by " + OVERLAY_ENV,
        })

    merged: Dict[str, Dict[str, Any]] = {}
    for i, raw in enumerate(base_doc.get("fabrics") or []):
        where = f"{base.name}#fabrics[{i}]"
        refusals.extend(_validate_fabric(raw, profiles=profiles, levels=levels, where=where))
        if isinstance(raw, dict) and isinstance(raw.get("key"), str):
            if raw["key"] in merged:
                refusals.append({"where": where, "field": "key", "reason": "duplicate_key", "hint": raw["key"]})
            merged[raw["key"]] = {
                **raw,
                "_source": SOURCE_FIXTURE,
                "_synthetic": bool(base_is_fixture or raw.get("synthetic")),
            }
    traversals_raw: List[Any] = list(base_doc.get("traversals") or [])

    overlay = _resolve_overlay(overlay_path, root_p)
    overlay_doc: Dict[str, Any] = {}
    if overlay is not None:
        overlay_doc = _read_yaml(overlay)
        for k in overlay_doc.get("drop") or []:
            merged.pop(str(k), None)
        seen_overlay: set = set()
        for i, raw in enumerate(overlay_doc.get("fabrics") or []):
            where = f"{overlay.name}#fabrics[{i}]"
            refusals.extend(_validate_fabric(raw, profiles=profiles, levels=levels, where=where))
            if isinstance(raw, dict) and isinstance(raw.get("key"), str):
                if raw["key"] in seen_overlay:
                    refusals.append({"where": where, "field": "key", "reason": "duplicate_key", "hint": raw["key"]})
                seen_overlay.add(raw["key"])
                # An overlay entry REPLACES the fixture entry whole: no fixture
                # field may leak into a real fabric.
                merged[raw["key"]] = {
                    **raw,
                    "_source": SOURCE_OVERLAY,
                    "_synthetic": bool(raw.get("synthetic", False)),
                }
        if "traversals" in overlay_doc:
            traversals_raw = list(overlay_doc.get("traversals") or [])
            traversals_are_fixture = False
        else:
            traversals_are_fixture = base_is_fixture
    else:
        traversals_are_fixture = base_is_fixture

    # A FIXTURE traversal whose endpoint the overlay dropped or never declared
    # is fixture data too: it goes with the fabric, and is reported, never
    # refused. An OVERLAY traversal naming an undeclared endpoint is refused.
    traversals_dropped: List[Dict[str, Any]] = []
    if traversals_are_fixture:
        kept: List[Any] = []
        for t in traversals_raw:
            if isinstance(t, dict) and (t.get("from") not in merged or t.get("to") not in merged):
                traversals_dropped.append({"from": t.get("from"), "to": t.get("to"), "kind": t.get("kind")})
            else:
                kept.append(t)
        traversals_raw = kept

    # Traversals are validated against the MERGED set.
    valid_for_traversal = {
        k: v for k, v in merged.items()
        if isinstance(v.get("classification"), str) and v["classification"] in profiles
    }
    for i, raw in enumerate(traversals_raw):
        refusals.extend(_validate_traversal(raw, fabrics=valid_for_traversal, where=f"traversals[{i}]"))

    if refusals:
        raise FabricRegistryError(refusals)

    fabrics = [
        _view(v, profiles=profiles, source=v["_source"], synthetic=v["_synthetic"])
        for v in merged.values()
    ]
    traversals = [
        {
            "from": t["from"],
            "to": t["to"],
            "kind": t["kind"],
            "guard": t.get("guard"),
            "direction": _direction(merged[t["from"]].get("classification"), merged[t["to"]].get("classification")),
        }
        for t in traversals_raw
    ]
    return FabricRegistry(
        fabrics=fabrics,
        traversals=traversals,
        source={
            "base": str(base),
            "base_is_fixture": base_is_fixture,
            "overlay": str(overlay) if overlay else None,
            "overlay_env": OVERLAY_ENV,
            "overlay_active": overlay is not None,
            "dropped_by_overlay": [str(k) for k in (overlay_doc.get("drop") or [])],
            "fixture_traversals_dropped": traversals_dropped,
        },
    )


def load_registry(*, include_synthetic: bool = False, **kwargs: Any) -> Dict[str, Any]:
    """The posture seam (``posture._REGISTRY_LOADERS`` probes this name first).

    Synthetic fixture fabrics are EXCLUDED unless asked for, and the count
    excluded is reported beside a reason, so a default deployment reads "no
    fabrics declared (3 fixtures excluded)" rather than a fabricated fleet.
    """
    return load(**kwargs).to_dict(include_synthetic=include_synthetic)


def list_fabrics(*, include_synthetic: bool = False, **kwargs: Any) -> List[Dict[str, Any]]:
    return load_registry(include_synthetic=include_synthetic, **kwargs)["fabrics"]


def get_fabric(key: str, **kwargs: Any) -> Optional[Dict[str, Any]]:
    return load(**kwargs).get(key)


def required_controls(fabric: Dict[str, Any]) -> Dict[str, Any]:
    """NIST 800-53 controls the fabric's impact level requires, via the crosswalk.

    ``count`` is None -- never 0 -- when the crosswalk cannot answer for this
    level (IL2 today) or is unreadable; ``crosswalk_declares_none`` is the
    MEASURED zero, kept apart from both.
    """
    il = str(fabric.get("impact_level") or "").upper()
    out: Dict[str, Any] = {"impact_level": il, "state": None, "count": None, "control_ids": None, "reason": None}
    try:
        from tools.compliance.crosswalk_engine import get_controls_for_impact_level  # noqa: PLC0415
    except ImportError as exc:
        out.update(state="source_unavailable", reason=f"crosswalk_engine not importable: {exc}")
        return out
    try:
        entries = get_controls_for_impact_level(il)
    except ValueError as exc:
        out.update(state="no_crosswalk_for_impact_level", reason=str(exc))
        return out
    except Exception as exc:  # noqa: BLE001 - an unreadable crosswalk is reported, never an empty list
        out.update(state="source_unavailable", reason=str(exc))
        return out
    ids = sorted({
        str(e.get("nist_800_53") or e.get("nist_id") or "").upper()
        for e in entries
        if e.get("nist_800_53") or e.get("nist_id")
    })
    out.update(
        state="declared" if ids else "crosswalk_declares_none",
        count=len(ids),
        control_ids=ids,
    )
    return out


def describe(*, include_synthetic: bool = True, with_controls: bool = True, **kwargs: Any) -> Dict[str, Any]:
    reg = load(**kwargs)
    payload = reg.to_dict(include_synthetic=include_synthetic)
    if with_controls:
        for f in payload["fabrics"]:
            rc = required_controls(f)
            f["required_controls"] = {k: rc[k] for k in ("impact_level", "state", "count", "reason")}
    payload["valid"] = True
    return payload


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _render(payload: Dict[str, Any]) -> str:
    src = payload["source"]
    lines = [
        f"fabric registry: {src['base']}" + ("  (fixture)" if src.get("base_is_fixture") else ""),
        f"overlay ({src['overlay_env']}): {src['overlay'] or 'not set'}",
        f"declared {payload['fabric_count_declared']}, shown {len(payload['fabrics'])}, "
        f"synthetic excluded {payload['synthetic_excluded']}",
    ]
    if payload.get("reason"):
        lines.append(f"NOTE: {payload['reason']}")
    for f in payload["fabrics"]:
        tag = " [SYNTHETIC]" if f.get("synthetic") else ""
        lines.append(f"[{f['key']}] {f['display_name']}{tag}")
        lines.append(
            f"  classification={f['classification']} ({f['classification_display']}) "
            f"impact_level={f['impact_level']} egress_restricted={f['egress_restricted']} "
            f"rank={f['sensitivity_rank']}"
        )
        lines.append(
            f"  inventory={f['authoritative_inventory_source']} adapters={','.join(f['discovery_adapters']) or '-'}"
        )
        rc = f.get("required_controls")
        if rc:
            n = rc["count"] if rc["count"] is not None else "?"
            lines.append(f"  required_controls={n} ({rc['state']})")
    for t in payload["traversals"]:
        lines.append(
            f"traversal {t['from']} -> {t['to']}: {t['kind']} {t['direction']}"
            + (f" guard={t['guard']}" if t.get("guard") else "")
        )
    return "\n".join(lines)


def main(argv: Optional[Iterable[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Fabric registry (rmf-fab-01)")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--include-synthetic", action="store_true", help="show fixture fabrics too")
    parser.add_argument("--overlay", default=None, help=f"overlay path (default: ${OVERLAY_ENV})")
    parser.add_argument("--fabric", default=None, help="limit to one fabric key")
    parser.add_argument("--check", action="store_true", help="validate only; exit 1 on refusal")
    args = parser.parse_args(list(argv) if argv is not None else None)
    try:
        payload = describe(include_synthetic=args.include_synthetic or args.check, overlay_path=args.overlay)
    except FabricRegistryError as exc:
        if args.json:
            print(json.dumps({"valid": False, "refusals": exc.refusals}, indent=2))
        else:
            print("REFUSED")
            for r in exc.refusals:
                print(f"  {r.get('where')}: {r['reason']}" + (f" -- {r['hint']}" if r.get("hint") else ""))
        return 1
    if args.check:
        print("ok" if not args.json else json.dumps({"valid": True, "fabric_count_declared": payload["fabric_count_declared"]}))
        return 0
    if args.fabric:
        payload["fabrics"] = [f for f in payload["fabrics"] if f["key"] == args.fabric]
    print(json.dumps(payload, indent=2, default=str) if args.json else _render(payload))
    return 0


if __name__ == "__main__":
    sys.exit(main())

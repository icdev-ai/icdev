# [TEMPLATE: CUI // SP-CTI]
"""Unified OSCAL catalog adapter — reads both NIST OSCAL and ICDEV formats.

Supports two catalog formats:
  1. Official NIST OSCAL (nested groups/controls/params from usnistgov/oscal-content)
  2. ICDEV custom flat format (context/compliance/nist_800_53.json)

The adapter normalizes both into a common internal format so callers don't need
to know which catalog is active.  Priority: official OSCAL → ICDEV fallback.

Architecture Decision D304.

Usage (library):
    from tools.compliance.oscal_catalog_adapter import OscalCatalogAdapter
    adapter = OscalCatalogAdapter()
    ctrl = adapter.get_control("AC-2")
    ctrls = adapter.list_controls(family="AC")
    stats = adapter.get_catalog_stats()

Usage (CLI):
    python tools/compliance/oscal_catalog_adapter.py --lookup AC-2 --json
    python tools/compliance/oscal_catalog_adapter.py --list --family AC --json
    python tools/compliance/oscal_catalog_adapter.py --stats --json
"""

import argparse
import json
import logging
import re
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent.parent.parent

# Default catalog sources in priority order (D304)
_DEFAULT_SOURCES = [
    BASE_DIR / "context" / "oscal" / "NIST_SP-800-53_rev5_catalog.json",
    BASE_DIR / "context" / "compliance" / "nist_800_53.json",
]

# Cache parsed catalogs at module level (same pattern as crosswalk_engine.py)
_CATALOG_CACHE = {}


def _normalize_control_id(control_id):
    """Normalize control ID to uppercase with hyphens: ac-2 → AC-2, ac-2.1 → AC-2(1)."""
    if not control_id:
        return ""
    cid = control_id.strip().upper()
    # Convert OSCAL dot notation back to parenthetical: AC-2.1 → AC-2(1)
    cid = re.sub(r"\.(\d+)", r"(\1)", cid)
    return cid


def _is_oscal_format(data):
    """Check if data is official NIST OSCAL catalog format."""
    return isinstance(data, dict) and "catalog" in data


def _is_icdev_format(data):
    """Check if data is ICDEV custom catalog format."""
    return isinstance(data, dict) and "controls" in data and "metadata" in data


def _parse_oscal_catalog(data):
    """Parse official NIST OSCAL catalog into normalized control list.

    OSCAL structure: catalog.groups[].controls[].controls[] (enhancements nested)
    Each control has: id, title, params[], props[], parts[], controls[] (enhancements)
    """
    controls = {}
    catalog = data.get("catalog", {})

    for group in catalog.get("groups", []):
        family_id = group.get("id", "").upper()
        family_title = group.get("title", "")

        for control in group.get("controls", []):
            ctrl = _extract_oscal_control(control, family_id, family_title)
            if ctrl:
                controls[ctrl["id"]] = ctrl

            # Process enhancements (nested controls)
            for enhancement in control.get("controls", []):
                enh = _extract_oscal_control(enhancement, family_id, family_title)
                if enh:
                    enh["is_enhancement"] = True
                    enh["parent_id"] = _normalize_control_id(control.get("id", ""))
                    controls[enh["id"]] = enh

    return controls


def _extract_oscal_control(control, family_id, family_title):
    """Extract a single OSCAL control into normalized format."""
    raw_id = control.get("id", "")
    if not raw_id:
        return None

    ctrl_id = _normalize_control_id(raw_id)

    # Extract description from parts
    description = ""
    supplemental = ""
    for part in control.get("parts", []):
        part_name = part.get("name", "")
        prose = part.get("prose", "")
        if part_name == "statement":
            description = prose
        elif part_name == "guidance":
            supplemental = prose

    # Extract impact level from props
    impact_level = "low"
    for prop in control.get("props", []):
        if prop.get("name") == "label":
            pass  # label is the human-readable ID
        # OSCAL doesn't store impact_level directly — that's in profiles/baselines

    # Extract parameters
    params = []
    for param in control.get("params", []):
        param_entry = {
            "id": param.get("id", ""),
            "label": param.get("label", ""),
        }
        if "select" in param:
            param_entry["choices"] = [
                c for c in param["select"].get("choice", [])
            ]
        if "guidelines" in param:
            param_entry["guidelines"] = [
                g.get("prose", "") for g in param["guidelines"]
            ]
        params.append(param_entry)

    # Check withdrawn status
    withdrawn = False
    for prop in control.get("props", []):
        if prop.get("name") == "status" and prop.get("value") == "withdrawn":
            withdrawn = True
            break

    return {
        "id": ctrl_id,
        "family": family_id.split("-")[0] if "-" in family_id else family_id,
        "family_title": family_title,
        "title": control.get("title", ""),
        "description": description,
        "supplemental_guidance": supplemental,
        "impact_level": impact_level,
        "is_enhancement": False,
        "parent_id": None,
        "params": params,
        "withdrawn": withdrawn,
        "source": "nist_oscal",
    }


def _parse_icdev_catalog(data):
    """Parse ICDEV custom catalog into normalized control dict."""
    controls = {}
    for ctrl in data.get("controls", []):
        ctrl_id = _normalize_control_id(ctrl.get("id", ""))
        if not ctrl_id:
            continue
        controls[ctrl_id] = {
            "id": ctrl_id,
            "family": ctrl.get("family", ""),
            "family_title": "",
            "title": ctrl.get("title", ""),
            "description": ctrl.get("description", ""),
            "supplemental_guidance": ctrl.get("supplemental_guidance", ""),
            "impact_level": ctrl.get("impact_level", "low"),
            "is_enhancement": "(" in ctrl_id,
            "parent_id": None,
            "params": [],
            "withdrawn": False,
            "source": "icdev_custom",
        }
    return controls


class OscalCatalogAdapter:
    """Unified catalog reader supporting both NIST OSCAL and ICDEV formats.

    Priority: official NIST OSCAL catalog → ICDEV custom catalog (fallback).
    """

    def __init__(self, catalog_path=None, catalog_sources=None):
        """Load catalog from the first available source.

        Args:
            catalog_path: Explicit path to a catalog file. Overrides priority.
            catalog_sources: List of paths to try in order. Defaults to D304 sources.
        """
        self._controls = {}
        self._source_path = None
        self._source_format = None
        self._metadata = {}

        if catalog_path:
            sources = [Path(catalog_path)]
        elif catalog_sources:
            sources = [Path(s) for s in catalog_sources]
        else:
            sources = list(_DEFAULT_SOURCES)

        for src in sources:
            if src.exists():
                self._load(src)
                if self._controls:
                    break

        if not self._controls:
            logger.warning("No OSCAL catalog found. Tried: %s", sources)

    def _load(self, path):
        """Load and parse a catalog file."""
        cache_key = str(path)
        if cache_key in _CATALOG_CACHE:
            cached = _CATALOG_CACHE[cache_key]
            self._controls = cached["controls"]
            self._source_path = cached["source_path"]
            self._source_format = cached["source_format"]
            self._metadata = cached["metadata"]
            return

        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Failed to load catalog %s: %s", path, exc)
            return

        if _is_oscal_format(data):
            self._controls = _parse_oscal_catalog(data)
            self._source_format = "nist_oscal"
            self._metadata = data.get("catalog", {}).get("metadata", {})
        elif _is_icdev_format(data):
            self._controls = _parse_icdev_catalog(data)
            self._source_format = "icdev_custom"
            self._metadata = data.get("metadata", {})
        else:
            logger.warning("Unrecognized catalog format: %s", path)
            return

        self._source_path = str(path)

        _CATALOG_CACHE[cache_key] = {
            "controls": self._controls,
            "source_path": self._source_path,
            "source_format": self._source_format,
            "metadata": self._metadata,
        }

    def get_control(self, control_id):
        """Get a single control by ID (case-insensitive).

        Args:
            control_id: Control ID (e.g., "AC-2", "ac-2", "AC-2(1)").

        Returns:
            Control dict or None if not found.
        """
        normalized = _normalize_control_id(control_id)
        return self._controls.get(normalized)

    def list_controls(self, family=None, include_withdrawn=False):
        """List controls, optionally filtered by family.

        Args:
            family: Family code to filter (e.g., "AC", "AU"). Case-insensitive.
            include_withdrawn: Include withdrawn controls. Default False.

        Returns:
            List of control dicts sorted by ID.
        """
        results = []
        family_upper = family.upper() if family else None

        for ctrl in self._controls.values():
            if family_upper and ctrl.get("family", "").upper() != family_upper:
                continue
            if not include_withdrawn and ctrl.get("withdrawn", False):
                continue
            results.append(ctrl)

        results.sort(key=lambda c: c.get("id", ""))
        return results

    def get_catalog_stats(self):
        """Return metadata about the loaded catalog.

        Returns:
            Dict with source, format, counts, and families.
        """
        families = set()
        enhancements = 0
        withdrawn = 0
        for ctrl in self._controls.values():
            families.add(ctrl.get("family", ""))
            if ctrl.get("is_enhancement"):
                enhancements += 1
            if ctrl.get("withdrawn"):
                withdrawn += 1

        return {
            "source_path": self._source_path,
            "source_format": self._source_format,
            "total_controls": len(self._controls),
            "base_controls": len(self._controls) - enhancements,
            "enhancements": enhancements,
            "withdrawn": withdrawn,
            "families": sorted(families),
            "family_count": len(families),
            "metadata": {
                "title": self._metadata.get("title", ""),
                "version": self._metadata.get("version", self._metadata.get("revision", "")),
            },
        }

    def is_official_catalog(self):
        """True if using the official NIST OSCAL format catalog."""
        return self._source_format == "nist_oscal"

    def is_loaded(self):
        """True if a catalog was successfully loaded."""
        return len(self._controls) > 0


def main():
    """CLI entry point for catalog operations."""
    parser = argparse.ArgumentParser(
        description="OSCAL Catalog Adapter — query NIST 800-53 controls (D304)"
    )
    parser.add_argument("--lookup", help="Look up a control by ID (e.g., AC-2)")
    parser.add_argument("--list", action="store_true", help="List controls")
    parser.add_argument("--family", help="Filter by family code (e.g., AC, AU)")
    parser.add_argument("--stats", action="store_true", help="Show catalog stats")
    parser.add_argument("--catalog", help="Explicit path to catalog file")
    parser.add_argument("--json", action="store_true", help="JSON output")
    parser.add_argument("--human", action="store_true", help="Human-readable output")
    args = parser.parse_args()

    adapter = OscalCatalogAdapter(catalog_path=args.catalog)

    if args.stats:
        result = adapter.get_catalog_stats()
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            print(f"Source:   {result['source_path']}")
            print(f"Format:   {result['source_format']}")
            print(f"Controls: {result['total_controls']} total "
                  f"({result['base_controls']} base, {result['enhancements']} enhancements)")
            print(f"Families: {result['family_count']} ({', '.join(result['families'])})")
        sys.exit(0)

    if args.lookup:
        ctrl = adapter.get_control(args.lookup)
        if ctrl:
            if args.json:
                print(json.dumps(ctrl, indent=2))
            else:
                print(f"{ctrl['id']}: {ctrl['title']}")
                print(f"  Family: {ctrl['family']}")
                print(f"  Description: {ctrl['description'][:200]}...")
                if ctrl.get("params"):
                    print(f"  Parameters: {len(ctrl['params'])}")
        else:
            print(json.dumps({"error": f"Control '{args.lookup}' not found"})
                  if args.json else f"Control '{args.lookup}' not found")
            sys.exit(1)
        sys.exit(0)

    if args.list:
        controls = adapter.list_controls(family=args.family)
        if args.json:
            print(json.dumps({"controls": controls, "count": len(controls)}, indent=2))
        else:
            for ctrl in controls:
                status = " [WITHDRAWN]" if ctrl.get("withdrawn") else ""
                enh = " (enhancement)" if ctrl.get("is_enhancement") else ""
                print(f"  {ctrl['id']}: {ctrl['title']}{enh}{status}")
            print(f"\n{len(controls)} controls")
        sys.exit(0)

    parser.print_help()


if __name__ == "__main__":
    main()

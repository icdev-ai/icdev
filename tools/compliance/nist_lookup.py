#!/usr/bin/env python3
"""NIST 800-53 Rev 5 control reference lookup tool.
Loads controls from context/compliance/nist_800_53.json and provides
lookup by control ID or listing by family code."""

import argparse
import json
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
CONTROLS_PATH = BASE_DIR / "context" / "compliance" / "nist_800_53.json"


def load_controls(controls_path=None):
    """Load NIST 800-53 controls from JSON file. Returns list of control dicts."""
    path = controls_path or CONTROLS_PATH
    if not path.exists():
        raise FileNotFoundError(
            f"Controls file not found: {path}\n"
            "Ensure context/compliance/nist_800_53.json exists."
        )
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data.get("controls", [])


def _build_index(controls):
    """Build lookup indices from the controls list."""
    by_id = {}
    by_family = {}
    for ctrl in controls:
        cid = ctrl["id"].upper()
        family = ctrl["family"].upper()
        by_id[cid] = ctrl
        by_family.setdefault(family, []).append(ctrl)
    return by_id, by_family


def lookup(control_id, controls=None, controls_path=None):
    """Look up a single control by ID (e.g., 'SA-11').
    Returns the full control dict or None if not found."""
    if controls is None:
        controls = load_controls(controls_path)
    by_id, _ = _build_index(controls)
    return by_id.get(control_id.upper())


def list_family(family_code, controls=None, controls_path=None):
    """List all controls in a given family (e.g., 'SA').
    Returns a list of control dicts."""
    if controls is None:
        controls = load_controls(controls_path)
    _, by_family = _build_index(controls)
    return by_family.get(family_code.upper(), [])


def list_all_families(controls=None, controls_path=None):
    """Return a dict mapping family codes to family names and control counts."""
    if controls is None:
        controls = load_controls(controls_path)

    family_names = {
        "AC": "Access Control",
        "AU": "Audit and Accountability",
        "CM": "Configuration Management",
        "IA": "Identification and Authentication",
        "SA": "System and Services Acquisition",
        "SC": "System and Communications Protection",
        "RA": "Risk Assessment",
        "CA": "Assessment, Authorization, and Monitoring",
    }

    _, by_family = _build_index(controls)
    result = {}
    for code in sorted(by_family.keys()):
        result[code] = {
            "name": family_names.get(code, code),
            "count": len(by_family[code]),
            "controls": [c["id"] for c in by_family[code]],
        }
    return result


def search_controls(query, controls=None, controls_path=None):
    """Search controls by keyword in title, description, or supplemental guidance.
    Returns a list of matching control dicts."""
    if controls is None:
        controls = load_controls(controls_path)

    query_lower = query.lower()
    results = []
    for ctrl in controls:
        searchable = " ".join([
            ctrl.get("title", ""),
            ctrl.get("description", ""),
            ctrl.get("supplemental_guidance", ""),
        ]).lower()
        if query_lower in searchable:
            results.append(ctrl)
    return results


def format_control(ctrl, verbose=True):
    """Format a control dict as a human-readable string."""
    lines = [
        f"{'=' * 70}",
        f"  {ctrl['id']}: {ctrl['title']}",
        f"{'=' * 70}",
        f"  Family:       {ctrl['family']}",
        f"  Impact Level: {ctrl.get('impact_level', 'N/A')}",
    ]
    if verbose:
        lines.append(f"\n  Description:\n  {_wrap(ctrl.get('description', 'N/A'), 68)}")
        guidance = ctrl.get("supplemental_guidance", "")
        if guidance:
            lines.append(f"\n  Supplemental Guidance:\n  {_wrap(guidance, 68)}")
    lines.append("")
    return "\n".join(lines)


def format_family_list(family_controls):
    """Format a list of controls in a family as a table."""
    if not family_controls:
        return "No controls found in this family."

    family_code = family_controls[0]["family"]
    lines = [
        f"NIST 800-53 Rev 5 — Family: {family_code}",
        f"{'─' * 70}",
        f"{'ID':<10} {'Impact':<10} {'Title'}",
        f"{'─' * 70}",
    ]
    for ctrl in sorted(family_controls, key=lambda c: c["id"]):
        lines.append(
            f"{ctrl['id']:<10} {ctrl.get('impact_level', 'N/A'):<10} {ctrl['title']}"
        )
    lines.append(f"{'─' * 70}")
    lines.append(f"Total: {len(family_controls)} controls")
    return "\n".join(lines)


def _wrap(text, width):
    """Simple word-wrap for display."""
    words = text.split()
    lines = []
    current_line = ""
    for word in words:
        if len(current_line) + len(word) + 1 > width:
            lines.append(current_line)
            current_line = word
        else:
            current_line = f"{current_line} {word}" if current_line else word
    if current_line:
        lines.append(current_line)
    return "\n  ".join(lines)


def main():
    parser = argparse.ArgumentParser(
        description="NIST 800-53 Rev 5 control reference lookup"
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--control-id", type=str,
        help="Look up a specific control by ID (e.g., SA-11, AC-2)"
    )
    group.add_argument(
        "--family", type=str,
        help="List all controls in a family (e.g., SA, AC, CM)"
    )
    group.add_argument(
        "--list-families", action="store_true",
        help="List all available control families"
    )
    group.add_argument(
        "--search", type=str,
        help="Search controls by keyword"
    )
    parser.add_argument(
        "--json", action="store_true",
        help="Output in JSON format"
    )
    parser.add_argument(
        "--controls-file", type=str, default=None,
        help="Path to NIST controls JSON file (default: context/compliance/nist_800_53.json)"
    )
    args = parser.parse_args()

    controls_path = Path(args.controls_file) if args.controls_file else None

    try:
        controls = load_controls(controls_path)
    except FileNotFoundError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)

    if args.control_id:
        ctrl = lookup(args.control_id, controls=controls)
        if ctrl is None:
            print(f"Control '{args.control_id}' not found.", file=sys.stderr)
            sys.exit(1)
        if args.json:
            print(json.dumps(ctrl, indent=2))
        else:
            print(format_control(ctrl))

    elif args.family:
        family_controls = list_family(args.family, controls=controls)
        if not family_controls:
            print(f"No controls found for family '{args.family}'.", file=sys.stderr)
            sys.exit(1)
        if args.json:
            print(json.dumps(family_controls, indent=2))
        else:
            print(format_family_list(family_controls))

    elif args.list_families:
        families = list_all_families(controls=controls)
        if args.json:
            print(json.dumps(families, indent=2))
        else:
            print("NIST 800-53 Rev 5 — Available Control Families")
            print("=" * 60)
            for code, info in sorted(families.items()):
                print(f"  {code:<6} {info['name']:<45} ({info['count']} controls)")
            print("=" * 60)

    elif args.search:
        results = search_controls(args.search, controls=controls)
        if not results:
            print(f"No controls found matching '{args.search}'.", file=sys.stderr)
            sys.exit(1)
        if args.json:
            print(json.dumps(results, indent=2))
        else:
            print(f"Search results for '{args.search}': {len(results)} controls found\n")
            for ctrl in results:
                print(format_control(ctrl, verbose=False))


if __name__ == "__main__":
    main()

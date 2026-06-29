# CUI // SP-CTI
"""ACE canvas role gap detector — identifies canvases with no representative ACE role.

Reads ``args/component_registry.yaml`` for enabled canvases, checks
``args/ace/roles/`` for a role with ``canvas: <key>`` field, and reports gaps.

Usage::

    python tools/ace/canvas_role_gap.py --json
    python tools/ace/canvas_role_gap.py          # human-readable table

Output (JSON)::

    {
      "total_canvases": 44,
      "covered": 3,
      "gaps": [
        {"canvas_key": "network", "display_name": "Network Canvas", "enabled": true},
        ...
      ],
      "covered_by": [
        {"canvas_key": "dic", "role_id": "dic_analyst", "trust_tier": "yellow"},
        ...
      ]
    }
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).parents[3].resolve()
_REGISTRY_PATH = _REPO_ROOT / "args" / "component_registry.yaml"
_ROLES_DIR = _REPO_ROOT / "args" / "ace" / "roles"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def detect_gaps() -> dict[str, Any]:
    """Detect canvases that have no matching ACE role.

    Returns:
        Dict with keys: total_canvases, covered, gaps, covered_by.
    """
    canvases = _load_canvases()
    role_canvas_map = _build_role_canvas_map()

    gaps: list[dict[str, Any]] = []
    covered_by: list[dict[str, Any]] = []

    for canvas in canvases:
        key = canvas["key"]
        if key in role_canvas_map:
            covered_by.append({
                "canvas_key": key,
                "display_name": canvas.get("display_name", key),
                "role_id": role_canvas_map[key]["role_id"],
                "trust_tier": role_canvas_map[key].get("trust_tier", "yellow"),
            })
        else:
            gaps.append({
                "canvas_key": key,
                "display_name": canvas.get("display_name", key),
                "enabled": canvas.get("enabled", True),
            })

    return {
        "total_canvases": len(canvases),
        "covered": len(covered_by),
        "gap_count": len(gaps),
        "gaps": gaps,
        "covered_by": covered_by,
    }


# ---------------------------------------------------------------------------
# Data loaders
# ---------------------------------------------------------------------------


def _load_canvases() -> list[dict[str, Any]]:
    """Return list of canvas entries from component_registry.yaml."""
    try:
        import yaml

        data = yaml.safe_load(_REGISTRY_PATH.read_text(encoding="utf-8")) or {}
        components = data.get("components", [])
        # Include canvas and child_app components (features/core_extensions are not user-facing canvases)
        return [
            c for c in components
            if isinstance(c, dict) and c.get("kind") in ("canvas", "child_app")
            and c.get("key")
        ]
    except Exception:
        return []


def _build_role_canvas_map() -> dict[str, dict[str, Any]]:
    """Return {canvas_key: {role_id, trust_tier}} for roles that declare canvas: <key>."""
    try:
        import yaml
    except ImportError:
        return {}

    canvas_map: dict[str, dict[str, Any]] = {}
    for yaml_path in sorted(_ROLES_DIR.glob("*.yaml")):
        try:
            data = yaml.safe_load(yaml_path.read_text(encoding="utf-8")) or {}
        except Exception:
            continue
        canvas_key = data.get("canvas", "").strip()
        role_id = data.get("role_id", "")
        if canvas_key and role_id:
            canvas_map[canvas_key] = {
                "role_id": role_id,
                "trust_tier": data.get("trust_tier", "yellow"),
            }
    return canvas_map


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _render_table(result: dict[str, Any]) -> str:
    lines = [
        "ACE Canvas Role Gap Report",
        f"  Total canvases : {result['total_canvases']}",
        f"  Covered        : {result['covered']}",
        f"  Gaps           : {result['gap_count']}",
        "",
        "GAPS (canvases with no ACE role):",
        f"  {'Key':<30} {'Display Name':<40} {'Enabled'}",
        "  " + "-" * 75,
    ]
    for g in result["gaps"]:
        lines.append(
            f"  {g['canvas_key']:<30} {g['display_name']:<40} {'yes' if g['enabled'] else 'no'}"
        )
    if not result["gaps"]:
        lines.append("  (none — all canvases covered!)")
    lines.append("")
    lines.append("COVERED (canvases with a role):")
    lines.append(f"  {'Key':<30} {'Role ID':<35} {'Trust'}")
    lines.append("  " + "-" * 75)
    for c in result["covered_by"]:
        lines.append(
            f"  {c['canvas_key']:<30} {c['role_id']:<35} {c['trust_tier']}"
        )
    if not result["covered_by"]:
        lines.append("  (none)")
    return "\n".join(lines)


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="ACE canvas role gap detector")
    parser.add_argument("--json", action="store_true", help="Output JSON")
    args = parser.parse_args()

    result = detect_gaps()

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(_render_table(result))


if __name__ == "__main__":
    main()

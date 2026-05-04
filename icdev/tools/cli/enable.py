#!/usr/bin/env python3
# CUI // SP-CTI
"""`icdev enable|disable|status` — manage canvas + subsystem toggles in .env.

Each canvas is gated by 1-2 env flags. Manually editing .env is error-prone
(you might set ICDEV_BDC_ENABLED=true but forget ICDEV_BOUNDARY_ENABLED=true
and the canvas silently stays disabled). These commands update all flags
atomically.

Usage:
    icdev enable boundary security pipeline    # flip all required flags to true
    icdev disable network                       # flip both flags to false
    icdev status                                # show current state per canvas
    icdev status --json                         # machine-readable
    icdev enable --list                         # list supported toggles

All commands operate on ./.env by default (override with --env-file PATH).
Preserves comments and existing formatting.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path


# Canonical name → list of required env flags (all must be true to enable).
# Kept in sync with args/awareness_enablement_map.yaml.
TOGGLES: dict[str, list[str]] = {
    # Design canvases
    "boundary":      ["ICDEV_BOUNDARY_ENABLED", "ICDEV_BDC_ENABLED"],
    "data":          ["ICDEV_DATA_CANVAS_ENABLED", "ICDEV_DDC_ENABLED"],
    "infra":         ["ICDEV_INFRA_ENABLED", "ICDEV_IDC_ENABLED"],
    "network":       ["ICDEV_NETWORK_ENABLED", "ICDEV_NDC_ENABLED"],
    "observability": ["ICDEV_OBSERVABILITY_ENABLED", "ICDEV_ODC_ENABLED"],
    "pipeline":      ["ICDEV_PIPELINE_ENABLED", "ICDEV_PDC_ENABLED"],
    "security":      ["ICDEV_SECURITY_ENABLED", "ICDEV_SDC_ENABLED"],
    "quality":       ["ICDEV_QDC_ENABLED"],
    "migration":     ["ICDEV_MIGRATION_CANVAS_ENABLED"],
    "canvas-kg":     ["ICDEV_CANVAS_KG_ENABLED"],
    # Other subsystems
    "rag":           ["RAG_ENABLED"],
    "govcon":        ["ICDEV_GOVCON_ENABLED"],
    "finetune":      ["FINETUNE_ENABLED"],
    "filesync":      ["ICDEV_FILESYNC_ENABLED"],
    "cui-banner":    ["ICDEV_CUI_BANNER_ENABLED"],
    "byok":          ["ICDEV_BYOK_ENABLED"],
    "two-tier-llm":  ["LLM_TWO_TIER_ENABLED"],
}

# Short descriptions for `icdev status` and `--list` output
DESCRIPTIONS: dict[str, str] = {
    "boundary":      "Boundary Design Canvas — ATO boundary + supply chain",
    "data":          "Data Design Canvas — schemas, lineage, quality",
    "infra":         "Infrastructure Design Canvas — cloud, IaC, cost",
    "network":       "Network Design Canvas — topology, routing, capacity",
    "observability": "Observability Design Canvas — logging, monitoring, SRE",
    "pipeline":      "Pipeline Design Canvas — CI/CD, GitOps, stages",
    "security":      "Security Design Canvas — threat model, hardening, STIGs",
    "quality":       "Quality Design Canvas — test strategy, QA/QC",
    "migration":     "Migration Canvas — 7Rs modernization workflows",
    "canvas-kg":     "Canvas knowledge graph (cross-canvas reasoning)",
    "rag":           "RAG subsystem (semantic retrieval across all canvases)",
    "govcon":        "GovCon Intelligence (proposals, CPMP) — parent-only",
    "finetune":      "Fine-tuning pipeline (local model training)",
    "filesync":      "File sync dashboard + conflict resolution",
    "cui-banner":    "Render CUI banner on all dashboard pages",
    "byok":          "Bring-Your-Own-Key tenant LLM isolation",
    "two-tier-llm":  "Two-tier LLM routing (local + Claude review)",
}


def _normalize(val: str) -> bool:
    return val.strip().strip('"').strip("'").lower() in ("true", "1", "yes", "on")


def _parse_env(text: str) -> dict[str, tuple[int, str]]:
    """Parse .env into {flag_name: (line_idx, value_str)}.

    Uses line_idx so we can rewrite in place while preserving comments.
    """
    parsed: dict[str, tuple[int, str]] = {}
    for i, line in enumerate(text.splitlines()):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        m = re.match(r"^([A-Z_][A-Z0-9_]*)\s*=\s*(.*)$", stripped)
        if m:
            parsed[m.group(1)] = (i, m.group(2))
    return parsed


def _rewrite_flags(text: str, updates: dict[str, str]) -> str:
    """Replace flag values in .env text; append if flag is missing."""
    lines = text.splitlines()
    existing = _parse_env(text)
    appended: list = []

    for flag, new_val in updates.items():
        if flag in existing:
            idx, _old = existing[flag]
            lines[idx] = f"{flag}={new_val}"
        else:
            appended.append(f"{flag}={new_val}")

    if appended:
        if lines and lines[-1].strip():
            lines.append("")
        lines.append("# Appended by `icdev enable/disable`")
        lines.extend(appended)

    return "\n".join(lines) + ("\n" if text.endswith("\n") else "")


def _load_env_file(path: Path) -> str:
    if path.exists():
        return path.read_text(encoding="utf-8")
    return ""


def _save_env_file(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _resolve_toggle(name: str) -> list[str] | None:
    """Resolve a user-provided toggle name (case-insensitive, hyphen/underscore)."""
    key = name.lower().replace("_", "-")
    return TOGGLES.get(key)


def set_toggles(env_file: Path, names: list[str], value: bool) -> dict:
    """Set all flags for the given toggle names to value."""
    text = _load_env_file(env_file)
    existing = _parse_env(text)

    updates: dict[str, str] = {}
    unknown: list[str] = []
    per_toggle: list = []

    new_val = "true" if value else "false"
    for raw in names:
        flags = _resolve_toggle(raw)
        if flags is None:
            unknown.append(raw)
            continue
        changed_flags: list = []
        for f in flags:
            current = existing.get(f, (None, "false"))[1]
            if _normalize(current) != value:
                changed_flags.append(f)
                updates[f] = new_val
            else:
                updates[f] = new_val  # still record to ensure consistency
        per_toggle.append({
            "name": raw,
            "flags": flags,
            "changed": changed_flags,
        })

    if updates and not unknown:
        new_text = _rewrite_flags(text, updates)
        _save_env_file(env_file, new_text)

    return {
        "env_file": str(env_file),
        "action": "enable" if value else "disable",
        "toggles": per_toggle,
        "unknown": unknown,
        "flags_updated": len(updates),
        "supported": sorted(TOGGLES.keys()),
    }


def get_status(env_file: Path) -> dict:
    """Return the current on/off state of every known toggle."""
    text = _load_env_file(env_file)
    existing = _parse_env(text)

    rows: list = []
    for name, flags in TOGGLES.items():
        flag_states = {f: _normalize(existing.get(f, (None, "false"))[1]) for f in flags}
        enabled = all(flag_states.values()) if flag_states else False
        rows.append({
            "name": name,
            "enabled": enabled,
            "flags": flag_states,
            "description": DESCRIPTIONS.get(name, ""),
        })

    return {
        "env_file": str(env_file),
        "env_file_exists": env_file.exists(),
        "toggles": rows,
        "enabled_count": sum(1 for r in rows if r["enabled"]),
        "total_count": len(rows),
    }


def _list_toggles() -> dict:
    return {
        "toggles": [
            {"name": n, "flags": TOGGLES[n], "description": DESCRIPTIONS.get(n, "")}
            for n in sorted(TOGGLES.keys())
        ],
    }


def _print_status(result: dict) -> None:
    rows = result["toggles"]
    print(f"Env file: {result['env_file']}  "
          f"({'exists' if result['env_file_exists'] else 'NOT FOUND'})")
    print(f"Enabled: {result['enabled_count']} / {result['total_count']}")
    print()
    # Table: status | name | description | flags
    name_w = max(len(r["name"]) for r in rows) + 2
    for r in rows:
        mark = "[ON ]" if r["enabled"] else "[off]"
        desc = r["description"][:60]
        flags_str = ", ".join(
            f"{f}={'1' if v else '0'}" for f, v in r["flags"].items()
        )
        print(f"  {mark}  {r['name']:<{name_w}}{desc}")
        print(f"         {flags_str}")


def _print_change(result: dict) -> None:
    print(f"Env file: {result['env_file']}")
    if result.get("unknown"):
        print(f"ERROR: unknown toggle(s): {', '.join(result['unknown'])}")
        print(f"Supported: {', '.join(result['supported'])}")
        return
    action = result["action"].upper()
    any_changed = False
    for t in result["toggles"]:
        if t["changed"]:
            any_changed = True
            print(f"  {action:>7}: {t['name']}  (flags set: {', '.join(t['changed'])})")
        else:
            print(f"  no-op : {t['name']}  (already in desired state)")
    if not any_changed:
        print("  No changes made — all targets were already in the desired state.")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="icdev enable",
        description=__doc__.split("\n\n")[0],
    )
    parser.add_argument("action", choices=["enable", "disable", "status", "list"],
                        help="What to do")
    parser.add_argument("names", nargs="*",
                        help="Toggle names (for enable/disable)")
    parser.add_argument("--env-file", default=".env",
                        help="Path to .env (default: ./.env)")
    parser.add_argument("--json", action="store_true", help="JSON output")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    env_file = Path(args.env_file).resolve()

    if args.action == "list":
        result = _list_toggles()
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            print("Supported toggles:")
            for t in result["toggles"]:
                print(f"  {t['name']:<14} {t['description']}")
                print(f"                  flags: {', '.join(t['flags'])}")
        return 0

    if args.action == "status":
        result = get_status(env_file)
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            _print_status(result)
        return 0

    # enable or disable
    if not args.names:
        parser.error(f"{args.action}: need at least one toggle name "
                     f"(try `icdev enable --list` for supported names)")

    value = args.action == "enable"
    result = set_toggles(env_file, args.names, value)
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        _print_change(result)

    return 1 if result.get("unknown") else 0


if __name__ == "__main__":
    sys.exit(main())

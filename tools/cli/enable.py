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
    icdev list                                  # list supported toggles

All commands operate on ./.env by default (override with --env-file PATH).
Preserves comments and existing formatting.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

# Ensure repo root is on sys.path so `tools.config` is importable when run directly.
REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.config.component_registry import get_registry, log_component_audit  # noqa: E402

_REGISTRY = get_registry()


def _actor() -> str:
    return os.environ.get("USER") or os.environ.get("USERNAME") or "cli"

# Canonical name → list of required env flags (all must be true to enable).
# Derived from args/component_registry.yaml (canvas + feature components only).
TOGGLES: dict[str, list[str]] = _REGISTRY.get_cli_toggles()

# Short descriptions for `icdev status` and `--list` output
DESCRIPTIONS: dict[str, str] = _REGISTRY.get_cli_descriptions()


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
    path.write_text(text, encoding="utf-8", newline="")


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

        actor = _actor()
        for t in per_toggle:
            if t["changed"]:
                log_component_audit(
                    event_type="enable" if value else "disable",
                    actor=actor,
                    component_key=t["name"],
                    details={
                        "flags": t["flags"],
                        "changed_flags": t["changed"],
                        "env_file": str(env_file),
                    },
                )

    return {
        "env_file": str(env_file),
        "action": "enable" if value else "disable",
        "toggles": per_toggle,
        "unknown": unknown,
        "flags_updated": len(updates),
        "supported": sorted(TOGGLES.keys()),
    }


def _looks_uninitialized(env_file: Path) -> bool:
    """True if the project has not been scaffolded (no .env or no .claude/).

    A fresh `pip install icdev` user who runs `icdev status` before
    `icdev init` sees an all-off table that looks like a broken install. This
    lets `status` detect that state and point them at the one command they
    missed instead.
    """
    return not env_file.exists() or not (env_file.parent / ".claude").is_dir()


def _init_hint(env_file: Path) -> str:
    """The exact command a fresh user must run, targeting the .env's directory."""
    target = env_file.parent
    where = "." if target == Path.cwd() else str(target)
    return (
        "This project is not initialized yet (no .env / .claude found).\n"
        f"Run:  icdev init {where}\n"
        "  → scaffolds CLAUDE.md, .claude/ (commands, hooks, skills), and a\n"
        "    complete .env listing every canvas/feature flag. Then re-run "
        "`icdev status`.\n"
        "  See docs/ops/airgap-pip-install.md for the full pip-only walkthrough."
    )


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


def _domain_summary() -> dict:
    """Which parent this checkout IS, per icdev_domain.yaml (xit-decl-01).

    Best-effort: a status command must never fail because the declaration is
    unreadable — it reports the error instead.
    """
    try:
        from icdev.core.context import describe

        info = describe(anchor=__file__)
    except Exception as exc:  # noqa: BLE001 - status is diagnostic, never a gate
        return {"error": str(exc)}
    dom = info.get("domain") or {}
    ident = info.get("identity") or {}
    return {
        "key": dom.get("key"),
        "name": dom.get("name"),
        "source": dom.get("source"),
        "root": info.get("paths", {}).get("root"),
        "identity": ident.get("verdict"),
        "database_declared": dom.get("db", {}).get("databases"),
        "database_observed": ident.get("database_observed"),
        "error": info.get("error"),
    }


def _print_status(result: dict) -> None:
    rows = result["toggles"]
    dom = result.get("domain") or {}
    if dom:
        print(f"Domain: {dom.get('key')} ({dom.get('name')}) from {dom.get('source')}  "
              f"root={dom.get('root')}  identity={dom.get('identity', 'unknown').upper()}")
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
    # Windows consoles default to cp1252; the hints below carry "→". Never
    # let a diagnostic command die on its own arrow.
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass
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
        result["domain"] = _domain_summary()
        uninitialized = _looks_uninitialized(env_file)
        result["initialized"] = not uninitialized
        if uninitialized:
            result["init_hint"] = _init_hint(env_file)
        if args.json:
            print(json.dumps(result, indent=2))
        elif uninitialized:
            # Fresh install: point at `icdev init` instead of a bare all-off table.
            print(_init_hint(env_file))
        else:
            _print_status(result)
        return 0

    # enable or disable
    if not args.names:
        parser.error(f"{args.action}: need at least one toggle name "
                     f"(try `icdev list` for supported names)")

    value = args.action == "enable"
    result = set_toggles(env_file, args.names, value)
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        _print_change(result)

    return 1 if result.get("unknown") else 0


if __name__ == "__main__":
    sys.exit(main())

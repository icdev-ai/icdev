#!/usr/bin/env python3
# CUI // SP-CTI
"""`icdev profile` — inspect and apply enterprise core profiles.

Subcommands:
  icdev profile list                  List available profiles
  icdev profile show [<name>]         Show profile details (active profile by default)
  icdev profile apply <name>          Append profile env overrides to .env
  icdev profile apply <name> --dry-run  Preview overrides without writing
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
if REPO_ROOT.name == "icdev":
    REPO_ROOT = REPO_ROOT.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.config.core_profile import (  # noqa: E402
    get_active_profile,
    get_profile,
    load_profiles,
    profile_env_overrides,
)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="icdev profile",
        description=__doc__,
    )
    sub = parser.add_subparsers(dest="action", required=True)

    list_p = sub.add_parser("list", help="List available core profiles")
    list_p.add_argument("--json", action="store_true", help="Emit JSON")

    show = sub.add_parser("show", help="Show profile details")
    show.add_argument("name", nargs="?", help="Profile name (default: active profile from env)")
    show.add_argument("--json", action="store_true", help="Emit JSON")

    apply = sub.add_parser("apply", help="Apply a profile's env overrides to .env")
    apply.add_argument("name", help="Profile name")
    apply.add_argument("--env-file", default=".env", help="Path to .env file")
    apply.add_argument("--dry-run", action="store_true", help="Preview without writing")
    apply.add_argument("--json", action="store_true", help="Emit JSON")

    return parser


def _load_env_text(path: Path) -> str:
    if path.exists():
        return path.read_text(encoding="utf-8")
    return ""


def _parse_env(text: str) -> dict[str, tuple[int, str]]:
    parsed: dict[str, tuple[int, str]] = {}
    for i, line in enumerate(text.splitlines()):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        m = re.match(r"^([A-Z_][A-Z0-9_]*)\s*=\s*(.*)$", stripped)
        if m:
            parsed[m.group(1)] = (i, m.group(2))
    return parsed


def _rewrite_env(text: str, updates: dict[str, str]) -> str:
    lines = text.splitlines()
    existing = _parse_env(text)
    appended: list[str] = []

    for flag, new_val in updates.items():
        if flag in existing:
            idx, _old = existing[flag]
            lines[idx] = f"{flag}={new_val}"
        else:
            appended.append(f"{flag}={new_val}")

    if appended:
        if lines and lines[-1].strip():
            lines.append("")
        lines.append("# Appended by `icdev profile apply`")
        lines.extend(appended)

    return "\n".join(lines) + ("\n" if text.endswith("\n") else "")


def _list_profiles() -> dict:
    profiles = load_profiles()
    return {
        "profiles": [
            {"name": name, "description": p.get("description", "")}
            for name, p in profiles.items()
        ]
    }


def _show_profile(name: str | None) -> dict:
    profile = get_profile(name) if name else get_active_profile()
    if profile is None:
        return {"error": f"Profile '{name or os.environ.get('ICDEV_CORE_PROFILE')}' not found"}
    return {
        "name": name or os.environ.get("ICDEV_CORE_PROFILE"),
        "profile": profile,
        "overrides": profile_env_overrides(profile),
    }


def _apply_profile(name: str, env_file: Path, dry_run: bool) -> dict:
    profile = get_profile(name)
    if profile is None:
        return {"error": f"Profile '{name}' not found"}

    overrides = profile_env_overrides(profile)
    text = _load_env_text(env_file)
    new_text = _rewrite_env(text, overrides)

    if not dry_run and overrides:
        env_file.parent.mkdir(parents=True, exist_ok=True)
        env_file.write_text(new_text, encoding="utf-8")

    return {
        "profile": name,
        "env_file": str(env_file),
        "dry_run": dry_run,
        "overrides": overrides,
        "written": not dry_run and bool(overrides),
    }


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.action == "list":
        result = _list_profiles()
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            print("Available core profiles:")
            for p in result["profiles"]:
                print(f"  {p['name']:<14} {p['description']}")
        return 0

    if args.action == "show":
        result = _show_profile(args.name)
        if "error" in result:
            print(result["error"], file=sys.stderr)
            return 1
        if args.json:
            print(json.dumps(result, indent=2, default=str))
        else:
            print(f"Profile: {result['name']}")
            print(json.dumps(result["profile"], indent=2, default=str))
            if result["overrides"]:
                print("Env overrides (not already set):")
                for k, v in result["overrides"].items():
                    print(f"  {k}={v}")
        return 0

    if args.action == "apply":
        env_file = Path(args.env_file).resolve()
        result = _apply_profile(args.name, env_file, args.dry_run)
        if "error" in result:
            print(result["error"], file=sys.stderr)
            return 1
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            print(f"Profile: {result['profile']}")
            print(f"Env file: {result['env_file']}")
            print(f"Dry run: {result['dry_run']}")
            if result["overrides"]:
                print("Overrides:")
                for k, v in result["overrides"].items():
                    print(f"  {k}={v}")
            else:
                print("No env overrides needed (all values already set or profile empty).")
            if result["written"]:
                print("Wrote updates to .env")
        return 0

    return 2


if __name__ == "__main__":
    sys.exit(main())

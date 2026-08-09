#!/usr/bin/env python3
# CUI // SP-CTI
"""`icdev profile` — inspect and apply enterprise core profiles, and manage
directory-based operator profiles for the standalone agent (sag-prof-01).

Subcommands:
  icdev profile list                  List core + isolation profiles
  icdev profile show [<name>]         Show profile details (active profile by default)
  icdev profile apply <name>          Append core-profile env overrides to .env
  icdev profile apply <name> --dry-run  Preview overrides without writing
  icdev profile create <name>         Scaffold an isolated operator profile
                                        (~/.icdev/profiles/<name>/: env overlay + skills/)
  icdev profile use [<name>]          Set the sticky active profile the SAG runtime
                                        reads at startup ('default' clears isolation)
  icdev profile which                 Print the current sticky active profile
  icdev profile remove <name>         Deregister an isolation profile (--purge to
                                        also delete its state directory)
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

from tools.config.component_registry import log_component_audit  # noqa: E402
from tools.config.core_profile import (  # noqa: E402
    get_active_profile,
    get_profile,
    load_profiles,
    profile_env_overrides,
)


def _actor() -> str:
    return os.environ.get("USER") or os.environ.get("USERNAME") or "cli"


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

    create = sub.add_parser("create", help="Scaffold an isolated operator profile")
    create.add_argument("name", help="Profile name (lowercase, - / _)")
    create.add_argument("--description", default="", help="Optional description")
    create.add_argument("--use", action="store_true", help="Also make it the active profile")
    create.add_argument("--json", action="store_true", help="Emit JSON")

    use = sub.add_parser("use", help="Set the sticky active profile")
    use.add_argument("name", nargs="?", default="default",
                     help="Profile name, or 'default' to clear isolation")
    use.add_argument("--json", action="store_true", help="Emit JSON")

    which = sub.add_parser("which", help="Print the current sticky active profile")
    which.add_argument("--json", action="store_true", help="Emit JSON")

    remove = sub.add_parser("remove", help="Deregister an isolation profile")
    remove.add_argument("name", help="Profile name")
    remove.add_argument("--purge", action="store_true", help="Also delete the state directory")
    remove.add_argument("--json", action="store_true", help="Emit JSON")

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
        env_file.write_text(new_text, encoding="utf-8", newline="")
        log_component_audit(
            event_type="profile_apply",
            actor=_actor(),
            profile_name=name,
            details={
                "overrides": overrides,
                "env_file": str(env_file),
            },
        )

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
        try:
            from tools.agent_runtime import profiles as _iso

            result["isolation_profiles"] = _iso.list_profiles()
        except Exception:  # noqa: BLE001 — isolation registry is best-effort
            result["isolation_profiles"] = []
        if args.json:
            print(json.dumps(result, indent=2, default=str))
        else:
            print("Available core profiles:")
            for p in result["profiles"]:
                print(f"  {p['name']:<14} {p['description']}")
            iso = result.get("isolation_profiles") or []
            if iso:
                print("\nIsolation profiles (~/.icdev/profiles/):")
                for p in iso:
                    mark = " *active" if p.get("active") else ""
                    print(f"  {p['name']:<14} {p.get('description', '') or p.get('state_dir', '')}{mark}")
        return 0

    if args.action == "create":
        from tools.agent_runtime import profiles as _iso

        try:
            info = _iso.create_profile(args.name, args.description)
        except ValueError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        if args.use:
            _iso.set_active(args.name)
            info["active"] = True
        if args.json:
            print(json.dumps(info, indent=2, default=str))
        else:
            print(f"Created isolation profile '{args.name}' at {info['state_dir']}")
            print(f"  env overlay: {info['env']}")
            print(f"  skills dir:  {info['skills']}")
            if args.use:
                print(f"  now active (SAG runtime will use '{args.name}')")
        return 0

    if args.action == "use":
        from tools.agent_runtime import profiles as _iso

        try:
            active = _iso.set_active(args.name)
        except ValueError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        if args.json:
            print(json.dumps({"active": active}, indent=2))
        else:
            if _iso.is_default(active):
                print("Cleared active profile (isolation off — using default).")
            else:
                print(f"Active profile is now '{active}'.")
        return 0

    if args.action == "which":
        from tools.agent_runtime import profiles as _iso

        active = _iso.active_profile() or "default"
        if args.json:
            print(json.dumps({"active": active}, indent=2))
        else:
            print(active)
        return 0

    if args.action == "remove":
        from tools.agent_runtime import profiles as _iso

        try:
            existed = _iso.remove_profile(args.name, purge=args.purge)
        except ValueError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        if args.json:
            print(json.dumps({"removed": existed, "purged": args.purge}, indent=2))
        else:
            print(f"Removed isolation profile '{args.name}'." if existed
                  else f"No such isolation profile '{args.name}'.")
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

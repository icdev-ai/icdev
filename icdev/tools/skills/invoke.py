#!/usr/bin/env python3
# CUI // SP-CTI
"""OPT-41 — Skill invoker for headless (non-Claude Code) execution.

Reads the skill registry (see tools/skills/registry.py) and exposes a
CLI that can:

  - List all skills               : --list
  - Show a skill card             : --show <name>
  - Dry-run: print commands only  : --dry-run <name> [-- ARGS...]
  - Execute a skill               : --exec <name> [-- ARGS...]

In --exec mode the invoker runs each documented `python tools/...`
command in order, substituting `$ARGUMENTS` with the positional args
provided after `--`. Stops on first failure unless `--keep-going`.

Example:
    python tools/skills/invoke.py --list --json
    python tools/skills/invoke.py --show icdev-init
    python tools/skills/invoke.py --exec icdev-status -- --format markdown
    python tools/skills/invoke.py --dry-run icdev-secure -- --scan tools/

The invoker is intentionally conservative: it executes only commands
beginning with `python ` (no shell builtins, no curl, etc.). For skills
whose value is primarily LLM-guided (icdev-intake, icdev-knowledge,
etc.), `--exec` will still print the skill card + any documented tool
commands so a human can follow along.
"""
from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess  # nosec B404 — required for CLI invocation, safely parameterized
import sys
from pathlib import Path
from typing import Any

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, OSError):
    pass

BASE_DIR = Path(__file__).resolve().parents[2]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.skills.registry import load_registry  # noqa: E402


# ---------------------------------------------------------------------------
# Safe command invocation
# ---------------------------------------------------------------------------
_ALLOWED_PREFIXES = ("python tools/", "python -m tools", "python -c")


def _is_safe_command(cmd: str) -> bool:
    """Only run python invocations targeting this repo's tools/ tree."""
    stripped = cmd.strip().lstrip("!").strip()
    return any(stripped.startswith(pfx) for pfx in _ALLOWED_PREFIXES)


def _substitute_args(cmd: str, args: list[str]) -> str:
    """Replace $ARGUMENTS with the space-joined, shell-quoted args tuple."""
    if "$ARGUMENTS" not in cmd and "${ARGUMENTS}" not in cmd:
        return cmd
    joined = " ".join(shlex.quote(a) for a in args)
    return cmd.replace("$ARGUMENTS", joined).replace("${ARGUMENTS}", joined)


def run_command(cmd: str, args: list[str], *, timeout: int = 600,
                cwd: str | Path | None = None) -> dict[str, Any]:
    """Run a single documented command. Returns a dict with exit code + output.

    ``cwd`` overrides the checkout the command runs in — callers dispatching on
    behalf of a git worktree (see tools/genesis/reflexes/kanban.py) must pass it,
    or the scan silently reports on the shared checkout instead of the branch
    under test. It sets PYTHONPATH too, so `import tools.x` resolves to the same
    tree the command is reading.
    """
    expanded = _substitute_args(cmd, args)
    if not _is_safe_command(expanded):
        return {
            "command": cmd,
            "skipped": True,
            "reason": "command prefix not in allowlist (python tools/, python -m tools, python -c)",
        }
    root = str(Path(cwd).resolve()) if cwd else str(BASE_DIR)
    try:
        # Propagate PYTHONPATH so child `python tools/...` invocations can
        # import from this repo (mirrors how Claude Code launches them).
        env = os.environ.copy()
        existing = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = (root + os.pathsep + existing
                             if existing else root)
        env.setdefault("PYTHONIOENCODING", "utf-8")
        result = subprocess.run(  # nosec B603 — invocation is allowlisted
            shlex.split(expanded, posix=False),
            capture_output=True, text=True, cwd=root,
            timeout=timeout, encoding="utf-8", errors="replace", env=env,
        )
        return {
            "command": expanded,
            "returncode": result.returncode,
            "stdout": result.stdout[-4000:],
            "stderr": result.stderr[-4000:],
        }
    except subprocess.TimeoutExpired:
        return {"command": expanded, "error": "timeout", "timeout_seconds": timeout}
    except Exception as exc:
        return {"command": expanded, "error": str(exc)[:300]}


def invoke_skill(name: str, args: list[str], *, dry_run: bool = False,
                 keep_going: bool = False, timeout: int = 600) -> dict[str, Any]:
    """Execute all documented commands for a skill, in order."""
    reg = load_registry()
    skills = reg.get("skills", {})
    if name not in skills:
        return {"error": "skill not found", "name": name,
                "available": sorted(skills.keys())}
    entry = skills[name]
    cmds = entry.get("commands", [])
    steps: list[dict] = []

    for idx, cmd in enumerate(cmds, start=1):
        expanded = _substitute_args(cmd, args)
        if dry_run:
            steps.append({"step": idx, "command": expanded,
                          "would_run": _is_safe_command(expanded)})
            continue
        result = run_command(cmd, args, timeout=timeout)
        result["step"] = idx
        steps.append(result)
        if result.get("returncode", 0) != 0 and not keep_going and not result.get("skipped"):
            break

    executed = [s for s in steps if not s.get("skipped") and "returncode" in s]
    failed = [s for s in executed if s["returncode"] != 0]
    return {
        "skill": name,
        "description": entry.get("description", ""),
        "total_commands": len(cmds),
        "mcp_references": len(entry.get("mcp_references", [])),
        "steps": steps,
        "executed_count": len(executed),
        "failed_count": len(failed),
        "dry_run": dry_run,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _parse_args(argv: list[str] | None = None) -> tuple[argparse.Namespace, list[str]]:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--list", action="store_true", help="List all skills")
    g.add_argument("--show", metavar="SKILL", help="Show a skill card (frontmatter + commands)")
    g.add_argument("--dry-run", metavar="SKILL", help="Preview commands without running them")
    g.add_argument("--exec", dest="execute", metavar="SKILL",
                   help="Execute all documented commands for a skill")
    p.add_argument("--json", action="store_true")
    p.add_argument("--keep-going", action="store_true", help="Do not stop on first failure")
    p.add_argument("--timeout", type=int, default=600, help="Per-command timeout (seconds)")
    p.add_argument("rest", nargs=argparse.REMAINDER,
                   help="Positional args after `--` are forwarded to $ARGUMENTS")
    return p.parse_args(argv), []


def main(argv: list[str] | None = None) -> int:
    args, _ = _parse_args(argv)
    # Strip the leading '--' from argparse's REMAINDER convention
    forwarded = args.rest[1:] if args.rest and args.rest[0] == "--" else args.rest

    if args.list:
        reg = load_registry()
        names = sorted(reg.get("skills", {}).keys())
        if args.json:
            print(json.dumps({"skills": names, "count": len(names)}, indent=2))
        else:
            for n in names:
                e = reg["skills"][n]
                print(f"  {n:22s} cmds={len(e['commands']):2d}  {e['description'][:80]}")
        return 0

    if args.show:
        reg = load_registry()
        entry = reg.get("skills", {}).get(args.show)
        if entry is None:
            print(json.dumps({"error": "skill not found"}) if args.json else "Not found")
            return 1
        if args.json:
            print(json.dumps(entry, indent=2))
        else:
            print(f"=== {entry['name']} ===")
            print(entry["description"])
            print(f"\nAllowed tools: {', '.join(entry.get('allowed_tools', []))}")
            print(f"\nCommands ({len(entry['commands'])}):")
            for i, c in enumerate(entry["commands"], 1):
                print(f"  {i:2d}. {c}")
            if entry.get("mcp_references"):
                print(f"\nMCP references ({len(entry['mcp_references'])}):")
                for r in entry["mcp_references"][:5]:
                    print(f"  - {r[:100]}")
        return 0

    target = args.dry_run or args.execute
    is_dry = bool(args.dry_run)
    result = invoke_skill(target, forwarded, dry_run=is_dry,
                          keep_going=args.keep_going, timeout=args.timeout)

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(f"skill: {result.get('skill') or target}")
        if result.get("error"):
            print(f"  ERROR: {result['error']}")
            return 1
        for step in result.get("steps", []):
            print(f"  step {step['step']}: {step.get('command', '')[:100]}")
            if step.get("skipped"):
                print(f"    SKIPPED — {step.get('reason', '')}")
            elif "returncode" in step:
                print(f"    rc={step['returncode']}")
        print(f"executed={result.get('executed_count', 0)} "
              f"failed={result.get('failed_count', 0)}")

    if result.get("error") or result.get("failed_count", 0) > 0:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

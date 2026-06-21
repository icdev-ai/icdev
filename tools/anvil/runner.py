#!/usr/bin/env python3
# CUI // SP-CTI
"""OPT-42 — Shared ANVIL-command runner.

The 10 core slash commands (feature/bug/chore/test/review/commit/status/
monitor/maintain/secure/deploy) are documented as Claude Code prompts in
.claude/commands/*.md or as skills in .agents/skills/icdev-*/SKILL.md. This
runner lets non-Claude environments trigger the same sequences headlessly by:

  1. Parsing the source file for documented `python tools/...` commands
     (same rules as tools/skills/registry.py).
  2. Substituting `$ARGUMENTS` with positional args forwarded after `--`.
  3. Running the commands with an allowlisted prefix (`python tools/`,
     `python -m tools`, `python -c`) and an inherited PYTHONPATH.
  4. Stopping on the first failure unless `--keep-going` is passed.

The per-command thin wrappers in tools/anvil/{feature,bug,...}.py call
run_command() with their source file path.

A `skill` variant delegates to tools/skills/invoke.py for the 5 commands
that are backed by .agents/skills/icdev-* rather than .claude/commands/*.
"""
from __future__ import annotations

import json
import os
import re
import shlex
import subprocess  # nosec B404 — invocation is allowlisted
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

_ALLOWED_PREFIXES = ("python tools/", "python -m tools", "python -c")
_FENCE_RE = re.compile(r"```(?:bash|shell|sh)?\n(.*?)```", re.DOTALL)
_PY_CMD_RE = re.compile(r"^\s*(?:!)?\s*(python(?:\s+-m)?\s+\S+[^\n]*)",
                        re.MULTILINE)


# ---------------------------------------------------------------------------
# Command extraction
# ---------------------------------------------------------------------------
def extract_commands(markdown_path: Path) -> list[str]:
    """Dedup python `tools/...` commands in fenced bash blocks of a markdown file."""
    text = markdown_path.read_text(encoding="utf-8", errors="replace")
    cmds: list[str] = []
    seen: set[str] = set()
    for fence in _FENCE_RE.findall(text):
        for m in _PY_CMD_RE.finditer(fence):
            cmd = m.group(1).strip()
            if " #" in cmd:
                cmd = cmd.split(" #", 1)[0].rstrip()
            if cmd and cmd not in seen:
                seen.add(cmd)
                cmds.append(cmd)
    return cmds


def _is_background(cmd: str) -> bool:
    """True when the command ends with ' &' — shell background syntax unsupported by subprocess."""
    return cmd.rstrip().endswith(" &")


def _is_safe(cmd: str) -> bool:
    stripped = cmd.strip().lstrip("!").strip()
    return any(stripped.startswith(p) for p in _ALLOWED_PREFIXES)


def _substitute(cmd: str, args: list[str]) -> str:
    if "$ARGUMENTS" not in cmd and "${ARGUMENTS}" not in cmd:
        return cmd
    joined = " ".join(shlex.quote(a) for a in args)
    return cmd.replace("$ARGUMENTS", joined).replace("${ARGUMENTS}", joined)


def _run_one(cmd: str, args: list[str], *, timeout: int = 600) -> dict[str, Any]:
    expanded = _substitute(cmd, args)
    if _is_background(expanded):
        return {"command": cmd, "skipped": True,
                "reason": "background process (& suffix) — not supported in headless runner"}
    if not _is_safe(expanded):
        return {"command": cmd, "skipped": True,
                "reason": "prefix not in allowlist"}
    env = os.environ.copy()
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = (str(BASE_DIR) + os.pathsep + existing
                         if existing else str(BASE_DIR))
    env.setdefault("PYTHONIOENCODING", "utf-8")
    try:
        result = subprocess.run(  # nosec B603 — allowlisted
            shlex.split(expanded, posix=False),
            capture_output=True, text=True, cwd=str(BASE_DIR),
            timeout=timeout, encoding="utf-8", errors="replace", env=env,
        )
        return {"command": expanded, "returncode": result.returncode,
                "stdout": result.stdout[-4000:], "stderr": result.stderr[-4000:]}
    except subprocess.TimeoutExpired:
        return {"command": expanded, "error": "timeout", "timeout_seconds": timeout}
    except Exception as exc:
        return {"command": expanded, "error": str(exc)[:300]}


# ---------------------------------------------------------------------------
# Public runners
# ---------------------------------------------------------------------------
def _wiki_navigate_context(args: list[str]) -> dict[str, Any] | None:
    """Run the Navigate wiki query as a pre-step before manifest grep.

    Queries memory wiki for tool-relevant entries using the $ARGUMENTS as the
    task description.  Returns a synthetic step dict so the result appears in
    the run_command steps list.  Best-effort — never blocks the main pipeline.
    """
    try:
        from tools.memory.wiki_tool_query import wiki_tool_query, _format_context_block

        query = " ".join(args) if args else ""
        if not query.strip():
            return None
        results = wiki_tool_query(query, top_k=5)
        context = _format_context_block(results)
        return {
            "step": 0,
            "command": f"wiki_navigate_context({query[:60]!r})",
            "returncode": 0,
            "stdout": context,
            "stderr": "",
            "wiki_hits": len(results),
        }
    except Exception as exc:
        return {
            "step": 0,
            "command": "wiki_navigate_context",
            "skipped": True,
            "reason": str(exc)[:200],
        }


def run_command(source_rel: str, args: list[str], *,
                dry_run: bool = False, keep_going: bool = False,
                timeout: int = 600, wiki_navigate: bool = True) -> dict[str, Any]:
    """Run an ANVIL command whose source is a .claude/commands/<name>.md file.

    Args:
        source_rel: repo-relative path to the .md source file
        args: positional arguments forwarded to $ARGUMENTS
        dry_run: preview commands without executing
        keep_going: do not stop on first failure
        timeout: per-command wall-clock cap (seconds)
        wiki_navigate: when True (default), run a wiki query as step 0 before
            the manifest grep (Karpathy Navigate wiki integration, Item 3).
    """
    src_path = (BASE_DIR / source_rel).resolve()
    if not src_path.exists():
        return {"error": "source not found", "source": source_rel}

    cmds = extract_commands(src_path)
    steps: list[dict] = []

    # Karpathy Navigate wiki pre-step (Item 3): surface wiki knowledge before
    # grepping the manifest so Navigate can reuse institutional know-how.
    if wiki_navigate and not dry_run and args:
        wiki_step = _wiki_navigate_context(args)
        if wiki_step:
            steps.append(wiki_step)

    for idx, cmd in enumerate(cmds, start=1):
        expanded = _substitute(cmd, args)
        if dry_run:
            steps.append({"step": idx, "command": expanded,
                          "would_run": _is_safe(expanded)})
            continue
        result = _run_one(cmd, args, timeout=timeout)
        result["step"] = idx
        steps.append(result)
        if result.get("returncode", 0) != 0 and not keep_going \
                and not result.get("skipped"):
            break

    executed = [s for s in steps if not s.get("skipped") and "returncode" in s]
    failed = [s for s in executed if s["returncode"] != 0]
    return {
        "source": source_rel,
        "total_commands": len(cmds),
        "dry_run": dry_run,
        "steps": steps,
        "executed_count": len(executed),
        "failed_count": len(failed),
    }


def run_skill(skill_name: str, args: list[str], *,
              dry_run: bool = False, keep_going: bool = False,
              timeout: int = 600) -> dict[str, Any]:
    """Delegate to tools.skills.invoke.invoke_skill (OPT-41)."""
    from tools.skills.invoke import invoke_skill
    return invoke_skill(skill_name, args, dry_run=dry_run,
                        keep_going=keep_going, timeout=timeout)


# ---------------------------------------------------------------------------
# CLI plumbing used by per-command wrappers
# ---------------------------------------------------------------------------
def cli_main(*, wrapper_name: str, source: str, kind: str = "md") -> int:
    """Reusable main() for tools/anvil/<name>.py entry points."""
    import argparse
    p = argparse.ArgumentParser(
        prog=f"tools/anvil/{wrapper_name}.py",
        description=f"ANVIL headless {wrapper_name} runner — source: {source}")
    p.add_argument("--dry-run", action="store_true",
                   help="Preview commands without running them")
    p.add_argument("--keep-going", action="store_true",
                   help="Do not stop on first failure")
    p.add_argument("--timeout", type=int, default=600,
                   help="Per-command timeout in seconds")
    p.add_argument("--json", action="store_true")
    p.add_argument("rest", nargs=argparse.REMAINDER,
                   help="Positional args after `--` forwarded to $ARGUMENTS")
    args = p.parse_args()
    forwarded = args.rest[1:] if args.rest and args.rest[0] == "--" else args.rest

    if kind == "skill":
        result = run_skill(source, forwarded, dry_run=args.dry_run,
                           keep_going=args.keep_going, timeout=args.timeout)
    else:
        result = run_command(source, forwarded, dry_run=args.dry_run,
                             keep_going=args.keep_going, timeout=args.timeout)

    if args.json:
        print(json.dumps(result, indent=2, default=str))
    else:
        print(f"anvil {wrapper_name}: {result.get('source') or result.get('skill')}")
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
    # Self-test: list available commands
    print("ANVIL runner — this module is intended to be imported by "
          "tools/anvil/<name>.py wrappers.")
    print("Commands discovered in .claude/commands:")
    cmds_dir = BASE_DIR / ".claude" / "commands"
    if cmds_dir.exists():
        for f in sorted(cmds_dir.glob("*.md")):
            cmds = extract_commands(f)
            print(f"  {f.stem:15s}  {len(cmds)} python cmd(s)")

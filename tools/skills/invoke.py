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

ars-scope-01 — capability scoping
---------------------------------
A SKILL.md may narrow that allowlist with two OPTIONAL frontmatter
fields:

    paths:  repo-relative directories/files the skill may act on
    tools:  tool modules the skill may invoke (`tools/db/` or `tools.db`)

Both are enforced here, at the same seam as `_ALLOWED_PREFIXES` — scope
can only ever narrow it, never widen it. A skill that omits both fields
behaves exactly as it did before. A scoped skill whose command reaches
outside its declaration is BLOCKED (the command is not executed and the
invocation exits non-zero) — it is never merely warned about.
"""
from __future__ import annotations

import argparse
import json
import os
import re
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

from tools.skills.registry import _as_list, _parse_frontmatter, load_registry  # noqa: E402


# ---------------------------------------------------------------------------
# Safe command invocation
# ---------------------------------------------------------------------------
_ALLOWED_PREFIXES = ("python tools/", "python -m tools", "python -c")


def _is_safe_command(cmd: str) -> bool:
    """Only run python invocations targeting this repo's tools/ tree."""
    stripped = cmd.strip().lstrip("!").strip()
    return any(stripped.startswith(pfx) for pfx in _ALLOWED_PREFIXES)


# ---------------------------------------------------------------------------
# ars-scope-01 — optional `paths:` / `tools:` scoping, enforced not warned
# ---------------------------------------------------------------------------
_DOTTED_TOOLS_RE = re.compile(r"\btools(?:\.[A-Za-z_][A-Za-z0-9_]*)+")
_CODE_PATH_RE = re.compile(r"""["']([^"'\s]*[/\\][^"'\s]*)["']""")
_URL_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*://")
_GLOB_CHARS = ("*", "?", "[")

EMPTY_SCOPE: dict[str, list[str]] = {"paths": [], "tools": []}


def resolve_scope(entry: dict[str, Any], *, root: Path | None = None) -> dict[str, list[str]]:
    """Return ``{'paths': [...], 'tools': [...]}`` declared by a skill.

    Read from the live SKILL.md when it is present: ``registry.json`` is a
    committed cache and a stale copy would drop the scoping fields, which
    would fail OPEN (a scoped skill running unscoped). The cached entry is
    used only when the card itself cannot be read, so a skill recorded as
    scoped stays scoped.
    """
    base = Path(root) if root else BASE_DIR
    rel = entry.get("path")
    if rel:
        card = base / rel
        try:
            if card.is_file():
                fm, _ = _parse_frontmatter(card.read_text(encoding="utf-8", errors="replace"))
                return {"paths": _as_list(fm.get("paths")), "tools": _as_list(fm.get("tools"))}
        except OSError:
            pass
    return {"paths": _as_list(entry.get("paths")), "tools": _as_list(entry.get("tools"))}


def is_scoped(scope: dict[str, list[str]] | None) -> bool:
    return bool(scope and (scope.get("paths") or scope.get("tools")))


def _norm_target(value: str) -> str:
    """Normalise a tool reference to a comparable posix-ish path."""
    v = value.strip().strip('"').strip("'").replace("\\", "/")
    if v.endswith(".py"):
        v = v[:-3]
    elif "/" not in v and v.count(".") >= 1:
        v = v.replace(".", "/")          # dotted module: tools.db.storage
    v = v.strip("/")
    while v.startswith("./"):
        v = v[2:]
    return v


def _split_command(cmd: str) -> tuple[list[str], bool]:
    """Tokenise a documented command; flag whether it is a python invocation."""
    stripped = cmd.strip().lstrip("!").strip()
    try:
        toks = shlex.split(stripped, posix=False)
    except ValueError:
        toks = stripped.split()
    head = Path(toks[0]).name.lower() if toks else ""
    return toks, head.startswith("python")


def _command_tool_targets(cmd: str) -> list[str]:
    """Tool modules a command would execute (script, ``-m``, ``-c`` imports)."""
    toks, is_python = _split_command(cmd)
    if not is_python:
        return []          # not a python invocation — nothing resolvable
    targets: list[str] = []
    i = 1  # toks[0] is the interpreter
    while i < len(toks):
        tok = toks[i]
        if tok == "-m" and i + 1 < len(toks):
            targets.append(_norm_target(toks[i + 1]))
            break
        if tok == "-c" and i + 1 < len(toks):
            for m in _DOTTED_TOOLS_RE.findall(toks[i + 1]):
                targets.append(_norm_target(m))
            break
        if tok.startswith("-"):
            i += 1
            continue
        targets.append(_norm_target(tok))     # the script path
        break
    return [t for t in targets if t]


def _tool_allowed(target: str, declared: list[str]) -> bool:
    for raw in declared:
        if any(ch in raw for ch in _GLOB_CHARS):
            from fnmatch import fnmatch
            if fnmatch(target, _norm_target(raw)) or fnmatch(target + ".py", raw):
                return True
            continue
        allowed = _norm_target(raw)
        if allowed and (target == allowed or target.startswith(allowed + "/")):
            return True
    return False


def _command_path_operands(cmd: str) -> list[str]:
    """Path-like operands a command would act on (its script target excluded).

    ``paths:`` constrains what a skill acts *on*; ``tools:`` constrains what
    it acts *with*. Requiring the script itself to sit inside ``paths:``
    would force every scoped skill to declare ``tools/``, which scopes
    nothing.
    """
    toks, is_python = _split_command(cmd)
    operands: list[str] = []
    i = 1
    # For a non-python command there is no script token to exempt, so every
    # path-like token counts as an operand.
    script_seen = not is_python
    while i < len(toks):
        tok = toks[i]
        if tok in ("-m", "-c") and i + 1 < len(toks):
            if tok == "-c":
                operands.extend(_CODE_PATH_RE.findall(toks[i + 1]))
            i += 2
            script_seen = True
            continue
        if tok.startswith("-"):
            if "=" in tok:
                operands.append(tok.split("=", 1)[1])
            i += 1
            continue
        if not script_seen:
            script_seen = True          # the script path itself, not an operand
            i += 1
            continue
        operands.append(tok)
        i += 1
    out: list[str] = []
    for raw in operands:
        val = raw.strip().strip('"').strip("'")
        if not val or _URL_RE.match(val):
            continue
        if _looks_like_path(val):
            out.append(val)
    return out


def _looks_like_path(value: str) -> bool:
    if "/" in value or "\\" in value:
        return True
    if re.match(r"^[A-Za-z]:[\\/]", value):
        return True
    return (BASE_DIR / value).exists()


def _is_within(child: Path, parent: Path) -> bool:
    c = os.path.normcase(str(child))
    p = os.path.normcase(str(parent)).rstrip("\\/")
    return c == p or c.startswith(p + os.sep) or c.startswith(p + "/")


def _abs(value: str, root: Path) -> Path:
    p = Path(value)
    target = p if p.is_absolute() else root / p
    try:
        return target.resolve()
    except OSError:
        return target.absolute()


def check_scope(scope: dict[str, list[str]] | None, cmd: str, *,
                root: Path | None = None) -> list[dict[str, Any]]:
    """Return scope violations for ``cmd``. Empty list means allowed."""
    if not is_scoped(scope):
        return []
    assert scope is not None
    base = Path(root).resolve() if root else BASE_DIR
    violations: list[dict[str, Any]] = []

    declared_tools = scope.get("tools") or []
    if declared_tools:
        targets = _command_tool_targets(cmd)
        if not targets:
            # Fail closed: a command whose tool target cannot be resolved
            # cannot be shown to be inside the declaration.
            violations.append({"field": "tools", "value": "<unresolved>",
                               "declared": list(declared_tools)})
        for target in targets:
            if not _tool_allowed(target, declared_tools):
                violations.append({"field": "tools", "value": target,
                                   "declared": list(declared_tools)})

    declared_paths = scope.get("paths") or []
    if declared_paths:
        roots = [_abs(p, base) for p in declared_paths]
        for operand in _command_path_operands(cmd):
            resolved = _abs(operand, base)
            if not any(_is_within(resolved, r) for r in roots):
                violations.append({"field": "paths", "value": operand,
                                   "resolved": str(resolved),
                                   "declared": list(declared_paths)})
    return violations


def _blocked_step(cmd: str, violations: list[dict[str, Any]]) -> dict[str, Any]:
    detail = "; ".join(f"{v['field']}: {v['value']} outside {v['declared']}" for v in violations)
    return {
        "command": cmd,
        "blocked": True,
        "violations": violations,
        "error": f"skill scope violation — {detail}",
    }


def _substitute_args(cmd: str, args: list[str]) -> str:
    """Replace $ARGUMENTS with the space-joined, shell-quoted args tuple."""
    if "$ARGUMENTS" not in cmd and "${ARGUMENTS}" not in cmd:
        return cmd
    joined = " ".join(shlex.quote(a) for a in args)
    return cmd.replace("$ARGUMENTS", joined).replace("${ARGUMENTS}", joined)


def run_command(cmd: str, args: list[str], *, timeout: int = 600,
                cwd: str | Path | None = None,
                scope: dict[str, list[str]] | None = None) -> dict[str, Any]:
    """Run a single documented command. Returns a dict with exit code + output.

    ``cwd`` overrides the checkout the command runs in — callers dispatching on
    behalf of a git worktree (see tools/genesis/reflexes/kanban.py) must pass it,
    or the scan silently reports on the shared checkout instead of the branch
    under test. It sets PYTHONPATH too, so `import tools.x` resolves to the same
    tree the command is reading.

    ``scope`` is the invoking skill's ``paths:``/``tools:`` declaration. It is
    checked before the prefix allowlist and before anything is executed, so a
    command reaching outside the declared scope is blocked rather than run.
    """
    expanded = _substitute_args(cmd, args)
    root_for_scope = Path(cwd).resolve() if cwd else BASE_DIR
    violations = check_scope(scope, expanded, root=root_for_scope)
    if violations:
        return _blocked_step(expanded, violations)
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
    scope = resolve_scope(entry)
    scoped = is_scoped(scope)

    for idx, cmd in enumerate(cmds, start=1):
        expanded = _substitute_args(cmd, args)
        violations = check_scope(scope, expanded)
        if violations:
            # A declared scope is a hard boundary: stop the whole invocation,
            # in dry-run too, so `--dry-run` doubles as a static scope check.
            step = _blocked_step(expanded, violations)
            step["step"] = idx
            if dry_run:
                step["would_run"] = False
            steps.append(step)
            break
        if dry_run:
            steps.append({"step": idx, "command": expanded,
                          "would_run": _is_safe_command(expanded)})
            continue
        result = run_command(cmd, args, timeout=timeout, scope=scope)
        result["step"] = idx
        steps.append(result)
        if result.get("blocked"):
            break
        if result.get("returncode", 0) != 0 and not keep_going and not result.get("skipped"):
            break

    executed = [s for s in steps if not s.get("skipped") and "returncode" in s]
    failed = [s for s in executed if s["returncode"] != 0]
    blocked = [s for s in steps if s.get("blocked")]
    return {
        "skill": name,
        "description": entry.get("description", ""),
        "total_commands": len(cmds),
        "mcp_references": len(entry.get("mcp_references", [])),
        "steps": steps,
        "executed_count": len(executed),
        "failed_count": len(failed),
        "blocked_count": len(blocked),
        "scoped": scoped,
        "scope": scope,
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
            scope = resolve_scope(entry)
            if is_scoped(scope):
                print(f"Scoped paths: {', '.join(scope['paths']) or '(unscoped)'}")
                print(f"Scoped tools: {', '.join(scope['tools']) or '(unscoped)'}")
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
            if step.get("blocked"):
                print(f"    BLOCKED — {step.get('error', '')}")
            elif step.get("skipped"):
                print(f"    SKIPPED — {step.get('reason', '')}")
            elif "returncode" in step:
                print(f"    rc={step['returncode']}")
        print(f"executed={result.get('executed_count', 0)} "
              f"failed={result.get('failed_count', 0)} "
              f"blocked={result.get('blocked_count', 0)}")

    if (result.get("error") or result.get("failed_count", 0) > 0
            or result.get("blocked_count", 0) > 0):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

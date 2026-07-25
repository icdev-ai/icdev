#!/usr/bin/env python3
# CUI // SP-CTI
"""`icdev init` — Scaffold a new ICDEV™ project from the installed package.

Copies the FORGE orchestration layer from the installed icdev package into
the user's project directory so Claude Code (and humans) have everything
needed to drive ICDEV™:

    my-project/
    ├── CLAUDE.md            (master instructions for Claude Code)
    ├── .mcp.json            (MCP server configuration)
    ├── .env.template        (env var template — copy to .env and edit)
    ├── .claude/
    │   ├── commands/        (slash commands: /prime, /commit, /start, ...)
    │   ├── hooks/           (session hooks: stop, pre_tool_use, ...)
    │   ├── settings.json    (Claude Code settings)
    │   └── skills/          (ICDEV-specific skills)
    ├── args/                (deterministic behavior configs — editable)
    ├── goals/               (FORGE goal workflow definitions — editable)
    ├── hardprompts/         (reusable LLM instruction templates — editable)
    ├── context/             (reference material: tone, samples, case studies)
    ├── data/                (sqlite dbs, project-local)
    └── docs/                (project-local documentation)

After `icdev init`, users open the project in Claude Code and it "just works".

Usage:
    icdev init                        # scaffold in cwd (prompts before overwrite)
    icdev init my-project             # scaffold into ./my-project/
    icdev init --force                # overwrite existing files
    icdev init --minimal              # CLAUDE.md + .claude/ only (skip FORGE data)
    icdev init --list                 # just show what would be copied
    icdev init --json                 # JSON output
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from importlib import resources
from pathlib import Path


def _package_resource_path(resource: str) -> Path | None:
    """Resolve a path inside the installed icdev package.

    Tries importlib.resources first (wheel install), falls back to direct
    filesystem resolution (development checkout).
    """
    try:
        # importlib.resources.files is Python 3.9+
        pkg_root = resources.files("icdev")
        target = pkg_root / resource
        if target.is_dir() or target.is_file():
            return Path(str(target))
    except Exception:
        pass
    # Dev fallback: relative to this file
    dev_root = Path(__file__).resolve().parent.parent.parent / "icdev"
    candidate = dev_root / resource
    if candidate.exists():
        return candidate
    return None


def _list_files(root: Path) -> list[Path]:
    """Recursively list files under root (excluding __pycache__ etc.)."""
    out: list = []
    if not root.exists():
        return out
    if root.is_file():
        return [root]
    for p in root.rglob("*"):
        if p.is_file():
            name = p.name
            if name == "__pycache__" or name.endswith(".pyc"):
                continue
            out.append(p)
    return out


# Mapping: bootstrap-source → project-target
BOOTSTRAP_MAP: list[tuple[str, str]] = [
    ("data/claude_bootstrap/CLAUDE.md", "CLAUDE.md"),
    ("data/claude_bootstrap/mcp.json", ".mcp.json"),
    ("data/claude_bootstrap/.env.template", ".env.template"),
    ("data/claude_bootstrap/claude/commands", ".claude/commands"),
    ("data/claude_bootstrap/claude/hooks", ".claude/hooks"),
    ("data/claude_bootstrap/claude/settings.json.template", ".claude/settings.json"),
    ("data/claude_bootstrap/claude/skills", ".claude/skills"),
    ("data/claude_bootstrap/claude/agents", ".claude/agents"),
]

# Bootstrap sources that may legitimately be absent from the package (e.g.
# `.claude/agents` ships zero files today but will the day agents are added).
# A missing OPTIONAL source is reported as "optional_missing" and does NOT
# count toward the `missing` total or flip the process exit code — a mapped
# entry with no source is a benign no-op, never an init failure.
OPTIONAL_SOURCES: set[str] = {
    "data/claude_bootstrap/claude/agents",
}

# FORGE data (editable project config, copied from package defaults)
FORGE_MAP: list[tuple[str, str]] = [
    ("data/args", "args"),
    ("data/goals", "goals"),
    ("data/hardprompts", "hardprompts"),
    ("data/context", "context"),
]


def _copy_one(src: Path, dst: Path, force: bool = False) -> tuple[bool, str]:
    """Copy one file or directory. Returns (copied, message)."""
    if not src.exists():
        return False, f"source missing: {src}"
    if dst.exists() and not force:
        return False, f"exists (use --force to overwrite): {dst}"

    try:
        if src.is_file():
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            return True, f"copied file: {dst}"
        if src.is_dir():
            if dst.exists() and force:
                shutil.rmtree(dst)
            shutil.copytree(src, dst, ignore=shutil.ignore_patterns(
                "__pycache__", "*.pyc", "*.log", "*.pid"
            ))
            return True, f"copied tree: {dst}"
    except Exception as exc:
        return False, f"copy failed ({src} -> {dst}): {exc}"
    return False, "unknown error"


def init_project(
    target_dir: Path,
    force: bool = False,
    minimal: bool = False,
    list_only: bool = False,
) -> dict:
    """Scaffold a new ICDEV project.

    Args:
        target_dir: Where to create the project (must exist or will be created).
        force: Overwrite existing files.
        minimal: Only copy CLAUDE.md + .claude/ (skip FORGE data).
        list_only: Don't copy — just report what would happen.

    Returns a dict with success/failure per item.
    """
    target_dir.mkdir(parents=True, exist_ok=True)
    actions: list = []

    sources = list(BOOTSTRAP_MAP)
    if not minimal:
        sources.extend(FORGE_MAP)

    for pkg_rel, proj_rel in sources:
        src = _package_resource_path(pkg_rel)
        dst = target_dir / proj_rel

        if src is None:
            status = ("optional_missing" if pkg_rel in OPTIONAL_SOURCES
                      else "source_missing")
            actions.append({"src": pkg_rel, "dst": str(proj_rel),
                            "status": status})
            continue

        if list_only:
            n_files = len(_list_files(src))
            actions.append({"src": str(src), "dst": str(dst),
                            "status": "would_copy", "files": n_files})
            continue

        ok, msg = _copy_one(src, dst, force=force)
        actions.append({"src": str(src), "dst": str(dst),
                        "status": "copied" if ok else "skipped", "message": msg})

    # Write a complete .env: template's non-component keys + a generated
    # section covering EVERY component env flag from the registry, so no
    # canvas/feature is ever silently undiscoverable (the reported bug).
    if not list_only:
        env_template = target_dir / ".env.template"
        env_file = target_dir / ".env"
        if env_template.exists() and not env_file.exists():
            msg = "created .env from template"
            try:
                from tools.cli.env_generator import compose_env
                from tools.config.component_registry import get_registry

                template_text = env_template.read_text(encoding="utf-8")
                composed = compose_env(template_text, get_registry())
                env_file.write_text(composed, encoding="utf-8")
                msg = "created .env from template + registry component flags"
            except Exception as exc:
                # Never let registry generation block init — fall back to the
                # static template so `icdev init` always produces a usable .env.
                shutil.copy2(env_template, env_file)
                msg = f"created .env from template (registry augmentation skipped: {exc})"
            actions.append({"src": ".env.template", "dst": str(env_file),
                            "status": "copied", "message": msg})

    summary = {
        "target": str(target_dir.resolve()),
        "minimal": minimal,
        "force": force,
        "list_only": list_only,
        "actions": actions,
        "copied": sum(1 for a in actions if a["status"] == "copied"),
        "skipped": sum(1 for a in actions if a["status"] == "skipped"),
        "missing": sum(1 for a in actions if a["status"] == "source_missing"),
        "optional_missing": sum(
            1 for a in actions if a["status"] == "optional_missing"
        ),
    }
    return summary


def _next_steps(target: Path) -> str:
    return (
        f"\nNext steps:\n"
        f"  1. cd {target}\n"
        f"  2. Edit .env to add your API keys (ANTHROPIC_API_KEY, etc.)\n"
        f"  3. icdev-init-db                 # initialize databases\n"
        f"  4. icdev-dashboard               # start dashboard on :5050\n"
        f"  5. Open the project in Claude Code — CLAUDE.md will guide the agent.\n"
        f"\n"
        f"Canvases (Tech Writer, Notebook, BI Dashboard, etc.) are opt-in —\n"
        f"run 'icdev list' to see them, 'icdev enable <name>' to turn one on.\n"
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="icdev init",
        description=__doc__.split("\n\n")[0],
    )
    parser.add_argument("target", nargs="?", default=".",
                        help="Target directory (default: current directory)")
    parser.add_argument("--force", action="store_true",
                        help="Overwrite existing files")
    parser.add_argument("--minimal", action="store_true",
                        help="Only scaffold CLAUDE.md + .claude/ (skip FORGE data)")
    parser.add_argument("--list", dest="list_only", action="store_true",
                        help="Show what would be copied without copying")
    parser.add_argument("--json", action="store_true", help="JSON output")
    args = parser.parse_args()

    target = Path(args.target).resolve()
    result = init_project(
        target,
        force=args.force,
        minimal=args.minimal,
        list_only=args.list_only,
    )

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(f"Target: {result['target']}")
        for a in result["actions"]:
            icon = {"copied": "[+]", "skipped": "[=]",
                    "source_missing": "[!]",
                    "optional_missing": "[~]",
                    "would_copy": "[?]"}.get(a["status"], "[?]")
            print(f"  {icon} {a['dst']}  ({a.get('message', a['status'])})")
        print(f"\nCopied: {result['copied']}   Skipped: {result['skipped']}   "
              f"Missing: {result['missing']}")
        if not args.list_only and result["copied"] > 0:
            print(_next_steps(target))

    return 0 if result["missing"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())

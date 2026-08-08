#!/usr/bin/env python3
# CUI // SP-CTI
"""Pre-build prep: populate icdev/data/claude_bootstrap/ from repo root.

Run BEFORE `python -m build` to copy the orchestration layer into the
package so `pip install icdev` ships everything a user needs to drive
Claude Code:

    icdev/data/claude_bootstrap/
    ├── CLAUDE.md              (master instruction file)
    ├── mcp.json               (MCP server configuration)
    ├── .env.template          (env var template)
    ├── claude/                (was .claude/ — dots in package dirs are finicky)
    │   ├── commands/          (slash command definitions)
    │   └── hooks/             (session hooks — stop, pre_tool_use, etc.)
    └── README.md              (how to use the bootstrap)

Users bootstrap their project with `icdev init` (see tools/cli/init.py)
which copies this content into their cwd.

Usage:
    python tools/installer/prebuild_bootstrap.py
    python tools/installer/prebuild_bootstrap.py --json
    python tools/installer/prebuild_bootstrap.py --clean   # delete before copying

Also runs automatically from the PyPI deploy pipeline.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
BOOTSTRAP_DIR = REPO_ROOT / "icdev" / "data" / "claude_bootstrap"

# Files/dirs to copy from repo-root → bootstrap
SOURCES: list[tuple[str, str, str]] = [
    # (repo-root path, bootstrap-dir relative path, kind: "file" | "dir")
    ("CLAUDE.md", "CLAUDE.md", "file"),
    (".mcp.json", "mcp.json", "file"),
    (".env.example", ".env.template", "file"),
    (".env.sample", ".env.sample", "file"),
    (".claude/commands", "claude/commands", "dir"),
    (".claude/hooks", "claude/hooks", "dir"),
    # hgx-guard-01/02: pre_tool_use.py is a thin adapter over
    # tools/hooks/shared_checks.py and FAILS OPEN without it. The hook resolves
    # the module as <project>/tools/hooks/shared_checks.py, so it has to ship
    # beside the hook or every `icdev init` project gets a guard that guards
    # nothing. Deliberately NOT in OPTIONAL_SOURCES: a wheel missing it should
    # fail init loudly rather than scaffold a dead guardrail.
    ("tools/hooks", "tools/hooks", "dir"),
    (".claude/settings.json", "claude/settings.json.template", "file"),
    (".agents/skills", "claude/skills", "dir"),
    (".claude/agents", "claude/agents", "dir"),
]

# ICDEV is LLM-agnostic: the same guardrails ship for every major AI coding
# tool, not just Claude Code. All ten of these were tracked in git and NONE of
# them reached the wheel, so `pip install icdev && icdev init` produced a
# Claude-only project. Sourced from tools/dx/ai_platforms.py so the generator,
# the wheel and `icdev init` cannot drift apart.
# REPO_ROOT must be on sys.path FIRST. Run as a script, `sys.path[0]` is
# `tools/installer/`, not the repo root, so a bare `from tools.dx...` import
# fails — and an earlier version swallowed that failure, silently dropping all
# ten platform files from the wheel while reporting a successful build. The
# import is load-bearing for packaging correctness, so it raises now.
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.dx.ai_platforms import (  # noqa: E402
    AI_PLATFORM_FILES,
    bootstrap_name,
)

SOURCES.extend(
    (rel, bootstrap_name(rel), "file") for _platform, rel in AI_PLATFORM_FILES
)

# Sources that may legitimately not exist yet. A missing OPTIONAL source is
# recorded under `skipped_optional` and does NOT go to `errors`, so the
# prebuild step (and the build that runs it) never fails just because an
# as-yet-unpopulated directory — e.g. `.claude/agents` — is absent. Keep this
# in sync with OPTIONAL_SOURCES in tools/cli/init.py so a map entry added here
# is collected when populated and silently skipped when empty.
OPTIONAL_SOURCES: set[str] = {
    ".claude/agents",
}

# Files to exclude from directory copies (test artifacts, user-specific state)
EXCLUDE_NAMES = {
    "__pycache__",
    ".pyc",
    ".DS_Store",
    "scheduled_tasks.lock",
    "*.log",
    "*.pid",
}


def _should_exclude(path: Path) -> bool:
    name = path.name
    if name in EXCLUDE_NAMES:
        return True
    if name.endswith(".pyc") or name.endswith(".log") or name.endswith(".pid"):
        return True
    return False


def _copy_file(src: Path, dst: Path) -> dict:
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)
    return {"src": str(src.relative_to(REPO_ROOT)), "dst": str(dst.relative_to(REPO_ROOT)),
            "size": dst.stat().st_size}


def _copy_tree(src: Path, dst: Path) -> dict:
    copied = 0
    skipped = 0
    for p in src.rglob("*"):
        if _should_exclude(p):
            skipped += 1
            continue
        if p.is_file():
            rel = p.relative_to(src)
            target = dst / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(p, target)
            copied += 1
    return {"src": str(src.relative_to(REPO_ROOT)), "dst": str(dst.relative_to(REPO_ROOT)),
            "files_copied": copied, "files_skipped": skipped}


def run(clean: bool = False) -> dict:
    """Copy orchestration-layer files into icdev/data/claude_bootstrap/."""
    if clean and BOOTSTRAP_DIR.exists():
        shutil.rmtree(BOOTSTRAP_DIR)
    BOOTSTRAP_DIR.mkdir(parents=True, exist_ok=True)

    results: list = []
    errors: list = []
    skipped_optional: list = []

    for rel_src, rel_dst, kind in SOURCES:
        src = REPO_ROOT / rel_src
        dst = BOOTSTRAP_DIR / rel_dst
        if not src.exists():
            if rel_src in OPTIONAL_SOURCES:
                skipped_optional.append(rel_src)
            else:
                errors.append(f"missing: {rel_src}")
            continue
        try:
            if kind == "file":
                results.append(_copy_file(src, dst))
            elif kind == "dir":
                results.append(_copy_tree(src, dst))
        except Exception as exc:
            errors.append(f"copy {rel_src} -> {rel_dst} failed: {exc}")

    # Create an index README so users know what they got
    readme = BOOTSTRAP_DIR / "README.md"
    readme.write_text(
        "# ICDEV™ Claude Bootstrap\n\n"
        "This directory ships with the `icdev` PyPI package. It contains the\n"
        "FORGE orchestration layer that makes Claude Code work with ICDEV™:\n\n"
        "- **CLAUDE.md** — master instruction file for Claude Code\n"
        "- **mcp.json** — MCP server configuration\n"
        "- **.env.template** — environment variable template\n"
        "- **claude/commands/** — slash command definitions (`/prime`, `/commit`, etc.)\n"
        "- **claude/hooks/** — session hooks (stop, pre_tool_use, post_tool_use)\n"
        "- **claude/skills/** — ICDEV-specific Claude skills\n\n"
        "## Bootstrap a new project\n\n"
        "```bash\n"
        "mkdir my-icdev-project && cd my-icdev-project\n"
        "icdev init            # copies this bootstrap into cwd\n"
        "icdev-init-db         # initializes the databases\n"
        "icdev-dashboard       # starts the dashboard on :5050\n"
        "```\n\n"
        "After `icdev init`, the project is ready — open it in Claude Code\n"
        "and the agent will follow CLAUDE.md.\n",
        encoding="utf-8",
    )

    return {
        "bootstrap_dir": str(BOOTSTRAP_DIR.relative_to(REPO_ROOT)),
        "sources_copied": results,
        "errors": errors,
        "skipped_optional": skipped_optional,
        "total_files": sum(
            r.get("files_copied", 1) for r in results
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--clean", action="store_true",
                        help="Delete bootstrap dir before copying")
    args = parser.parse_args()

    result = run(clean=args.clean)
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(f"Bootstrap dir: {result['bootstrap_dir']}")
        print(f"Total files copied: {result['total_files']}")
        for r in result["sources_copied"]:
            if "files_copied" in r:
                print(f"  + {r['src']} -> {r['dst']}: "
                      f"{r['files_copied']} files (skipped {r['files_skipped']})")
            else:
                print(f"  + {r['src']} -> {r['dst']}: {r['size']} bytes")
        for rel_src in result.get("skipped_optional", []):
            print(f"  ~ {rel_src}: absent (optional) — skipped, not an error")
        if result["errors"]:
            print("\nErrors:")
            for e in result["errors"]:
                print(f"  ! {e}")
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

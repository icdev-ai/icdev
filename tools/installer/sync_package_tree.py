#!/usr/bin/env python3
# CUI // SP-CTI
"""Pre-build sync: mirror the full repo into icdev/ for PyPI packaging.

Run BEFORE `python -m build` to ensure the wheel includes the COMPLETE
FORGE + ANVIL framework plus all canvases and engines — everything a
user needs to build systems with ICDEV™.

What it syncs:
    tools/         → icdev/tools/         (all tool subsystems, excl. parent-only)
    goals/         → icdev/data/goals/    (FORGE goals layer)
    args/          → icdev/data/args/     (FORGE args layer)
    context/       → icdev/data/context/  (FORGE context layer)
    hardprompts/   → icdev/data/hardprompts/ (FORGE hardprompts layer)
    features/      → icdev/data/features/ (BDD features)
    docs/          → icdev/data/docs/     (documentation)
    memory/        → icdev/data/memory/   (memory templates)
    schemas/       → icdev/tools/schemas/ (shared schemas)
    plus orchestration bootstrap (see prebuild_bootstrap.py).

Parent-only dirs (NOT shipped in child-app wheel):
    saas, govcon, rfx, marketplace, creative, gateway, playground,
    trading, market_intel (FathomDesk is a separate product).

Usage:
    python tools/installer/sync_package_tree.py           # sync + report
    python tools/installer/sync_package_tree.py --json    # JSON output
    python tools/installer/sync_package_tree.py --dry-run # preview only
    python tools/installer/sync_package_tree.py --clean   # remove icdev/tools first

Also runs prebuild_bootstrap.py for CLAUDE.md + .claude/ orchestration layer.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
PKG_DIR = REPO_ROOT / "icdev"
PKG_TOOLS = PKG_DIR / "tools"
PKG_DATA = PKG_DIR / "data"

# Canonical content for icdev/tools/__init__.py — see sync_tools() for why
# this is written directly rather than copied from tools/__init__.py.
ICDEV_TOOLS_INIT = '''"""ICDEV™ tools package — deterministic execution modules.

All new code should use the canonical namespace ``icdev.tools.*``.
The root-level ``tools`` package is a backward-compatibility shim that
redirects ``tools.xxx`` imports to ``icdev.tools.xxx``.
"""
'''

# Subsystems NOT shipped in the public icdev wheel.
# These are owner-specific child apps / parent-platform services that users
# of `pip install icdev` don't need (they build their OWN systems with the
# framework, not operate the owner's child apps).
#
# The goal: ship EVERYTHING needed to use ICDEV as a framework to build
# other systems and run all the canvases, EXCEPT owner-operated child apps.
#
# Framework/core subsystems that ARE shipped (do not add these here):
#   - All 9 canvases (boundary, security, data, infra, migration,
#     observability, qdc, network, pipeline, + canvas orchestrator)
#   - Genesis daemon + reflexes (autonomous engine)
#   - Oracle, Awareness (internal awareness engine)
#   - RAG, Kanban, Memory, Notifications
#   - Writing (WriteGuard — shared library, used BY Pulse etc.)
#   - ANVIL, AppForge, Compliance, MBSE, Modernization
#   - All infra: ci, db, dashboard, mcp, llm, testing, observability, ...
PARENT_ONLY_DIRS = {
    # Owner-operated child apps
    "pulse",             # Pulse AI blog engine (owner's blog product)
    "proposal_genesis",  # GovCon proposal capture pipeline
    "govcon",            # GovCon intelligence
    "rfx",               # legacy (folded into govcon)
    "autoresearch",      # autonomous research (GovCon-specific)
    "scout",             # Genesis Scout reflex (owner's intel gathering)
    "creative",          # Creative engine (owner's competitor discovery)
    "trading",           # FathomDesk (separate product)
    "market_intel",      # FathomDesk dependency
    # Parent-platform services (not for end users)
    "saas",              # SaaS multi-tenancy platform
    "marketplace",       # Federated FORGE asset marketplace
    "gateway",           # Remote Command Gateway
    "playground",        # internal experiments
}

# Top-level directories to mirror into icdev/data/ for FORGE framework
FORGE_DATA_DIRS = [
    "goals",
    "args",
    "context",
    "hardprompts",
]

# Additional data directories
EXTRA_DATA_DIRS = [
    "features",
    "docs",
]

# Files / patterns to exclude from every copy
EXCLUDE_PATTERNS = (
    "__pycache__",
    "*.pyc",
    "*.pyo",
    ".DS_Store",
    "*.log",
    "*.pid",
    "*.lock",
    "scheduled_tasks.lock",
    "*.egg-info",
    # Runtime state that shouldn't ship (users initialize their own DBs)
    "*.db",
    "*.db-journal",
    "*.sqlite",
    "*.sqlite3",
    # Dev screenshots and binary test artifacts
    "*.png",
    "*.jpg",
    "*.jpeg",
    "*.gif",
    "*.mp4",
    "*.mov",
    # Backup / swap files
    "*.bak",
    "*.swp",
    "*.orig",
)

# Directories to exclude wholesale (path suffix match)
EXCLUDE_DIRS = (
    "screenshots",       # dashboard test screenshots (1.2 MB)
    "logs",              # runtime logs
    "__pycache__",
)


def _should_skip(path: Path) -> bool:
    name = path.name
    # File-name pattern match
    for pat in EXCLUDE_PATTERNS:
        if pat.startswith("*"):
            if name.endswith(pat[1:]):
                return True
        elif name == pat:
            return True
    # Directory-name match (wholesale exclude)
    if path.is_dir() and name in EXCLUDE_DIRS:
        return True
    # Also skip if any parent dir name is in EXCLUDE_DIRS
    for part in path.parts:
        if part in EXCLUDE_DIRS:
            return True
    return False


#: A `tools/x.py` that only re-exports from `icdev.tools.x` — the back-compat
#: shim documented in CLAUDE.md. `icdev.tools.*` is the CANONICAL namespace, so
#: for these modules the real implementation lives in the mirror and the source
#: file is the stub.
_SHIM_RE = re.compile(r"^\s*from\s+icdev\.tools\.[\w.]+\s+import\b", re.M)


def _is_backcompat_shim(src_file: Path, target: Path) -> bool:
    """True when copying ``src_file`` would DESTROY a real implementation.

    Copying a shim over its own twin produces a module that imports from
    itself — `from icdev.tools.llm.agent_loop import DONE` inside
    `icdev/tools/llm/agent_loop.py` — which raises ImportError on a partially
    initialized module the first time anything imports it.

    That is not hypothetical: a sync run replaced the 1,825-line
    `icdev/tools/llm/agent_loop.py` with its 89-line shim and the result was
    published to PyPI, where `import icdev.tools.llm.agent_loop` failed outright.

    The guard is deliberately narrow — it only refuses when the source really is
    a shim (small, and importing from the canonical package) AND a substantially
    larger implementation already exists at the target. A genuine module that
    merely happens to import from `icdev.tools.*` still syncs.
    """
    if src_file.suffix != ".py" or not target.exists():
        return False
    try:
        src_text = src_file.read_text(encoding="utf-8", errors="replace")
        dst_text = target.read_text(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001 - unreadable file is not a shim decision
        return False
    if not _SHIM_RE.search(src_text):
        return False
    # A shim is a stub. If the target is not meaningfully bigger, the source is
    # the implementation and must win as usual.
    return len(dst_text.splitlines()) > len(src_text.splitlines()) * 2


def _copy_tree(src: Path, dst: Path, dry_run: bool = False) -> dict:
    """Copy a directory tree honoring EXCLUDE_PATTERNS."""
    if not src.exists():
        return {"src": str(src), "error": "source missing"}
    copied = 0
    skipped = 0
    for p in src.rglob("*"):
        if _should_skip(p):
            skipped += 1
            continue
        # Also skip if ANY parent component is excluded
        if any(_should_skip(parent) for parent in p.parents):
            continue
        if p.is_file():
            rel = p.relative_to(src)
            target = dst / rel
            if _is_backcompat_shim(p, target):
                skipped += 1
                continue
            if not dry_run:
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(p, target)
            copied += 1
    return {
        "src": str(src.relative_to(REPO_ROOT)),
        "dst": str(dst.relative_to(REPO_ROOT)),
        "files_copied": copied,
        "files_skipped": skipped,
    }


def sync_tools(dry_run: bool = False, clean: bool = False) -> list:
    """Mirror tools/ into icdev/tools/ excluding parent-only dirs."""
    src_root = REPO_ROOT / "tools"
    if not src_root.exists():
        return [{"error": f"{src_root} missing"}]

    if clean and not dry_run and PKG_TOOLS.exists():
        shutil.rmtree(PKG_TOOLS)

    PKG_TOOLS.mkdir(parents=True, exist_ok=True)

    # icdev/tools/__init__.py is the real package init and must never be
    # the copy of tools/__init__.py (the backward-compat shim, meant only
    # for the dev repo where both tools/ and icdev/tools/ coexist on
    # sys.path). Shipping the shim as icdev/tools/__init__.py makes the
    # installed wheel self-referential: icdev.tools.__init__ would import
    # from a non-existent top-level `tools` package, causing a circular
    # ImportError on first `import icdev` in any environment that isn't
    # the dev repo. Written unconditionally (even under --clean, which
    # deletes icdev/tools/ before this function repopulates it).
    if not dry_run:
        (PKG_TOOLS / "__init__.py").write_text(ICDEV_TOOLS_INIT, encoding="utf-8")

    results: list = []
    # Copy top-level files (manifest.md, helper .py files). __init__.py is
    # excluded — see the icdev/tools/__init__.py write above.
    for child in src_root.iterdir():
        if _should_skip(child) or child.name == "__init__.py":
            continue
        if child.is_file():
            if not dry_run:
                shutil.copy2(child, PKG_TOOLS / child.name)
            results.append({
                "src": str(child.relative_to(REPO_ROOT)),
                "dst": str((PKG_TOOLS / child.name).relative_to(REPO_ROOT)),
                "kind": "file",
                "copied": 1,
            })
            continue
        if child.is_dir():
            if child.name in PARENT_ONLY_DIRS:
                results.append({
                    "src": str(child.relative_to(REPO_ROOT)),
                    "skipped_parent_only": True,
                })
                continue
            target = PKG_TOOLS / child.name
            r = _copy_tree(child, target, dry_run=dry_run)
            r["kind"] = "dir"
            results.append(r)
    return results


def sync_data(dry_run: bool = False) -> list:
    """Mirror FORGE layer dirs (goals/, args/, context/, hardprompts/, features/, docs/)
    into icdev/data/.
    """
    PKG_DATA.mkdir(parents=True, exist_ok=True)
    results: list = []
    for name in FORGE_DATA_DIRS + EXTRA_DATA_DIRS:
        src = REPO_ROOT / name
        if not src.exists():
            results.append({"src": name, "skipped": "missing"})
            continue
        dst = PKG_DATA / name
        r = _copy_tree(src, dst, dry_run=dry_run)
        results.append(r)
    return results


# Sample apps shipped in the icdev wheel (non-private, referenced in README)
SAMPLE_APPS = [
    "autonomous_coder",   # Autonomous Coder sample app — /autonomous-coder/ dashboard route
    "ai_gameday",         # AI GameDay + TTX Engine — /gameday/ dashboard route
]


def sync_apps(dry_run: bool = False) -> list:
    """Mirror selected sample apps from apps/ into icdev/apps/ for PyPI inclusion."""
    src_root = REPO_ROOT / "apps"
    pkg_apps = PKG_DIR / "apps"
    if not src_root.exists():
        return [{"error": f"{src_root} missing"}]

    results: list = []
    # Ensure icdev/apps/__init__.py exists so it's a proper Python package
    if not dry_run:
        pkg_apps.mkdir(parents=True, exist_ok=True)
        init = pkg_apps / "__init__.py"
        if not init.exists():
            init.write_text("# CUI // SP-CTI\n")

    for app_name in SAMPLE_APPS:
        src = src_root / app_name
        if not src.exists():
            results.append({"src": f"apps/{app_name}", "skipped": "missing"})
            continue
        dst = pkg_apps / app_name
        r = _copy_tree(src, dst, dry_run=dry_run)
        r["kind"] = "sample_app"
        results.append(r)
    return results


def run(dry_run: bool = False, clean: bool = False) -> dict:
    """Full prebuild sync: tools + data + sample apps + orchestration bootstrap."""
    result = {
        "repo_root": str(REPO_ROOT),
        "pkg_dir": str(PKG_DIR),
        "dry_run": dry_run,
        "clean": clean,
        "tools_sync": [],
        "data_sync": [],
        "apps_sync": [],
        "bootstrap": {},
    }

    print("[1/4] Syncing tools/ -> icdev/tools/ ...", file=sys.stderr)
    result["tools_sync"] = sync_tools(dry_run=dry_run, clean=clean)

    print("[2/4] Syncing FORGE data layers -> icdev/data/ ...", file=sys.stderr)
    result["data_sync"] = sync_data(dry_run=dry_run)

    print("[3/4] Syncing sample apps -> icdev/apps/ ...", file=sys.stderr)
    result["apps_sync"] = sync_apps(dry_run=dry_run)

    # Run prebuild_bootstrap.py (CLAUDE.md + .claude/ + skills)
    if not dry_run:
        print("[4/4] Running prebuild_bootstrap.py ...", file=sys.stderr)
        try:
            bs = subprocess.run(
                [sys.executable, "tools/installer/prebuild_bootstrap.py",
                 "--clean", "--json"],
                capture_output=True, text=True, encoding="utf-8", errors="replace",
                cwd=str(REPO_ROOT), timeout=60,
            )
            if bs.returncode == 0 and bs.stdout.strip():
                try:
                    result["bootstrap"] = json.loads(bs.stdout)
                except Exception:
                    result["bootstrap"] = {"raw": bs.stdout[-500:]}
            else:
                result["bootstrap"] = {
                    "error": f"exit {bs.returncode}",
                    "stderr": bs.stderr[-500:],
                }
        except Exception as exc:
            result["bootstrap"] = {"error": str(exc)}

    # Totals
    total_tools = sum(r.get("files_copied", 0) for r in result["tools_sync"])
    total_data = sum(r.get("files_copied", 0) for r in result["data_sync"])
    total_apps = sum(r.get("files_copied", 0) for r in result["apps_sync"])
    total_bs = result["bootstrap"].get("total_files", 0)
    result["totals"] = {
        "tools_files": total_tools,
        "data_files": total_data,
        "apps_files": total_apps,
        "bootstrap_files": total_bs,
        "grand_total": total_tools + total_data + total_apps + total_bs,
    }
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--clean", action="store_true",
                        help="Remove icdev/tools/ before syncing")
    args = parser.parse_args()

    result = run(dry_run=args.dry_run, clean=args.clean)
    if args.json:
        print(json.dumps(result, indent=2))
        return 0

    print()
    print("=== Sync Summary ===")
    print(f"Tools:     {result['totals']['tools_files']:>6} files")
    print(f"Data:      {result['totals']['data_files']:>6} files")
    print(f"Apps:      {result['totals']['apps_files']:>6} files")
    print(f"Bootstrap: {result['totals']['bootstrap_files']:>6} files")
    print(f"TOTAL:     {result['totals']['grand_total']:>6} files")
    print()

    # Show any skipped parent-only dirs and errors
    parent_skipped = [r for r in result["tools_sync"] if r.get("skipped_parent_only")]
    errors = [r for r in result["tools_sync"] + result["data_sync"] if "error" in r]
    if parent_skipped:
        print(f"Skipped parent-only dirs: {len(parent_skipped)}")
        for r in parent_skipped:
            print(f"  - {r['src']}")
    if errors:
        print(f"\nErrors: {len(errors)}")
        for e in errors:
            print(f"  ! {e}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

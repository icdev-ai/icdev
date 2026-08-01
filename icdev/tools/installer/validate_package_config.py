#!/usr/bin/env python3
# CUI // SP-CTI
"""Pre-build guard: validate the PyPI package is configured correctly.

Runs a checklist before `python -m build` to prevent broken releases:

  1. PARENT_ONLY_DIRS in sync across three files:
        - tools/installer/sync_package_tree.py (authoritative)
        - pyproject.toml [tool.setuptools.packages.find] exclude
        - MANIFEST.in  recursive-exclude icdev/tools/<name>/

  2. All framework/core subsystems are present in icdev/tools/ after sync

  3. Claude bootstrap is populated (CLAUDE.md + .claude/commands/ + hooks)

  4. FORGE layer data directories exist under icdev/data/

  5. Entry points in pyproject.toml resolve to actual files

  6. .env.example has every variable .env.sample defines — .env.sample is
     the comprehensive reference; .env.example seeds `icdev init`'s
     .env.template and tools/awareness/enablement.py's runtime defaults,
     so a var missing from .env.example is invisible to a fresh
     `pip install icdev` user even though it's fully shipped and working.

  7. .env.example documents every enablement env_flag declared in
     args/component_registry.yaml (the authoritative source for canvases
     and components). A canvas/component whose env_flag is absent here is
     undiscoverable to a pip-install user — this is the root-cause guard
     for the DIC/Tech Writer "where did it go?" class of bug.

  8. The committed bootstrap snapshot (icdev/data/claude_bootstrap/) matches
     the LIVE source trees by file set AND content. The snapshot is refreshed
     only by prebuild_bootstrap.py; a plain `python -m build` skips it, so a
     release cut without the sync ships a stale command/skill set. Fails with
     the exact added/removed/changed paths. --fix re-runs prebuild_bootstrap.

Exit code 0 if everything is green, 1 on any failure. Designed to run as
a pre-commit hook, in CI, and inside tools/installer/build_release.py.

Usage:
    python tools/installer/validate_package_config.py           # human output
    python tools/installer/validate_package_config.py --json    # machine output
    python tools/installer/validate_package_config.py --gate    # non-zero on fail
    python tools/installer/validate_package_config.py --fix     # refresh snapshot
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

try:
    import yaml as _yaml
except ImportError:
    _yaml = None  # type: ignore[assignment]

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

# CHECK 8 imports tools.installer.prebuild_bootstrap. Running this file as a
# script puts tools/installer/ on sys.path[0] — not the repo root — so that
# import raised ModuleNotFoundError unless PYTHONPATH already carried the root,
# and the check reported itself as a hard FAIL:
#
#   [FAIL] bootstrap_freshness
#          error: could not import prebuild_bootstrap: No module named 'tools'
#
# CI sets PYTHONPATH, so this only bit the documented local invocation and
# build_release.py, where it blocked the release at the validate step. Resolved
# from __file__ rather than cwd so it holds from a worktree or any directory.
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

PKG_DIR = REPO_ROOT / "icdev"
SYNC_SCRIPT = REPO_ROOT / "tools" / "installer" / "sync_package_tree.py"
PYPROJECT = REPO_ROOT / "pyproject.toml"
MANIFEST = REPO_ROOT / "MANIFEST.in"
_PKG_CONFIG = REPO_ROOT / "args" / "package_config.yaml"
ENV_EXAMPLE = REPO_ROOT / ".env.example"
ENV_SAMPLE = REPO_ROOT / ".env.sample"
COMPONENT_REGISTRY = REPO_ROOT / "args" / "component_registry.yaml"


def _load_pkg_config() -> dict:
    """Load args/package_config.yaml; return empty dict on any failure."""
    if _yaml is None or not _PKG_CONFIG.exists():
        return {}
    try:
        with open(_PKG_CONFIG, encoding="utf-8") as fh:
            return _yaml.safe_load(fh) or {}
    except Exception:
        return {}


def _min_slash_command_count() -> int:
    cfg = _load_pkg_config()
    return int(cfg.get("validate", {}).get("min_slash_command_count", 40))


# ---------------------------------------------------------------------------
# Source of truth: read PARENT_ONLY_DIRS from sync_package_tree.py
# ---------------------------------------------------------------------------
def _parse_parent_only_dirs() -> set[str]:
    """Extract PARENT_ONLY_DIRS names from sync_package_tree.py."""
    src = SYNC_SCRIPT.read_text(encoding="utf-8")
    match = re.search(
        r"PARENT_ONLY_DIRS\s*=\s*\{([^}]+)\}", src, re.DOTALL
    )
    if not match:
        return set()
    body = match.group(1)
    names: set[str] = set()
    for line in body.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        m = re.match(r'"([^"]+)"', line)
        if m:
            names.add(m.group(1))
    return names


def _parse_pyproject_excludes() -> set[str]:
    """Extract dir names from pyproject.toml [tool.setuptools.packages.find] exclude.

    Returns the base dir names (e.g. 'pulse' from 'icdev.tools.pulse*').
    """
    src = PYPROJECT.read_text(encoding="utf-8")
    match = re.search(
        r"\[tool\.setuptools\.packages\.find\].*?exclude\s*=\s*\[(.+?)\]",
        src, re.DOTALL,
    )
    if not match:
        return set()
    body = match.group(1)
    names: set[str] = set()
    for m in re.finditer(r'"icdev\.tools\.([a-zA-Z_][a-zA-Z0-9_]*)\*?"', body):
        names.add(m.group(1))
    return names


def _parse_manifest_excludes() -> set[str]:
    """Extract dir names from MANIFEST.in recursive-exclude lines."""
    src = MANIFEST.read_text(encoding="utf-8")
    names: set[str] = set()
    for m in re.finditer(
        r"recursive-exclude\s+icdev/tools/([a-zA-Z_][a-zA-Z0-9_]*)\s",
        src,
    ):
        names.add(m.group(1))
    return names


# ---------------------------------------------------------------------------
# Required framework/core subsystems (must exist in icdev/tools/ after sync)
# ---------------------------------------------------------------------------
REQUIRED_SUBSYSTEMS = [
    # 9 canvases + orchestrator
    "boundary_canvas", "security_canvas", "data_canvas", "infra_canvas",
    "migration_canvas", "observability_canvas", "qdc_canvas",
    "network", "pipeline", "canvas",
    # Autonomous engine
    "genesis", "oracle", "awareness", "kanban",
    # Core capabilities
    "rag", "memory", "notifications", "writing",
    # Build frameworks
    "anvil", "appforge", "builder",
    # Compliance stack
    "compliance", "mbse", "modernization",
    # Infrastructure
    "ci", "db", "dashboard", "mcp", "llm", "testing",
    "observability", "monitor", "workflow", "dx", "installer", "cli",
]

REQUIRED_BOOTSTRAP_FILES = [
    "icdev/data/claude_bootstrap/CLAUDE.md",
    "icdev/data/claude_bootstrap/mcp.json",
    "icdev/data/claude_bootstrap/.env.template",
    "icdev/data/claude_bootstrap/claude/settings.json.template",
]

REQUIRED_BOOTSTRAP_DIRS = [
    "icdev/data/claude_bootstrap/claude/commands",
    "icdev/data/claude_bootstrap/claude/hooks",
    "icdev/data/claude_bootstrap/claude/skills",
]

REQUIRED_DATA_DIRS = [
    "icdev/data/args",
    "icdev/data/goals",
    "icdev/data/hardprompts",
    "icdev/data/context",
]


# ---------------------------------------------------------------------------
# Validation checks
# ---------------------------------------------------------------------------
def check_parent_only_sync() -> dict:
    """CHECK 1: PARENT_ONLY_DIRS matches pyproject.toml and MANIFEST.in."""
    truth = _parse_parent_only_dirs()
    pyproj = _parse_pyproject_excludes()
    manifest = _parse_manifest_excludes()

    missing_in_pyproj = truth - pyproj
    missing_in_manifest = truth - manifest
    extra_in_pyproj = pyproj - truth
    extra_in_manifest = manifest - truth

    ok = not any((missing_in_pyproj, missing_in_manifest,
                  extra_in_pyproj, extra_in_manifest))
    return {
        "check": "parent_only_sync",
        "ok": ok,
        "parent_only_count": len(truth),
        "missing_in_pyproject": sorted(missing_in_pyproj),
        "missing_in_manifest": sorted(missing_in_manifest),
        "extra_in_pyproject": sorted(extra_in_pyproj),
        "extra_in_manifest": sorted(extra_in_manifest),
    }


def check_required_subsystems() -> dict:
    """CHECK 2: all required framework subsystems exist in icdev/tools/."""
    missing: list = []
    present: list = []
    tools_dir = PKG_DIR / "tools"
    for name in REQUIRED_SUBSYSTEMS:
        path = tools_dir / name
        if path.exists() and any(path.iterdir()):
            present.append(name)
        else:
            missing.append(name)
    return {
        "check": "required_subsystems",
        "ok": not missing,
        "required_count": len(REQUIRED_SUBSYSTEMS),
        "present_count": len(present),
        "missing": missing,
    }


def check_bootstrap_populated() -> dict:
    """CHECK 3: Claude bootstrap has CLAUDE.md, commands, hooks, skills."""
    missing: list = []
    for rel in REQUIRED_BOOTSTRAP_FILES:
        if not (REPO_ROOT / rel).is_file():
            missing.append(rel)
    for rel in REQUIRED_BOOTSTRAP_DIRS:
        p = REPO_ROOT / rel
        if not p.is_dir() or not any(p.iterdir()):
            missing.append(rel)

    cmds_dir = REPO_ROOT / "icdev" / "data" / "claude_bootstrap" / "claude" / "commands"
    cmd_count = len(list(cmds_dir.glob("*.md"))) if cmds_dir.exists() else 0
    min_cmds = _min_slash_command_count()

    ok = not missing and cmd_count >= min_cmds
    return {
        "check": "bootstrap_populated",
        "ok": ok,
        "missing": missing,
        "slash_command_count": cmd_count,
        "min_command_count": min_cmds,
    }


def check_forge_data_dirs() -> dict:
    """CHECK 4: FORGE layer dirs exist under icdev/data/."""
    missing: list = []
    counts: dict = {}
    for rel in REQUIRED_DATA_DIRS:
        p = REPO_ROOT / rel
        if not p.is_dir() or not any(p.iterdir()):
            missing.append(rel)
        else:
            counts[Path(rel).name] = sum(1 for _ in p.rglob("*") if _.is_file())
    return {
        "check": "forge_data_dirs",
        "ok": not missing,
        "missing": missing,
        "file_counts": counts,
    }


def check_entry_points() -> dict:
    """CHECK 5: pyproject.toml entry points resolve to real modules."""
    src = PYPROJECT.read_text(encoding="utf-8")
    match = re.search(
        r"\[project\.scripts\](.+?)(?=\n\[|\Z)", src, re.DOTALL
    )
    if not match:
        return {"check": "entry_points", "ok": False, "error": "no [project.scripts] section"}

    broken: list = []
    entries: list = []
    for line in match.group(1).splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        m = re.match(r'([a-zA-Z0-9_-]+)\s*=\s*"([^"]+)"', line)
        if not m:
            continue
        name = m.group(1)
        target = m.group(2)
        # Resolve module:function
        mod_path, _, _func = target.partition(":")
        rel = mod_path.replace(".", "/") + ".py"
        # Try as a file first
        candidate = REPO_ROOT / rel
        # Or as a package (module_path/__init__.py)
        candidate_pkg = REPO_ROOT / mod_path.replace(".", "/") / "__init__.py"
        exists = candidate.exists() or candidate_pkg.exists()
        entries.append({"name": name, "target": target, "resolved": exists})
        if not exists:
            broken.append({"name": name, "target": target})

    return {
        "check": "entry_points",
        "ok": not broken,
        "total_entries": len(entries),
        "broken": broken,
    }


def _parse_env_keys(path: Path) -> set[str]:
    """Extract VAR_NAME= keys from an env file, ignoring comments/blanks."""
    keys: set[str] = set()
    if not path.is_file():
        return keys
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        m = re.match(r"([A-Za-z_][A-Za-z0-9_]*)=", line)
        if m:
            keys.add(m.group(1))
    return keys


def check_env_files_sync() -> dict:
    """CHECK 6: .env.example has every key .env.sample defines.

    .env.sample is the comprehensive reference; .env.example is what
    icdev init actually seeds a new project's .env.template from, and what
    tools/awareness/enablement.py reads as its runtime defaults layer. A key
    present only in .env.sample is invisible to both — extra keys in
    .env.example (not in .env.sample) are fine, not a failure.
    """
    sample_keys = _parse_env_keys(ENV_SAMPLE)
    example_keys = _parse_env_keys(ENV_EXAMPLE)
    missing_in_example = sample_keys - example_keys
    return {
        "check": "env_files_sync",
        "ok": not missing_in_example,
        "sample_key_count": len(sample_keys),
        "example_key_count": len(example_keys),
        "missing_in_example": sorted(missing_in_example),
    }


def _registry_env_flags() -> set[str]:
    """Primary env_flags declared by canvases/components in the registry."""
    flags: set[str] = set()
    if _yaml is None or not COMPONENT_REGISTRY.is_file():
        return flags
    try:
        with open(COMPONENT_REGISTRY, encoding="utf-8") as fh:
            data = _yaml.safe_load(fh) or {}
    except Exception:
        return flags

    def walk(obj: object) -> None:
        if isinstance(obj, dict):
            ef = obj.get("env_flag")
            if isinstance(ef, str) and ef:
                flags.add(ef)
            for v in obj.values():
                walk(v)
        elif isinstance(obj, list):
            for item in obj:
                walk(item)

    walk(data)
    return flags


def check_env_flags_documented() -> dict:
    """CHECK 7: .env.example documents every registry-declared env_flag.

    args/component_registry.yaml is authoritative for canvas/component
    enablement. Any component whose primary env_flag is missing from
    .env.example is undiscoverable to a pip-install user (and, if
    default_enabled is false, effectively unreachable via `icdev init`'s
    generated .env). Extra flags in .env.example are fine.
    """
    registry_flags = _registry_env_flags()
    example_keys = _parse_env_keys(ENV_EXAMPLE)
    undocumented = registry_flags - example_keys
    return {
        "check": "env_flags_documented",
        "ok": not undocumented,
        "registry_flag_count": len(registry_flags),
        "undocumented_in_example": sorted(undocumented),
    }


# ---------------------------------------------------------------------------
# CHECK 8: bootstrap snapshot freshness (pkg-rel-02)
# ---------------------------------------------------------------------------
# icdev/data/claude_bootstrap/ is a COMMITTED snapshot refreshed only by
# prebuild_bootstrap.py (via sync_package_tree.py). A plain `python -m build`
# skips that entirely, so a release cut without the sync step ships a months-old
# command/skill set that looks fine. This check compares the snapshot against
# the LIVE source trees by FILE SET AND CONTENT and fails with the exact
# added/removed/changed paths.
#
# It derives the source→snapshot mapping directly from prebuild_bootstrap.SOURCES
# so the gate can never disagree with what prebuild actually writes. This is what
# keeps the skills nuance honest: prebuild reads `.agents/skills` (not
# `.claude/skills`) as the source of truth for icdev-* skills, so comparing
# against `.claude/skills` would report permanent false drift.


def _tree_file_bytes(root: Path, exclude) -> dict[str, bytes]:
    """Map every non-excluded file under root to its bytes, keyed by rel posix path."""
    out: dict[str, bytes] = {}
    if not root.is_dir():
        return out
    for p in root.rglob("*"):
        if exclude(p):
            continue
        if p.is_file():
            rel = str(p.relative_to(root)).replace("\\", "/")
            try:
                out[rel] = p.read_bytes()
            except Exception:
                out[rel] = b"<unreadable>"
    return out


def check_bootstrap_freshness() -> dict:
    """CHECK 8: the committed bootstrap snapshot matches the live source trees.

    Uses prebuild_bootstrap's own SOURCES mapping as the single source of truth
    for what should be copied where (so the skills `.agents/skills` nuance is
    handled automatically). Reports the specific added/removed/changed paths so
    a stale release is diagnosable, not just detectable.
    """
    try:
        from tools.installer.prebuild_bootstrap import (
            BOOTSTRAP_DIR,
            OPTIONAL_SOURCES,
            SOURCES,
            _should_exclude,
        )
    except Exception as exc:  # pragma: no cover - import failure
        return {
            "check": "bootstrap_freshness",
            "ok": False,
            "error": f"could not import prebuild_bootstrap: {exc}",
        }

    added: list[str] = []      # live has it, snapshot missing it
    removed: list[str] = []    # snapshot has it, live dropped it (stale)
    changed: list[str] = []    # present both sides, content differs
    missing_sources: list[str] = []

    for rel_src, rel_dst, kind in SOURCES:
        src = REPO_ROOT / rel_src
        dst = BOOTSTRAP_DIR / rel_dst
        if not src.exists():
            if rel_src not in OPTIONAL_SOURCES:
                missing_sources.append(rel_src)
            continue

        if kind == "file":
            if not dst.is_file():
                added.append(rel_dst)
            elif src.read_bytes() != dst.read_bytes():
                changed.append(rel_dst)
            continue

        # kind == "dir"
        live = _tree_file_bytes(src, _should_exclude)
        snap = _tree_file_bytes(dst, _should_exclude)
        live_keys, snap_keys = set(live), set(snap)
        added += [f"{rel_dst}/{k}" for k in sorted(live_keys - snap_keys)]
        removed += [f"{rel_dst}/{k}" for k in sorted(snap_keys - live_keys)]
        changed += [
            f"{rel_dst}/{k}" for k in sorted(live_keys & snap_keys)
            if live[k] != snap[k]
        ]

    ok = not (added or removed or changed or missing_sources)
    return {
        "check": "bootstrap_freshness",
        "ok": ok,
        "added_missing_from_snapshot": added,
        "removed_stale_in_snapshot": removed,
        "changed_content": changed,
        "missing_sources": missing_sources,
        "drift_count": len(added) + len(removed) + len(changed),
        "fix": "python tools/installer/prebuild_bootstrap.py --clean",
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def validate() -> dict:
    checks = [
        check_parent_only_sync(),
        check_required_subsystems(),
        check_bootstrap_populated(),
        check_forge_data_dirs(),
        check_entry_points(),
        check_env_files_sync(),
        check_env_flags_documented(),
        check_bootstrap_freshness(),
    ]
    overall_ok = all(c["ok"] for c in checks)
    return {
        "overall_ok": overall_ok,
        "checks": checks,
    }


def _print_human(result: dict) -> None:
    print("=" * 68)
    status = "PASS" if result["overall_ok"] else "FAIL"
    print(f"  ICDEV(TM) Package Config Validation: {status}")
    print("=" * 68)
    print()
    for c in result["checks"]:
        mark = "[PASS]" if c["ok"] else "[FAIL]"
        print(f"{mark}  {c['check']}")
        if not c["ok"]:
            for k, v in c.items():
                if k in ("check", "ok"):
                    continue
                if v:
                    print(f"       {k}: {v}")
        else:
            # Show key positive stat
            for k in ("parent_only_count", "present_count",
                      "slash_command_count", "total_entries",
                      "sample_key_count", "registry_flag_count",
                      "drift_count"):
                if k in c:
                    print(f"       {k}: {c[k]}")
    print()
    if not result["overall_ok"]:
        print("Fix the failures above before running `python -m build`.")


def _fix_bootstrap_freshness() -> dict:
    """Re-run prebuild_bootstrap to refresh the committed snapshot from live."""
    from tools.installer.prebuild_bootstrap import run as _prebuild_run

    return _prebuild_run(clean=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--gate", action="store_true",
                        help="Exit 1 on any failure (for CI)")
    parser.add_argument("--fix", action="store_true",
                        help="Auto-fix what is safely fixable (re-runs "
                             "prebuild_bootstrap.py to refresh the snapshot), "
                             "then re-validates")
    args = parser.parse_args()

    result = validate()

    if args.fix:
        freshness = next(
            (c for c in result["checks"] if c["check"] == "bootstrap_freshness"),
            None,
        )
        if freshness and not freshness["ok"]:
            fix_result = _fix_bootstrap_freshness()
            if not args.json:
                print("Refreshed bootstrap snapshot via prebuild_bootstrap "
                      f"(--clean): {fix_result.get('total_files')} files copied.")
            result = validate()  # re-validate after the fix

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        _print_human(result)

    if not result["overall_ok"]:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

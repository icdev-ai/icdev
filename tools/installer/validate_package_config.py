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

Exit code 0 if everything is green, 1 on any failure. Designed to run as
a pre-commit hook, in CI, and inside tools/installer/build_release.py.

Usage:
    python tools/installer/validate_package_config.py           # human output
    python tools/installer/validate_package_config.py --json    # machine output
    python tools/installer/validate_package_config.py --gate    # non-zero on fail
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
PKG_DIR = REPO_ROOT / "icdev"
SYNC_SCRIPT = REPO_ROOT / "tools" / "installer" / "sync_package_tree.py"
PYPROJECT = REPO_ROOT / "pyproject.toml"
MANIFEST = REPO_ROOT / "MANIFEST.in"
_PKG_CONFIG = REPO_ROOT / "args" / "package_config.yaml"
ENV_EXAMPLE = REPO_ROOT / ".env.example"
ENV_SAMPLE = REPO_ROOT / ".env.sample"


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
                      "sample_key_count"):
                if k in c:
                    print(f"       {k}: {c[k]}")
    print()
    if not result["overall_ok"]:
        print("Fix the failures above before running `python -m build`.")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--gate", action="store_true",
                        help="Exit 1 on any failure (for CI)")
    args = parser.parse_args()

    result = validate()
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        _print_human(result)

    if not result["overall_ok"] and args.gate:
        return 1
    if not result["overall_ok"]:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
# CUI // SP-CTI
"""Guard test for nav-strat-05 — the legacy, unregistered Strategos blueprint
duplicate must stay deleted and must never be imported again.

The canonical, registered Strategos blueprint lives in ``apps/strategos/blueprint.py``
(two factories: ``create_strategos_blueprint`` + ``create_strategos_api_blueprint``,
wired in ``tools/dashboard/app.py``).  The old ``tools/strategos/blueprint.py`` (and
its ``icdev/tools/strategos/blueprint.py`` twin) was an unregistered duplicate that
confused greps and future edits; nav-strat-05 removed it and ported its unique
wargame endpoints into the apps API blueprint.

This test fails if either legacy file reappears or if any Python module imports the
legacy module path.
"""
from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

# The two legacy files that must remain deleted.
LEGACY_FILES = (
    REPO_ROOT / "tools" / "strategos" / "blueprint.py",
    REPO_ROOT / "icdev" / "tools" / "strategos" / "blueprint.py",
)

# Import statements that would resurrect the legacy module (NOT apps.strategos.*).
_IMPORT_PATTERNS = (
    re.compile(r"^\s*from\s+tools\.strategos\.blueprint\b", re.MULTILINE),
    re.compile(r"^\s*import\s+tools\.strategos\.blueprint\b", re.MULTILINE),
    re.compile(r"^\s*from\s+icdev\.tools\.strategos\.blueprint\b", re.MULTILINE),
    re.compile(r"^\s*import\s+icdev\.tools\.strategos\.blueprint\b", re.MULTILINE),
)

# Directories we never scan (vcs, caches, virtualenvs, scratch, generated worktrees).
_SKIP_DIRS = {
    ".git", "__pycache__", "node_modules", ".venv", "venv", ".tmp",
    ".mypy_cache", ".pytest_cache", ".ruff_cache", "dist", "build",
}

# This guard file legitimately mentions the legacy dotted path in strings/regex.
_SELF = Path(__file__).resolve()


def _iter_python_files():
    for path in REPO_ROOT.rglob("*.py"):
        if path.resolve() == _SELF:
            continue
        if any(part in _SKIP_DIRS for part in path.parts):
            continue
        yield path


def test_legacy_blueprint_files_are_deleted():
    """The unregistered legacy blueprint and its icdev twin must not exist."""
    present = [str(p.relative_to(REPO_ROOT)) for p in LEGACY_FILES if p.exists()]
    assert not present, (
        "Legacy unregistered Strategos blueprint reappeared: "
        f"{present}. The canonical blueprint is apps/strategos/blueprint.py."
    )


def test_no_module_imports_legacy_blueprint():
    """No Python module may import tools.strategos.blueprint / icdev.tools.strategos.blueprint."""
    offenders: list[str] = []
    for path in _iter_python_files():
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if "strategos.blueprint" not in text:
            continue
        if any(pat.search(text) for pat in _IMPORT_PATTERNS):
            offenders.append(str(path.relative_to(REPO_ROOT)))
    assert not offenders, (
        "These modules import the deleted legacy Strategos blueprint; "
        f"repoint them at apps.strategos.blueprint: {offenders}"
    )


def test_canonical_blueprint_exists():
    """Sanity: the registered apps blueprint (the replacement) is present."""
    canonical = REPO_ROOT / "apps" / "strategos" / "blueprint.py"
    assert canonical.exists(), "apps/strategos/blueprint.py (canonical) is missing"

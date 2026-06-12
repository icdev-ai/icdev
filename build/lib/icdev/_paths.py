"""Centralized ICDEV™ path resolution.

Resolves the project root and data directories (args, context, goals,
hardprompts) whether ICDEV™ is run from a source checkout or installed
via pip.

Resolution order:
  1. ICDEV_PROJECT_ROOT environment variable (if set)
  2. Walk up from this file to find pyproject.toml (source checkout)
  3. Fall back to icdev/data/ (pip install)
"""

from __future__ import annotations

import os
from pathlib import Path

_ICDEV_PKG_DIR = Path(__file__).resolve().parent  # icdev/
_ICDEV_DATA_DIR = _ICDEV_PKG_DIR / "data"


def _find_project_root() -> Path | None:
    """Walk up from icdev/ to find pyproject.toml (source checkout)."""
    d = _ICDEV_PKG_DIR.parent
    for _ in range(5):  # max 5 levels up
        if (d / "pyproject.toml").exists():
            return d
        parent = d.parent
        if parent == d:
            break
        d = parent
    return None


_SOURCE_ROOT = _find_project_root()


def get_project_root() -> Path:
    """Return the ICDEV™ project root directory.

    For source checkout: returns the repo root (parent of icdev/).
    For pip install: returns the icdev package directory.
    """
    env = os.environ.get("ICDEV_PROJECT_ROOT")
    if env:
        p = Path(env)
        if p.is_dir():
            return p

    if _SOURCE_ROOT is not None:
        return _SOURCE_ROOT

    return _ICDEV_PKG_DIR


def get_data_path(name: str) -> Path:
    """Resolve a FORGE data directory (args, context, goals, hardprompts, etc.).

    Args:
        name: Directory name — one of 'args', 'context', 'goals',
              'hardprompts', 'features', 'docs'.

    Returns:
        Path to the resolved data directory.
    """
    # 1. Env override
    env = os.environ.get("ICDEV_PROJECT_ROOT")
    if env:
        p = Path(env) / name
        if p.is_dir():
            return p

    # 2. Source checkout: data dir at project root
    if _SOURCE_ROOT is not None:
        source = _SOURCE_ROOT / name
        if source.is_dir():
            return source

    # 3. Packaged: icdev/data/<name>/
    pkg = _ICDEV_DATA_DIR / name
    if pkg.is_dir():
        return pkg

    # 4. Fallback: relative to CWD
    return Path(name)

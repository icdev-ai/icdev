"""Centralized ICDEV™ path resolution — a thin delegate onto :mod:`icdev.core.paths`.

Kept for the call sites that import ``get_project_root`` / ``get_data_path``
from here. Resolution order is documented once, in ``icdev/core/paths.py``:
``ICDEV_PROJECT_ROOT`` -> the source checkout holding the calling code ->
the nearest ``icdev_domain.yaml`` above the current directory -> ``icdev/``.
"""

from __future__ import annotations

from pathlib import Path

from icdev.core import paths as _core_paths


def get_project_root() -> Path:
    """Return the ICDEV™ project root directory.

    For a source checkout: the repo root (parent of ``icdev/``).
    For a pip install serving a declared parent: that parent's root.
    Otherwise: the ``icdev`` package directory.
    """
    return _core_paths.repo_root()


def get_data_path(name: str) -> Path:
    """Resolve a FORGE data directory (args, context, goals, hardprompts, etc.)."""
    return _core_paths.data_path(name)

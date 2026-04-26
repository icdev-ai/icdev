# CUI // SP-CTI
"""Shared pytest configuration for tests/e2e/.

Ensures the worktree root is first on sys.path so `import tools` resolves
correctly regardless of how pytest is invoked (subprocess, worktree, CI).
"""
from __future__ import annotations

import sys
from pathlib import Path

# Prepend the project root (grandparent of this file: tests/e2e/conftest.py →
# tests/ → <project_root>) so `import tools` always resolves to the canonical
# project-root tools/ package, not icdev/tools/ or any other shadow.
_WORKTREE_ROOT = str(Path(__file__).resolve().parents[2])
if _WORKTREE_ROOT not in sys.path:
    sys.path.insert(0, _WORKTREE_ROOT)
# CUI // SP-CTI

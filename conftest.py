# CUI // SP-CTI
"""Root conftest.py — ensures project root is first in sys.path before any test collection.

This file is loaded by pytest before ANY test conftest.py or test module.
It prevents tools/ subdirectory sys.path inserts from shadowing the tools package.
"""
import os
import sys
from pathlib import Path

_PROJECT_ROOT = str(Path(__file__).resolve().parent)

# Force SQLite backend for tests (PostgreSQL not required for unit tests)
os.environ.setdefault("ICDEV_STORAGE_BACKEND", "sqlite")
os.environ.setdefault("NOCC_STORAGE_BACKEND", "sqlite")
os.environ.setdefault("PMC_STORAGE_BACKEND", "sqlite")

# Pre-import tools.db.storage to anchor it in sys.modules before any test
# can corrupt the tools package resolution with subdirectory sys.path inserts
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)
import tools.db.storage  # noqa: E402,F401 — anchor in sys.modules

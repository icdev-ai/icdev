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

# cnr-plat-01 / cnr-plat-03: keep the two new fail-closed-by-default platform
# gates (CSRF on cookie-authed mutating APIs; canvas access enforcement) OPT-OUT
# during the test suite so the many existing dashboard/canvas tests that use
# logged-in sessions (session_transaction) or unauthenticated test clients keep
# passing. Dedicated cnr-plat tests re-enable them per-test via monkeypatch.
# Loaded here (root conftest, first) so it applies regardless of where a test
# module lives. An explicit env value still wins.
os.environ.setdefault("ICDEV_CSRF_ENFORCE", "0")
os.environ.setdefault("ICDEV_CANVAS_ACCESS_OPEN", "true")

# Pre-import tools.db.storage to anchor it in sys.modules before any test
# can corrupt the tools package resolution with subdirectory sys.path inserts
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)
import tools.db.storage  # noqa: E402,F401 — anchor in sys.modules

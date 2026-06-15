# CUI // SP-CTI
"""Local conftest for observability regression tests.

Sets ``ICDEV_STORAGE_BACKEND=postgresql`` in the test runner context for
any test under ``tests/observability/`` so the regression tests exercise
the production backend (PostgreSQL primary) rather than the SQLite
default forced by the global ``tests/conftest.py``.

This is a LOCAL override — it does not change the global SQLite
default for the 330+ other tests in the suite.
"""
from __future__ import annotations

import os

# ---------------------------------------------------------------------------
# Force PostgreSQL backend for observability regression tests
# ---------------------------------------------------------------------------
# The global conftest forces ICDEV_STORAGE_BACKEND=sqlite for the entire
# suite. Observability regression tests must exercise the PG-primary
# backend because the upstream d3 task fixed the /observability/events
# endpoint against PG (not SQLite). We use a direct assignment (not
# setdefault) so that we override the global conftest's SQLite-forcing
# setting for any test under tests/observability/.
os.environ["ICDEV_STORAGE_BACKEND"] = "postgresql"
os.environ["OC_STORAGE_BACKEND"] = "postgresql"

# CUI // SP-CTI
"""Sentinel for the PG pytest tier (kph).

The whole point of the tier is to run against a LIVE PostgreSQL — but
get_connection() silently falls back to SQLite when PG is unreachable, which
would make the tier go green while testing nothing on PG (false confidence).
This test fails loudly if the ambient backend is not actually PostgreSQL, and
exercises a real %s round-trip through translate_sql on PG.

Skips outside the tier (normal SQLite suite) so it never fires there.
"""
from __future__ import annotations

import os

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("ICDEV_PYTEST_PG", "").lower() not in ("1", "true", "yes"),
    reason="PG tier only — set ICDEV_PYTEST_PG=1 with a live PostgreSQL service",
)


def test_ambient_backend_is_postgresql():
    from tools.db.storage import get_backend, get_connection

    assert get_backend() == "postgresql", (
        "PG tier is not on PostgreSQL — check ICDEV_STORAGE_BACKEND, the PG "
        "service, and ICDEV_PG_NO_FALLBACK (fail-closed)."
    )
    with get_connection() as conn:
        assert getattr(conn, "_backend", None) == "postgresql"
        # A real %s round-trip: on SQLite this would only work via translate_sql;
        # here it proves the live PG driver bound the parameter.
        row = conn.execute("SELECT %s::int AS n", (7,)).fetchone()
        val = row["n"] if not isinstance(row, (tuple, list)) else row[0]
        assert val == 7

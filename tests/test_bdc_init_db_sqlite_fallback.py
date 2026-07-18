#!/usr/bin/env python3
# CUI // SP-CTI
"""Smoke test — BDC init_db SQLite fallback translates %s placeholders.

Regression guard for task bdr-sec-3: the SQLite fallback in
``tools/boundary_canvas/db/init_db.py`` previously returned a RAW
``sqlite3.connect`` connection, which does NOT translate PG-native ``%s``
placeholders. Every ``%s`` query on that path (template/snippet/runbook seeding
via ``SELECT 1 ... WHERE id=%s``, ``INSERT ... VALUES (%s,...)``) raised
``sqlite3.ProgrammingError``.

The fix wraps the raw connection in ICDEV's translating ``StorageConnection``.
This test forces the fallback (``_BDC_BACKEND = "sqlite"`` + a temp DB path) and
asserts that a full ``init_db()`` runs clean and ``%s`` queries work afterwards.
"""

import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def test_sqlite_fallback_init_smoke(tmp_path, monkeypatch):
    import tools.boundary_canvas.db.init_db as bdc_init
    from tools.db.storage import StorageConnection

    # Force the SQLite init-only fallback onto a dedicated temp DB file.
    monkeypatch.setattr(bdc_init, "_BDC_BACKEND", "sqlite")
    monkeypatch.setattr(bdc_init, "DB_PATH", tmp_path / "boundary_canvas_smoke.db")

    # The fallback connection must be the translating wrapper, not a raw conn.
    conn = bdc_init.get_connection()
    assert isinstance(conn, StorageConnection)
    conn.close()

    # A full init must not raise sqlite3.ProgrammingError on any %s query.
    bdc_init.init_db()

    # %s placeholders must be translated to ? on this path (no ProgrammingError)
    # and the seed data must be present.
    conn = bdc_init.get_connection()
    try:
        missing = conn.execute(
            "SELECT COUNT(*) FROM bd_templates WHERE id = %s",
            ("does-not-exist",),
        ).fetchone()
        assert missing[0] == 0

        total = conn.execute("SELECT COUNT(*) FROM bd_templates").fetchone()
        assert total[0] >= 1, "init_db should have seeded at least one template"
    finally:
        conn.close()


def test_raw_sqlite_would_reject_percent_s():
    """Document the failure mode the fix prevents: raw sqlite3 rejects %s.

    A raw ``sqlite3`` connection does not understand ``%s`` placeholders — it
    raises a ``sqlite3.Error`` (an OperationalError syntax error on this SELECT,
    or a ProgrammingError binding mismatch on an INSERT). The StorageConnection
    wrapper is what makes the fallback path work.
    """
    import pytest

    raw = sqlite3.connect(":memory:")
    raw.execute("CREATE TABLE t (id TEXT)")
    with pytest.raises(sqlite3.Error):
        raw.execute("SELECT * FROM t WHERE id = %s", ("x",))
    raw.close()

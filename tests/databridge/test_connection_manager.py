# CUI // SP-CTI
"""Unit tests for tools/databridge/connection_manager.py."""

from __future__ import annotations

import sys
import sqlite3
from pathlib import Path
from unittest.mock import MagicMock, patch


sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.databridge.connection_manager import DB_PATH


# ---------------------------------------------------------------------------
# _get_conn — storage layer preference
# ---------------------------------------------------------------------------

class TestGetConn:
    """Verify _get_conn prefers get_connection() and falls back to SQLite.

    Regression: connection_manager previously used raw sqlite3.connect()
    unconditionally, bypassing the storage abstraction layer and breaking
    PostgreSQL deployments.
    """

    def test_prefers_storage_get_connection_for_icdev_db(self, monkeypatch) -> None:
        """When storage.get_connection() is available it must be used for icdev.db."""
        mock_conn = MagicMock()
        mock_conn.row_factory = None

        def fake_gc():
            return mock_conn

        with patch.dict("sys.modules", {"tools.db.storage": MagicMock(get_connection=fake_gc)}):
            import importlib
            import tools.databridge.connection_manager as cm
            importlib.reload(cm)  # pick up the patched module

            conn = cm._get_conn(str(DB_PATH))
            assert conn is mock_conn

    def test_falls_back_to_sqlite_when_storage_unavailable(self, tmp_path, monkeypatch) -> None:
        """When storage.get_connection() raises, _get_conn must fall back to SQLite."""
        db_file = tmp_path / "test.db"
        # Seed a minimal table so the connection is usable
        raw = sqlite3.connect(str(db_file))
        raw.execute("CREATE TABLE ping (id INTEGER PRIMARY KEY)")
        raw.commit()
        raw.close()

        import tools.databridge.connection_manager as cm

        # Make get_connection() raise to simulate PG unavailable
        with patch("tools.db.storage.get_connection", side_effect=ConnectionError("pg down")):
            conn = cm._get_conn(str(db_file))
            assert conn is not None
            # Must be a usable SQLite connection
            row = conn.execute("SELECT COUNT(*) FROM ping").fetchone()
            assert row is not None

    def test_falls_back_to_sqlite_when_storage_not_importable(self, tmp_path) -> None:
        """When tools.db.storage cannot be imported, fall back to SQLite silently."""
        db_file = tmp_path / "nodb.db"
        raw = sqlite3.connect(str(db_file))
        raw.execute("CREATE TABLE ping (id INTEGER PRIMARY KEY)")
        raw.commit()
        raw.close()

        import tools.databridge.connection_manager as cm

        # Simulate ImportError by temporarily hiding the storage module
        import sys as _sys
        original = _sys.modules.pop("tools.db.storage", None)
        try:
            conn = cm._get_conn(str(db_file))
            assert conn is not None
        finally:
            if original is not None:
                _sys.modules["tools.db.storage"] = original

    def test_row_factory_set_on_returned_connection(self, tmp_path) -> None:
        """Returned connection must have row_factory set regardless of backend."""
        db_file = tmp_path / "rowfactory.db"
        sqlite3.connect(str(db_file)).close()

        import tools.databridge.connection_manager as cm
        conn = cm._get_conn(str(db_file))
        assert conn.row_factory is not None

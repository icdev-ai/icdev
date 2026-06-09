"""Co-Workers canvas DB tests (cwk-db-01)."""
import sqlite3

import pytest


@pytest.fixture
def cwk_db(tmp_path):
    """Temp SQLite DB with MINIMAL_ICDEV_SCHEMA."""
    db_path = tmp_path / "icdev.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    from tests.conftest import MINIMAL_ICDEV_SCHEMA
    conn.executescript(MINIMAL_ICDEV_SCHEMA)
    conn.commit()
    yield conn
    conn.close()


class TestCwkDbInit:
    def test_cwk_coworkers_table_exists(self, cwk_db):
        rows = cwk_db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='cwk_coworkers'"
        ).fetchall()
        assert rows

    def test_cwk_sessions_table_exists(self, cwk_db):
        rows = cwk_db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='cwk_sessions'"
        ).fetchall()
        assert rows

    def test_cwk_coworkers_status_constraint(self, cwk_db):
        cwk_db.execute(
            "INSERT INTO cwk_coworkers (id, slug, name, status) VALUES (?, ?, ?, ?)",
            ("c1", "alpha", "Alpha", "active"),
        )
        cwk_db.commit()
        with pytest.raises(sqlite3.IntegrityError):
            cwk_db.execute(
                "INSERT INTO cwk_coworkers (id, slug, name, status) VALUES (?, ?, ?, ?)",
                ("c2", "beta", "Beta", "invalid"),
            )

    def test_cwk_sessions_insert(self, cwk_db):
        cwk_db.execute(
            "INSERT INTO cwk_sessions (id, coworker_id, chat_context_id, ace_instance_id) "
            "VALUES (?, ?, ?, ?)",
            ("s1", "strategos", "ctx-123", "ace-456"),
        )
        cwk_db.commit()
        row = cwk_db.execute(
            "SELECT * FROM cwk_sessions WHERE id = ?", ("s1",)
        ).fetchone()
        assert row
        assert row["coworker_id"] == "strategos"
        assert row["chat_context_id"] == "ctx-123"

    def test_cwk_sessions_indexed(self, cwk_db):
        cwk_db.execute(
            "INSERT INTO cwk_coworkers (id, slug, name) VALUES (?, ?, ?)",
            ("c1", "alpha", "Alpha"),
        )
        cwk_db.execute(
            "INSERT INTO cwk_sessions (id, coworker_id, chat_context_id, ace_instance_id) "
            "VALUES (?, ?, ?, ?)",
            ("s1", "alpha", "ctx-123", "ace-456"),
        )
        cwk_db.commit()
        rows = cwk_db.execute(
            "SELECT * FROM cwk_sessions WHERE coworker_id = ?", ("alpha",)
        ).fetchall()
        assert len(rows) == 1

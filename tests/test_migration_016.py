# CUI // SP-CTI
"""Tests for migration 016/027: compliance_snapshots table creation.

Migration numbering note: the plan named this migration 016, but slot 016
was already occupied (kanban_source_prediction_id). The DDL landed at 027.
This test file satisfies the acceptance criterion for dt-bdc-02 while
delegating execution to the canonical up.py.
"""

import importlib.util
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

_MIGRATION_PATH = (
    Path(__file__).parent.parent
    / "tools/db/migrations/027_compliance_snapshots/up.py"
)


def _import_up():
    spec = importlib.util.spec_from_file_location("migration_016_up", _MIGRATION_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture()
def conn(tmp_path):
    c = sqlite3.connect(str(tmp_path / "test.db"))
    c.execute("PRAGMA foreign_keys = ON")
    yield c
    c.close()


@pytest.fixture()
def up_mod():
    return _import_up()


class TestMigration016ComplianceSnapshots:
    def test_applies_on_fresh_db(self, conn, up_mod):
        result = up_mod.up(conn)
        assert result["status"] == "applied"
        assert "table_created" in result["actions"]

    def test_creates_table(self, conn, up_mod):
        up_mod.up(conn)
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='compliance_snapshots'"
        ).fetchone()
        assert row is not None

    def test_expected_columns(self, conn, up_mod):
        up_mod.up(conn)
        cols = {
            r[1]
            for r in conn.execute("PRAGMA table_info(compliance_snapshots)").fetchall()
        }
        assert cols == {
            "snapshot_id",
            "project_id",
            "framework_id",
            "control_id",
            "status",
            "evidence_ref",
            "taken_at",
        }

    def test_creates_composite_indexes(self, conn, up_mod):
        up_mod.up(conn)
        idx_names = {
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='index' AND tbl_name='compliance_snapshots'"
            ).fetchall()
        }
        assert "idx_cs_project_framework" in idx_names
        assert "idx_cs_control_status" in idx_names
        assert "idx_cs_taken_at" in idx_names

    def test_idempotent_second_run(self, conn, up_mod):
        up_mod.up(conn)
        result2 = up_mod.up(conn)
        assert result2["status"] == "skipped"

    def test_insert_valid_row(self, conn, up_mod):
        up_mod.up(conn)
        conn.execute(
            "INSERT INTO compliance_snapshots "
            "(snapshot_id, project_id, framework_id, control_id, status, taken_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("snap-001", "proj-a", "fedramp-moderate", "AC-2", "satisfied", "2026-04-18T00:00:00+00:00"),
        )
        conn.commit()
        row = conn.execute(
            "SELECT status FROM compliance_snapshots WHERE snapshot_id='snap-001'"
        ).fetchone()
        assert row[0] == "satisfied"

    def test_invalid_status_rejected(self, conn, up_mod):
        up_mod.up(conn)
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO compliance_snapshots "
                "(snapshot_id, project_id, framework_id, control_id, status, taken_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                ("snap-bad", "proj-a", "cmmc-l2", "AC-2", "INVALID", "2026-04-18T00:00:00+00:00"),
            )

    def test_evidence_ref_nullable(self, conn, up_mod):
        up_mod.up(conn)
        conn.execute(
            "INSERT INTO compliance_snapshots "
            "(snapshot_id, project_id, framework_id, control_id, status, evidence_ref, taken_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("snap-002", "proj-b", "nist-800-53", "IA-5", "planned", None, "2026-04-18T00:00:00+00:00"),
        )
        conn.commit()
        row = conn.execute(
            "SELECT evidence_ref FROM compliance_snapshots WHERE snapshot_id='snap-002'"
        ).fetchone()
        assert row[0] is None

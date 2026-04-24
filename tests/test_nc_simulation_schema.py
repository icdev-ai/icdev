# CUI // SP-CTI
"""Verify nc_simulation_* tables are present in MINIMAL_ICDEV_SCHEMA and
support basic insert/query operations via the icdev_db fixture."""

import sqlite3

import pytest


class TestNcSimulationSessionsTable:
    def test_table_exists(self, icdev_db):
        conn = sqlite3.connect(str(icdev_db))
        cursor = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='nc_simulation_sessions'"
        )
        assert cursor.fetchone() is not None
        conn.close()

    def test_insert_and_query(self, icdev_db):
        conn = sqlite3.connect(str(icdev_db))
        conn.execute(
            "INSERT INTO nc_simulation_sessions (id, canvas_type, topology_id, mode) "
            "VALUES ('sess-001', 'ndc', 'topo-a', 'explain')"
        )
        conn.commit()
        row = conn.execute(
            "SELECT id, canvas_type, topology_id, mode FROM nc_simulation_sessions WHERE id='sess-001'"
        ).fetchone()
        assert row is not None
        assert row[0] == "sess-001"
        assert row[1] == "ndc"
        assert row[2] == "topo-a"
        assert row[3] == "explain"
        conn.close()

    def test_default_metadata(self, icdev_db):
        conn = sqlite3.connect(str(icdev_db))
        conn.execute(
            "INSERT INTO nc_simulation_sessions (id, canvas_type) VALUES ('sess-002', 'sdc')"
        )
        conn.commit()
        row = conn.execute(
            "SELECT metadata FROM nc_simulation_sessions WHERE id='sess-002'"
        ).fetchone()
        assert row[0] == "{}"
        conn.close()

    def test_canvas_type_required(self, icdev_db):
        conn = sqlite3.connect(str(icdev_db))
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO nc_simulation_sessions (id) VALUES ('sess-bad')"
            )
        conn.close()


class TestNcSimulationRunsTable:
    def _insert_session(self, conn, session_id="sess-runs-001"):
        conn.execute(
            "INSERT INTO nc_simulation_sessions (id, canvas_type) VALUES (?, 'ndc')",
            (session_id,),
        )

    def test_table_exists(self, icdev_db):
        conn = sqlite3.connect(str(icdev_db))
        cursor = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='nc_simulation_runs'"
        )
        assert cursor.fetchone() is not None
        conn.close()

    def test_insert_and_query(self, icdev_db):
        conn = sqlite3.connect(str(icdev_db))
        self._insert_session(conn)
        conn.execute(
            "INSERT INTO nc_simulation_runs (id, session_id, steps, summary) "
            "VALUES ('run-001', 'sess-runs-001', '[\"step1\"]', 'done')"
        )
        conn.commit()
        row = conn.execute(
            "SELECT id, session_id, steps, summary FROM nc_simulation_runs WHERE id='run-001'"
        ).fetchone()
        assert row is not None
        assert row[0] == "run-001"
        assert row[1] == "sess-runs-001"
        assert row[2] == '["step1"]'
        assert row[3] == "done"
        conn.close()

    def test_default_steps(self, icdev_db):
        conn = sqlite3.connect(str(icdev_db))
        self._insert_session(conn)
        conn.execute(
            "INSERT INTO nc_simulation_runs (id, session_id) VALUES ('run-002', 'sess-runs-001')"
        )
        conn.commit()
        row = conn.execute(
            "SELECT steps FROM nc_simulation_runs WHERE id='run-002'"
        ).fetchone()
        assert row[0] == "[]"
        conn.close()

    def test_foreign_key_session_id(self, icdev_db):
        conn = sqlite3.connect(str(icdev_db))
        conn.execute("PRAGMA foreign_keys = ON")
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO nc_simulation_runs (id, session_id) "
                "VALUES ('run-bad', 'nonexistent-session')"
            )
        conn.close()


class TestNcSimulationArtifactsTable:
    def _insert_session_and_run(self, conn, session_id="sess-art-001", run_id="run-art-001"):
        conn.execute(
            "INSERT INTO nc_simulation_sessions (id, canvas_type) VALUES (?, 'eda')",
            (session_id,),
        )
        conn.execute(
            "INSERT INTO nc_simulation_runs (id, session_id) VALUES (?, ?)",
            (run_id, session_id),
        )

    def test_table_exists(self, icdev_db):
        conn = sqlite3.connect(str(icdev_db))
        cursor = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='nc_simulation_artifacts'"
        )
        assert cursor.fetchone() is not None
        conn.close()

    def test_insert_and_query(self, icdev_db):
        conn = sqlite3.connect(str(icdev_db))
        self._insert_session_and_run(conn)
        conn.execute(
            "INSERT INTO nc_simulation_artifacts (id, run_id, artifact_type, content) "
            "VALUES ('art-001', 'run-art-001', 'mermaid', '```mermaid\ngraph LR\n  A-->B\n```')"
        )
        conn.commit()
        row = conn.execute(
            "SELECT id, run_id, artifact_type, content "
            "FROM nc_simulation_artifacts WHERE id='art-001'"
        ).fetchone()
        assert row is not None
        assert row[0] == "art-001"
        assert row[1] == "run-art-001"
        assert row[2] == "mermaid"
        assert "mermaid" in row[3]
        conn.close()

    def test_default_content(self, icdev_db):
        conn = sqlite3.connect(str(icdev_db))
        self._insert_session_and_run(conn)
        conn.execute(
            "INSERT INTO nc_simulation_artifacts (id, run_id, artifact_type) "
            "VALUES ('art-002', 'run-art-001', 'summary')"
        )
        conn.commit()
        row = conn.execute(
            "SELECT content FROM nc_simulation_artifacts WHERE id='art-002'"
        ).fetchone()
        assert row[0] == ""
        conn.close()

    def test_foreign_key_run_id(self, icdev_db):
        conn = sqlite3.connect(str(icdev_db))
        conn.execute("PRAGMA foreign_keys = ON")
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO nc_simulation_artifacts (id, run_id, artifact_type) "
                "VALUES ('art-bad', 'nonexistent-run', 'mermaid')"
            )
        conn.close()

    def test_multiple_artifacts_per_run(self, icdev_db):
        conn = sqlite3.connect(str(icdev_db))
        self._insert_session_and_run(conn)
        for i, atype in enumerate(["mermaid", "summary", "stig_delta"]):
            conn.execute(
                "INSERT INTO nc_simulation_artifacts (id, run_id, artifact_type) "
                "VALUES (?, 'run-art-001', ?)",
                (f"art-multi-{i}", atype),
            )
        conn.commit()
        count = conn.execute(
            "SELECT COUNT(*) FROM nc_simulation_artifacts WHERE run_id='run-art-001'"
        ).fetchone()[0]
        assert count == 3
        conn.close()

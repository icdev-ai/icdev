# CUI // SP-CTI
"""Tests for tools/genesis/reflexes/cato_monitor.py — 4 cases, mocked DB."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest


@pytest.fixture
def reflex():
    import tools.genesis.reflexes.cato_monitor as rx
    return rx


@pytest.fixture
def mock_conn(monkeypatch):
    conn = MagicMock()
    from tools.db import storage as _stor
    monkeypatch.setattr(_stor, "get_connection", lambda: conn)
    return conn


class TestCatoMonitorReflex:
    def test_no_violations_returns_clean_shape(self, reflex, monkeypatch, mock_conn, tmp_path):
        """All IQE queries return empty rows → scanned=N, violations=0, poam_created=0."""
        fake_files = [tmp_path / f"q{i}.iqe" for i in range(5)]
        monkeypatch.setattr(reflex, "_collect_iqe_files", lambda: fake_files)
        monkeypatch.setattr(
            reflex,
            "_run_iqe_query",
            lambda qf, conn: {"name": qf.stem, "rows": [], "error": None},
        )

        result = reflex.run({}, None)

        assert result["success"] is True
        assert result["scanned"] == 5
        assert result["violations"] == 0
        assert result["poam_created"] == 0
        assert "details" in result

    def test_violations_trigger_poam_generator(self, reflex, monkeypatch, mock_conn, tmp_path):
        """Queries returning violation rows cause poam_generator to be called once."""
        fake_files = [tmp_path / f"q{i}.iqe" for i in range(3)]
        violation = {"control_id": "AC-2", "project_id": "sparkpilot"}

        def _fake_run_iqe(qf, conn):
            if qf.name == "q0.iqe":
                return {"name": "q0", "rows": [violation, violation], "error": None}
            return {"name": qf.stem, "rows": [], "error": None}

        poam_calls: list[str] = []

        def _fake_poam(project_id):
            poam_calls.append(project_id)
            return {"success": True, "path": "/data/poam-out.md"}

        monkeypatch.setattr(reflex, "_collect_iqe_files", lambda: fake_files)
        monkeypatch.setattr(reflex, "_run_iqe_query", _fake_run_iqe)
        monkeypatch.setattr(reflex, "_call_poam_generator", _fake_poam)

        result = reflex.run({}, None)

        assert result["success"] is True
        assert result["scanned"] == 3
        assert result["violations"] == 2
        assert result["poam_created"] == 1
        assert len(poam_calls) == 1

    def test_db_connection_failure_returns_error_shape(self, reflex, monkeypatch):
        """DB failure → success=False, all counts zero, error key present."""
        from tools.db import storage as _stor

        def _boom():
            raise RuntimeError("no db")

        monkeypatch.setattr(_stor, "get_connection", _boom)

        result = reflex.run({}, None)

        assert result["success"] is False
        assert result["scanned"] == 0
        assert result["violations"] == 0
        assert result["poam_created"] == 0
        assert "error" in result

    def test_iqe_query_error_continues_and_counts_failures(
        self, reflex, monkeypatch, mock_conn, tmp_path
    ):
        """One failing IQE query is gracefully skipped; others are counted normally."""
        fake_files = [tmp_path / f"q{i}.iqe" for i in range(3)]

        def _mixed_run(qf, conn):
            if qf.name == "q1.iqe":
                return {"name": "q1", "rows": [], "error": "parse error"}
            if qf.name == "q2.iqe":
                return {"name": "q2", "rows": [{"control_id": "SC-7"}], "error": None}
            return {"name": qf.stem, "rows": [], "error": None}

        monkeypatch.setattr(reflex, "_collect_iqe_files", lambda: fake_files)
        monkeypatch.setattr(reflex, "_run_iqe_query", _mixed_run)
        monkeypatch.setattr(
            reflex,
            "_call_poam_generator",
            lambda pid: {"success": True, "path": "/tmp/p.md"},  # nosec B108 — mock return value, no file I/O
        )

        result = reflex.run({}, None)

        assert result["success"] is True
        assert result["scanned"] == 3
        assert result["violations"] == 1
        assert result["details"]["queries_failed"] == 1

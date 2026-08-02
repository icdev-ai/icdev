# CUI // SP-CTI
"""Unit tests for tools.genesis.reflexes.qa_agent_reflex."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tools.genesis.reflexes.qa_agent_reflex import (
    _pending_gap_task_exists,
    _pending_sweep_exists,
    run,
)


def _make_conn(rows: dict | None = None) -> MagicMock:
    """Return a mock DB connection with configurable fetchone behaviour."""
    conn = MagicMock()
    rows = rows or {}

    def _execute(sql, params=()):
        cursor = MagicMock()
        # Determine which query is being run by looking at keywords
        sql_lower = sql.lower()
        if "full e2e suite sweep" in sql_lower:
            cursor.fetchone.return_value = rows.get("sweep_pending")
        elif "completed_at" in sql_lower and "full e2e suite sweep" in sql_lower:
            cursor.fetchone.return_value = rows.get("sweep_last_done")
        elif "qa-gap" in str(params).lower() or "qa_gap" in str(params).lower():
            cursor.fetchone.return_value = rows.get("gap_pending")
        else:
            cursor.fetchone.return_value = None
        return cursor

    conn.execute.side_effect = _execute
    return conn


class TestPendingSweepExists(unittest.TestCase):
    def test_returns_true_when_row_found(self):
        conn = MagicMock()
        cursor = MagicMock()
        cursor.fetchone.return_value = {"id": "task-qa-sweep-abc"}
        conn.execute.return_value = cursor
        assert _pending_sweep_exists(conn) is True

    def test_returns_false_when_no_row(self):
        conn = MagicMock()
        cursor = MagicMock()
        cursor.fetchone.return_value = None
        conn.execute.return_value = cursor
        assert _pending_sweep_exists(conn) is False


class TestPendingGapTaskExists(unittest.TestCase):
    def test_returns_true_when_gap_task_pending(self):
        conn = MagicMock()
        cursor = MagicMock()
        cursor.fetchone.return_value = {"id": "task-qa-gap-mycanvas-abc123"}
        conn.execute.return_value = cursor
        assert _pending_gap_task_exists(conn, "mycanvas") is True

    def test_returns_false_when_no_gap_task(self):
        conn = MagicMock()
        cursor = MagicMock()
        cursor.fetchone.return_value = None
        conn.execute.return_value = cursor
        assert _pending_gap_task_exists(conn, "mycanvas") is False


class TestRunReflex(unittest.TestCase):
    # Every test in this class calls run(), and run() calls _run_route_smoke(),
    # which sweeps all of NAV_ROUTES over real HTTP against a DASHBOARD THAT
    # MUST ALREADY BE LISTENING on http://localhost:5050. That is an ambient
    # dependency on the developer's machine, not fixture state: with a dashboard
    # up the class passes (slowly); with none up urllib blocks on connect for
    # every route in turn and the file times out rather than failing — which is
    # why it reads as a hang, not as a missing stub. None of these tests are
    # about route smoke; the sweep is stubbed out so the class exercises the
    # reflex's own DB/gap logic only.
    _SMOKE = "tools.genesis.reflexes.qa_agent_reflex._run_route_smoke"

    def setUp(self):
        _p = patch(self._SMOKE, return_value=[])
        _p.start()
        self.addCleanup(_p.stop)

    def _mock_conn(self, sweep_pending=False, gap_pending=False):
        conn = MagicMock()

        def execute_side(sql, params=()):
            c = MagicMock()
            sql_lower = sql.lower()
            if "full e2e suite sweep" in sql_lower:
                if "status in" in sql_lower and "backlog" in sql_lower:
                    c.fetchone.return_value = {"id": "x"} if sweep_pending else None
                else:
                    c.fetchone.return_value = None
            elif "qa-gap" in str(params).lower() or (params and "%" in str(params[0])):
                c.fetchone.return_value = {"id": "x"} if gap_pending else None
            else:
                c.fetchone.return_value = None
            return c

        conn.execute.side_effect = execute_side
        return conn

    # Patch the reflex module's own binding of get_connection (module-level import)
    _GET_CONN = "tools.genesis.reflexes.qa_agent_reflex.get_connection"
    # discover_coverage_gaps is imported inline inside run(), so patch the source module
    _DISCOVER = "tools.testing.qa_agent_runner.discover_coverage_gaps"

    def test_returns_dict_with_required_keys(self):
        conn = self._mock_conn()
        with (
            patch(self._GET_CONN, return_value=conn),
            patch(self._DISCOVER, return_value=[]),
        ):
            result = run({}, None)
        assert {"reflex", "sweep_task_created", "coverage_gaps_found", "gap_tasks_created"}.issubset(result)

    def test_sweep_task_created_when_no_pending(self):
        conn = self._mock_conn(sweep_pending=False)
        with (
            patch(self._GET_CONN, return_value=conn),
            patch(self._DISCOVER, return_value=[]),
        ):
            result = run({}, None)
        assert result["sweep_task_created"] is True
        assert result["sweep_task_id"] is not None

    def test_sweep_task_skipped_when_pending(self):
        conn = self._mock_conn(sweep_pending=True)
        with (
            patch(self._GET_CONN, return_value=conn),
            patch(self._DISCOVER, return_value=[]),
        ):
            result = run({}, None)
        assert result["skipped_pending_sweep"] is True
        assert result["sweep_task_created"] is False

    def test_gap_tasks_created_for_gaps(self):
        conn = self._mock_conn(sweep_pending=True, gap_pending=False)
        gaps = [
            {"canvas_key": "zta_canvas", "display_name": "ZTA", "route": "/security/zta", "enabled": True},
            {"canvas_key": "dic_canvas", "display_name": "DIC", "route": "/dic", "enabled": True},
        ]
        with (
            patch(self._GET_CONN, return_value=conn),
            patch(self._DISCOVER, return_value=gaps),
        ):
            result = run({}, None)
        assert result["coverage_gaps_found"] == 2
        assert result["gap_tasks_created"] == 2

    def test_max_gap_tasks_per_run_respected(self):
        conn = self._mock_conn(sweep_pending=True, gap_pending=False)
        gaps = [
            {"canvas_key": f"canvas_{i}", "display_name": f"Canvas {i}", "route": f"/c{i}", "enabled": True}
            for i in range(10)
        ]
        with (
            patch(self._GET_CONN, return_value=conn),
            patch(self._DISCOVER, return_value=gaps),
        ):
            result = run({"max_gap_tasks_per_run": 3}, None)
        assert result["gap_tasks_created"] <= 3

    def test_discover_gaps_failure_does_not_crash(self):
        conn = self._mock_conn()
        with (
            patch(self._GET_CONN, return_value=conn),
            patch(self._DISCOVER, side_effect=RuntimeError("yaml error")),
        ):
            result = run({}, None)
        assert "error" not in result  # reflex itself should not error
        assert result["coverage_gaps_found"] == 0

    def test_db_error_captured_in_result(self):
        with patch(self._GET_CONN, side_effect=Exception("conn failed")):
            result = run({}, None)
        assert "error" in result

    def test_commit_called_on_success(self):
        conn = self._mock_conn()
        with (
            patch(self._GET_CONN, return_value=conn),
            patch(self._DISCOVER, return_value=[]),
        ):
            run({}, None)
        conn.commit.assert_called_once()

    def test_connection_closed_always(self):
        conn = self._mock_conn()
        with (
            patch(self._GET_CONN, return_value=conn),
            patch(self._DISCOVER, return_value=[]),
        ):
            run({}, None)
        conn.close.assert_called_once()


if __name__ == "__main__":
    unittest.main()

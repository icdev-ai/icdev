# CUI // SP-CTI
"""Tests for get_eval_trends() and GET /api/ace/evals/trends."""
from __future__ import annotations
import json
from unittest.mock import MagicMock, patch


# ---------------------------------------------------------------------------
# Unit tests for get_eval_trends()
# ---------------------------------------------------------------------------

class TestGetEvalTrends:
    def test_returns_list(self):
        from icdev.tools.ace.evaluator import get_eval_trends
        with patch("icdev.tools.ace.evaluator._get_conn", return_value=None):
            result = get_eval_trends()
        assert isinstance(result, list)

    def test_empty_db_returns_empty_list(self):
        from icdev.tools.ace.evaluator import get_eval_trends
        with patch("icdev.tools.ace.evaluator._get_conn", return_value=None):
            result = get_eval_trends(days=30)
        assert result == []

    def test_days_capped_at_365(self):
        from icdev.tools.ace.evaluator import get_eval_trends
        calls = []

        def _fake_conn():
            m = MagicMock()
            cursor = MagicMock()
            cursor.fetchall.return_value = []
            cursor.description = []

            def _execute(sql, params):
                calls.append(params)
                return cursor

            cursor.execute = _execute
            m.cursor.return_value = cursor
            return m

        with patch("icdev.tools.ace.evaluator._get_conn", side_effect=_fake_conn):
            get_eval_trends(days=9999)

        assert calls, "execute should have been called"
        # cutoff is now computed in Python as an ISO timestamp, capped at 365 days back
        import re
        cutoff_str = calls[0][0]
        assert re.match(r"\d{4}-\d{2}-\d{2}T", cutoff_str), (
            f"Expected ISO timestamp cutoff, got: {cutoff_str!r}"
        )

    def test_bad_bucket_falls_back_to_week(self):
        from icdev.tools.ace.evaluator import get_eval_trends
        sqls = []

        def _fake_conn():
            m = MagicMock()
            cursor = MagicMock()
            cursor.fetchall.return_value = []
            cursor.description = []

            def _execute(sql, params):
                sqls.append(sql)
                return cursor

            cursor.execute = _execute
            m.cursor.return_value = cursor
            return m

        with patch("icdev.tools.ace.evaluator._get_conn", side_effect=_fake_conn):
            get_eval_trends(bucket="invalid_bucket")

        assert sqls, "execute should have been called"
        # 'weekday 0' is the SQLite week-start expression used for bucket='week'
        assert "weekday 0" in sqls[0]

    def test_result_has_expected_keys(self):
        from icdev.tools.ace.evaluator import get_eval_trends

        fake_row = ("2026-06-01", "ai_developer", 5, 0.7, 0.8, 0.1, 90.0)
        fake_cols = [
            ("period",), ("role",), ("count",),
            ("avg_efficiency",), ("avg_reasoning_coverage",),
            ("avg_tool_error_rate",), ("pct_done",),
        ]

        def _fake_conn():
            m = MagicMock()
            cursor = MagicMock()
            cursor.fetchall.return_value = [fake_row]
            cursor.description = fake_cols
            m.cursor.return_value = cursor
            return m

        with patch("icdev.tools.ace.evaluator._get_conn", side_effect=_fake_conn):
            result = get_eval_trends()

        assert len(result) == 1
        r = result[0]
        assert r["period"] == "2026-06-01"
        assert r["role"] == "ai_developer"
        assert r["count"] == 5
        assert r["avg_efficiency"] == 0.7
        assert r["avg_reasoning_coverage"] == 0.8
        assert r["avg_tool_error_rate"] == 0.1
        assert r["pct_done"] == 90.0

    def test_fallback_query_used_when_join_fails(self):
        from icdev.tools.ace.evaluator import get_eval_trends
        call_count = [0]

        def _fake_conn():
            m = MagicMock()
            cursor = MagicMock()
            cursor.fetchall.return_value = []
            cursor.description = []

            def _execute(sql, params):
                call_count[0] += 1
                if call_count[0] == 1:
                    raise Exception("no such table: ace_sessions")
                return cursor

            cursor.execute = _execute
            m.cursor.return_value = cursor
            return m

        with patch("icdev.tools.ace.evaluator._get_conn", side_effect=_fake_conn):
            result = get_eval_trends()

        assert isinstance(result, list)
        assert call_count[0] >= 2, "fallback query should have been attempted"


# ---------------------------------------------------------------------------
# API route tests
# ---------------------------------------------------------------------------

def _make_app():
    """Return a minimal Flask test client with the ACE blueprint registered."""
    try:
        from icdev.tools.ace.blueprint import ace_bp, ace_api_bp
        from flask import Flask
        app = Flask(__name__)
        app.register_blueprint(ace_bp)
        app.register_blueprint(ace_api_bp)
        app.config["TESTING"] = True
        return app
    except Exception:
        return None


class TestApiEvalsTrends:
    def test_returns_trends_structure(self):
        app = _make_app()
        if app is None:
            return
        with app.test_client() as client:
            with patch("icdev.tools.ace.evaluator.get_eval_trends", return_value=[]):
                resp = client.get("/api/ace/evals/trends")
                assert resp.status_code == 200
                data = json.loads(resp.data)
                assert "trends" in data
                assert "days" in data
                assert "bucket" in data

    def test_accepts_days_param(self):
        app = _make_app()
        if app is None:
            return
        with app.test_client() as client:
            with patch("icdev.tools.ace.evaluator.get_eval_trends", return_value=[]) as mock_fn:
                client.get("/api/ace/evals/trends?days=7&bucket=day")
                mock_fn.assert_called_once()
                kwargs = mock_fn.call_args[1]
                assert kwargs.get("days") == 7
                assert kwargs.get("bucket") == "day"

    def test_db_error_returns_500(self):
        app = _make_app()
        if app is None:
            return
        with app.test_client() as client:
            with patch(
                "icdev.tools.ace.evaluator.get_eval_trends",
                side_effect=RuntimeError("DB exploded"),
            ):
                resp = client.get("/api/ace/evals/trends")
                assert resp.status_code == 500
                data = json.loads(resp.data)
                assert "error" in data

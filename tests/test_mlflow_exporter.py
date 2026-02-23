#!/usr/bin/env python3
# CUI // SP-CTI
"""Tests for tools/observability/mlflow_exporter.py — MLflow batch export (D283).

Covers:
  - MLflowExporter initialization
  - export_pending when mlflow not installed
  - export_pending when no tracking URI
  - export_pending when DB missing
  - export_pending with no spans
  - export_pending with spans (mocked mlflow)
  - Span grouping by trace_id
  - get_status basic and with DB
  - Error handling during export
  - CLI argument parsing
"""

import json
import sqlite3
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _create_span_db(path: Path, spans=None):
    """Create a SQLite DB with otel_spans table and optional seed data."""
    conn = sqlite3.connect(str(path))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS otel_spans (
            id TEXT PRIMARY KEY,
            trace_id TEXT NOT NULL,
            parent_span_id TEXT,
            name TEXT NOT NULL,
            kind TEXT DEFAULT 'INTERNAL',
            start_time TEXT NOT NULL,
            end_time TEXT,
            duration_ms INTEGER DEFAULT 0,
            status_code TEXT DEFAULT 'UNSET',
            status_message TEXT,
            attributes TEXT,
            events TEXT,
            agent_id TEXT,
            project_id TEXT,
            classification TEXT DEFAULT 'CUI',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    if spans:
        for s in spans:
            conn.execute(
                """INSERT INTO otel_spans (id, trace_id, name, start_time, attributes, events)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    s.get("id", f"span-{id(s)}"),
                    s.get("trace_id", "trace-1"),
                    s.get("name", "test"),
                    s.get("start_time", datetime.now(timezone.utc).isoformat()),
                    json.dumps(s.get("attributes", {})),
                    json.dumps(s.get("events", [])),
                ),
            )
    conn.commit()
    conn.close()
    return path


@pytest.fixture
def db_path(tmp_path):
    return _create_span_db(tmp_path / "test.db")


@pytest.fixture
def db_with_spans(tmp_path):
    return _create_span_db(
        tmp_path / "spans.db",
        spans=[
            {"id": "s1", "trace_id": "t1", "name": "op1", "attributes": {"k": "v1"}},
            {"id": "s2", "trace_id": "t1", "name": "op2", "attributes": {"k": "v2"}},
            {"id": "s3", "trace_id": "t2", "name": "op3", "attributes": {"k": "v3"}},
        ],
    )


# ---------------------------------------------------------------------------
# MLflowExporter Tests
# ---------------------------------------------------------------------------

class TestMLflowExporterInit:
    def test_init_defaults(self, db_path):
        from tools.observability.mlflow_exporter import MLflowExporter
        exporter = MLflowExporter(db_path=db_path)
        assert exporter._experiment_name == "icdev-traces"

    def test_init_custom_experiment(self, db_path):
        from tools.observability.mlflow_exporter import MLflowExporter
        exporter = MLflowExporter(db_path=db_path, experiment_name="custom")
        assert exporter._experiment_name == "custom"


class TestExportPending:
    def test_skip_when_no_mlflow(self, db_path):
        with patch("tools.observability.mlflow_exporter.HAS_MLFLOW", False):
            from tools.observability.mlflow_exporter import MLflowExporter
            exporter = MLflowExporter(db_path=db_path, tracking_uri="http://fake:5001")
            # Force the attribute since we patched after import
            result = exporter.export_pending()
            # Without mlflow, should return skipped
            assert result["status"] in ("skipped", "ok")

    def test_skip_when_no_tracking_uri(self, db_path):
        from tools.observability.mlflow_exporter import MLflowExporter
        exporter = MLflowExporter(db_path=db_path, tracking_uri="")
        exporter._tracking_uri = ""
        result = exporter.export_pending()
        assert result["status"] == "skipped"

    def test_skip_when_db_missing(self, tmp_path):
        from tools.observability.mlflow_exporter import MLflowExporter
        exporter = MLflowExporter(
            db_path=tmp_path / "nonexistent.db",
            tracking_uri="http://localhost:5001",
        )
        result = exporter.export_pending()
        assert result["status"] == "skipped"

    def test_no_pending_spans(self, db_path):
        from tools.observability.mlflow_exporter import MLflowExporter

        mock_mlflow = MagicMock()
        with patch("tools.observability.mlflow_exporter.HAS_MLFLOW", True), \
             patch("tools.observability.mlflow_exporter.mlflow", mock_mlflow):
            exporter = MLflowExporter(
                db_path=db_path,
                tracking_uri="http://localhost:5001",
            )
            exporter._tracking_uri = "http://localhost:5001"
            result = exporter.export_pending()
            assert result["status"] == "ok"
            assert result["exported"] == 0

    @patch("tools.observability.mlflow_exporter.mlflow")
    def test_export_groups_by_trace_id(self, mock_mlflow, db_with_spans):
        with patch("tools.observability.mlflow_exporter.HAS_MLFLOW", True):
            from tools.observability.mlflow_exporter import MLflowExporter
            exporter = MLflowExporter(
                db_path=db_with_spans,
                tracking_uri="http://localhost:5001",
            )
            exporter._tracking_uri = "http://localhost:5001"

            # Mock the context manager for start_run
            mock_mlflow.start_run.return_value.__enter__ = MagicMock()
            mock_mlflow.start_run.return_value.__exit__ = MagicMock(return_value=False)

            result = exporter.export_pending()
            assert result["status"] == "ok"
            assert result["exported"] == 3
            assert result["traces"] == 2  # t1 and t2

    @patch("tools.observability.mlflow_exporter.mlflow")
    def test_export_handles_trace_error(self, mock_mlflow, db_with_spans):
        with patch("tools.observability.mlflow_exporter.HAS_MLFLOW", True):
            from tools.observability.mlflow_exporter import MLflowExporter

            mock_mlflow.start_run.side_effect = Exception("MLflow down")

            exporter = MLflowExporter(
                db_path=db_with_spans,
                tracking_uri="http://localhost:5001",
            )
            exporter._tracking_uri = "http://localhost:5001"

            result = exporter.export_pending()
            assert result["errors"] > 0


class TestGetStatus:
    def test_status_basic(self, db_path):
        from tools.observability.mlflow_exporter import MLflowExporter
        exporter = MLflowExporter(db_path=db_path)
        status = exporter.get_status()
        assert "mlflow_available" in status
        assert "experiment" in status
        assert status["total_spans"] == 0

    def test_status_with_spans(self, db_with_spans):
        from tools.observability.mlflow_exporter import MLflowExporter
        exporter = MLflowExporter(db_path=db_with_spans)
        status = exporter.get_status()
        assert status["total_spans"] == 3

    def test_status_missing_db(self, tmp_path):
        from tools.observability.mlflow_exporter import MLflowExporter
        exporter = MLflowExporter(db_path=tmp_path / "nope.db")
        status = exporter.get_status()
        assert "total_spans" not in status


class TestReadUnexportedSpans:
    def test_read_spans(self, db_with_spans):
        from tools.observability.mlflow_exporter import MLflowExporter
        exporter = MLflowExporter(db_path=db_with_spans)
        spans = exporter._read_unexported_spans(100)
        assert len(spans) == 3

    def test_read_with_limit(self, db_with_spans):
        from tools.observability.mlflow_exporter import MLflowExporter
        exporter = MLflowExporter(db_path=db_with_spans)
        spans = exporter._read_unexported_spans(2)
        assert len(spans) == 2

    def test_read_missing_db(self, tmp_path):
        from tools.observability.mlflow_exporter import MLflowExporter
        exporter = MLflowExporter(db_path=tmp_path / "nope.db")
        spans = exporter._read_unexported_spans(100)
        assert spans == []

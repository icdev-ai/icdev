# CUI // SP-CTI
"""Tests for Phase III — DDC lineage tagging on IQE external fetches.

Connections are obtained through ``tools.db.storage`` (the translate layer)
rather than a raw ``sqlite3.connect(":memory:")`` + hand-written schema. This
exercises the real ``StorageConnection``/``translate_sql`` path and seeds the
canonical DDC DDL (``tools.data_canvas.db.init_db.SCHEMA``), so column/DDL drift
in ``data_designs`` / ``dd_lineage`` surfaces as a test failure instead of a
silent false pass (a known repo pitfall where tests passed by accident).
"""
from __future__ import annotations

import importlib
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Ensure repo root on path
sys.path.insert(0, str(Path(__file__).parent.parent))

# Module objects resolved via importlib so patch.object targets the exact
# module the code under test imports (tools.* and icdev.tools.* are DISTINCT
# objects — shim-aware patching). record_external_fetch does a lazy
# ``from tools.data_canvas.db.init_db import get_connection`` INSIDE the call,
# so the name must be patched on the init_db module, not on lineage.
_INIT_DB = importlib.import_module("tools.data_canvas.db.init_db")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def ddc_conn(tmp_path):
    """A StorageConnection (SQLite backend under conftest) seeded with the real
    DDC schema via the storage translate layer — no raw sqlite3.connect.

    Under tests/conftest.py ICDEV_STORAGE_BACKEND is forced to sqlite, so
    get_connection(db_path=<tmp .db>) returns a StorageConnection wrapping a
    dedicated, RLS-free SQLite file. Seeding through executescript() and every
    later conn.execute() therefore flow through StorageCursor/translate_sql.
    """
    from tools.db.storage import get_connection

    db_path = tmp_path / "data_canvas_test.db"
    conn = get_connection(db_path=str(db_path))
    conn.executescript(_INIT_DB.SCHEMA)
    conn.commit()
    try:
        yield conn
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# record_external_fetch unit tests
# ---------------------------------------------------------------------------

class TestRecordExternalFetch:
    def test_inserts_lineage_row(self, ddc_conn):
        """Successful call inserts exactly one row into dd_lineage."""
        from tools.data_canvas.lineage import record_external_fetch

        with patch.object(_INIT_DB, "get_connection", return_value=ddc_conn):
            record_external_fetch("splunk", "alerts", 42)

        rows = ddc_conn.execute("SELECT * FROM dd_lineage").fetchall()
        assert len(rows) == 1
        row = dict(rows[0])
        assert row["source_node_id"] == "ext.splunk.alerts"
        assert row["target_node_id"] == "iqe.query.result"
        assert row["lineage_type"] == "col-passthrough"
        assert row["column_name"] == "*"
        assert "42 rows" in row["transform_desc"]

    def test_creates_sentinel_design(self, ddc_conn):
        """_ensure_ext_provenance_design creates the sentinel row on first call."""
        from tools.data_canvas.lineage import (
            _EXT_PROVENANCE_DESIGN_ID,
            _ensure_ext_provenance_design,
        )

        _ensure_ext_provenance_design(ddc_conn)

        designs = ddc_conn.execute(
            "SELECT * FROM data_designs WHERE id = ?", (_EXT_PROVENANCE_DESIGN_ID,)
        ).fetchall()
        assert len(designs) == 1
        assert dict(designs[0])["name"] == "External IQE Query Provenance"

    def test_sentinel_design_idempotent(self, ddc_conn):
        """Calling _ensure_ext_provenance_design twice does not duplicate the sentinel row."""
        from tools.data_canvas.lineage import (
            _EXT_PROVENANCE_DESIGN_ID,
            _ensure_ext_provenance_design,
        )

        _ensure_ext_provenance_design(ddc_conn)
        _ensure_ext_provenance_design(ddc_conn)

        count = ddc_conn.execute(
            "SELECT COUNT(*) FROM data_designs WHERE id = ?", (_EXT_PROVENANCE_DESIGN_ID,)
        ).fetchone()[0]
        assert count == 1

    def test_returns_dict_with_expected_keys(self, ddc_conn):
        """Return dict must contain id, design_id, source_node_id, row_count, created_at."""
        from tools.data_canvas.lineage import record_external_fetch

        with patch.object(_INIT_DB, "get_connection", return_value=ddc_conn):
            result = record_external_fetch("tenable", "vulnerabilities", 10)

        assert result is not None
        assert result["source_node_id"] == "ext.tenable.vulnerabilities"
        assert result["row_count"] == 10
        assert result["lineage_type"] == "col-passthrough"
        assert "id" in result
        assert "created_at" in result

    def test_returns_none_on_connection_error(self):
        """record_external_fetch returns None (never raises) when DB is unavailable."""
        from tools.data_canvas.lineage import record_external_fetch

        with patch.object(_INIT_DB, "get_connection",
                          side_effect=RuntimeError("no DB")):
            result = record_external_fetch("gdelt", "events", 5)

        assert result is None

    def test_classification_default(self, ddc_conn):
        """Default classification is CUI // SP-CTI."""
        from tools.data_canvas.lineage import record_external_fetch

        with patch.object(_INIT_DB, "get_connection", return_value=ddc_conn):
            result = record_external_fetch("servicenow_itsm", "incident", 3)

        assert result is not None
        assert result["classification"] == "CUI // SP-CTI"


# ---------------------------------------------------------------------------
# _record_lineage integration with _safe_fetch
# ---------------------------------------------------------------------------

class TestSafeFetchLineageIntegration:
    def _make_mock_connector(self, rows: list[dict]):
        """Build a mock DataBridge connector that returns the given rows."""
        from tools.databridge.connector import ConnectorResponse

        connector = MagicMock()
        connector.read.return_value = ConnectorResponse(status="ok", data=rows)
        return connector

    def test_safe_fetch_calls_record_lineage_on_non_empty_result(self):
        """_safe_fetch calls _record_lineage when the connector returns rows."""
        from tools.iqe.adapters import ext_databridge

        rows = [{"id": "INC001", "urgency": "high"}]
        mock_connector = self._make_mock_connector(rows)

        with patch("tools.databridge.registry.get_connector_instance",
                   return_value=mock_connector):
            with patch.object(ext_databridge, "_record_lineage") as mock_rec:
                result = ext_databridge._safe_fetch("splunk", "alerts")

        mock_rec.assert_called_once_with("splunk", "alerts", 1)
        assert result == rows

    def test_safe_fetch_skips_lineage_on_empty_result(self):
        """_safe_fetch does NOT call _record_lineage when connector returns no rows."""
        from tools.iqe.adapters import ext_databridge

        mock_connector = self._make_mock_connector([])

        with patch("tools.databridge.registry.get_connector_instance",
                   return_value=mock_connector):
            with patch.object(ext_databridge, "_record_lineage") as mock_rec:
                result = ext_databridge._safe_fetch("splunk", "alerts")

        mock_rec.assert_not_called()
        assert result == []

    def test_record_lineage_failure_does_not_propagate(self):
        """If _record_lineage raises, _safe_fetch still returns rows normally."""
        from tools.iqe.adapters import ext_databridge

        rows = [{"event_id": "E1"}]
        mock_connector = self._make_mock_connector(rows)

        with patch("tools.databridge.registry.get_connector_instance",
                   return_value=mock_connector):
            with patch.object(ext_databridge, "_record_lineage",
                              side_effect=RuntimeError("lineage DB down")):
                result = ext_databridge._safe_fetch("splunk", "events")

        # Rows still returned despite lineage failure
        assert result == rows

# CUI // SP-CTI
"""E2E test: Network diagram ingestion with OCR fallback.

Verifies the full lifecycle: vision LLM fails → OCR fallback fires →
topology persisted to DB (topologies, ni_devices) → result contains
ocr_used and method fields.
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))


# ── Fixtures ───────────────────────────────────────────────────────────────


class _UnclosableConnection:
    """Wrapper that ignores .close() so in-memory DB survives ingest_diagram's finally block."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def close(self) -> None:
        pass  # Intentionally ignored

    def __getattr__(self, name: str):
        return getattr(self._conn, name)


@pytest.fixture
def mem_db():
    """In-memory SQLite with NII schema for testing."""
    conn = sqlite3.connect(":memory:")
    conn.execute("""
        CREATE TABLE topologies (
            id TEXT PRIMARY KEY,
            name TEXT,
            graph_json TEXT,
            classification TEXT DEFAULT 'CUI // SP-CTI',
            created_at TEXT,
            updated_at TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE ni_devices (
            id TEXT PRIMARY KEY,
            topology_id TEXT,
            node_id TEXT NOT NULL,
            label TEXT NOT NULL,
            device_type TEXT NOT NULL,
            vendor TEXT,
            model TEXT,
            firmware_version TEXT,
            site TEXT,
            properties_json TEXT DEFAULT '{}',
            created_at TEXT,
            updated_at TEXT
        )
    """)
    conn.commit()
    wrapper = _UnclosableConnection(conn)
    yield wrapper
    conn.close()


@pytest.fixture
def test_image(tmp_path):
    """Create a dummy test image."""
    from PIL import Image

    img_path = tmp_path / "network_diagram.png"
    Image.new("RGB", (1920, 1080), "white").save(str(img_path))
    return img_path


# ── E2E Tests ──────────────────────────────────────────────────────────────


class TestIngestDiagramOCRFallback:
    """Test that ingest_diagram() falls through to OCR when vision fails."""

    def test_ocr_fallback_on_vision_failure(self, test_image, mem_db):
        """Vision fails → OCR succeeds → topology persisted to DB."""
        # Mock vision to fail
        vision_error = {"nodes": [], "edges": [], "error": "No LLM providers available"}

        # Mock OCR to return a known topology
        mock_ocr_result = {
            "nodes": [
                {"id": "r1", "label": "Core-Router", "type": "router", "x": 100, "y": 100},
                {"id": "sw1", "label": "Switch-A", "type": "switch", "x": 300, "y": 100},
                {"id": "fw1", "label": "Firewall-1", "type": "firewall", "x": 500, "y": 100},
            ],
            "edges": [
                {"id": "e1", "source": "r1", "target": "sw1", "label": "10G", "type": "ethernet"},
                {"id": "e2", "source": "sw1", "target": "fw1", "label": "Fiber", "type": "fiber"},
            ],
            "confidence": 0.85,
            "method": "ocr_ensemble",
        }

        with (
            patch("tools.network.network_ingester._extract_via_vision", return_value=vision_error),
            patch("tools.network.ocr_fallback.extract_topology_via_ocr", return_value=mock_ocr_result),
            patch("tools.network.db.init_db.get_connection", return_value=mem_db),
            patch("tools.network.network_ingester._persist_to_knowledge_graph", return_value=3),
        ):
            # Need to patch the import inside ingest_diagram
            with patch.dict("sys.modules", {}):
                from tools.network.network_ingester import ingest_diagram

                result = ingest_diagram(str(test_image), project_id="test-proj")

        # Verify result
        assert result["topology_id"] is not None
        assert result["node_count"] == 3
        assert result["edge_count"] == 2
        assert result["device_count"] == 3
        assert result["vision_used"] is False
        assert result["ocr_used"] is True
        assert "ocr" in result["format"]

        # Verify DB persistence
        rows = mem_db.execute("SELECT COUNT(*) FROM topologies").fetchone()
        assert rows[0] == 1

        devices = mem_db.execute("SELECT label, device_type FROM ni_devices ORDER BY label").fetchall()
        assert len(devices) == 3
        labels = [d[0] for d in devices]
        assert "Core-Router" in labels
        assert "Switch-A" in labels
        assert "Firewall-1" in labels

    def test_vision_succeeds_no_ocr(self, test_image, mem_db):
        """When vision succeeds, OCR should not be called."""
        vision_result = {
            "nodes": [
                {"id": "r1", "label": "Router", "type": "router", "x": 0, "y": 0},
            ],
            "edges": [],
            "confidence": 0.95,
        }

        with (
            patch("tools.network.network_ingester._extract_via_vision", return_value=vision_result),
            patch("tools.network.db.init_db.get_connection", return_value=mem_db),
            patch("tools.network.network_ingester._persist_to_knowledge_graph", return_value=1),
            patch("tools.network.ocr_fallback.extract_topology_via_ocr") as mock_ocr,
        ):
            from tools.network.network_ingester import ingest_diagram

            result = ingest_diagram(str(test_image), project_id="test-proj")

        assert result["vision_used"] is True
        assert result["ocr_used"] is False
        mock_ocr.assert_not_called()

    def test_both_vision_and_ocr_fail(self, test_image, mem_db):
        """When both vision and OCR fail, should return error."""
        vision_error = {"nodes": [], "edges": [], "error": "No LLM available"}
        ocr_error = {"nodes": [], "edges": [], "error": "No OCR providers"}

        with (
            patch("tools.network.network_ingester._extract_via_vision", return_value=vision_error),
            patch("tools.network.ocr_fallback.extract_topology_via_ocr", return_value=ocr_error),
        ):
            from tools.network.network_ingester import ingest_diagram

            result = ingest_diagram(str(test_image), project_id="test-proj")

        # Should report no devices found
        assert result.get("error") is not None or result.get("node_count", 0) == 0


class TestIngestPDFOCRFallback:
    """Test that PDF extraction falls through to OCR when vision fails."""

    def test_pdf_ocr_fallback(self, tmp_path):
        """PDF vision fails → OCR succeeds."""
        pdf_path = tmp_path / "network.pdf"
        pdf_path.write_bytes(b"%PDF-1.4 dummy")  # Not a real PDF, but tests the fallback path

        mock_ocr_result = {
            "nodes": [
                {"id": "s1", "label": "Server-1", "type": "server", "x": 100, "y": 100},
            ],
            "edges": [],
            "confidence": 0.7,
            "method": "ocr_tesseract",
        }

        with (
            patch("tools.network.network_ingester._extract_via_vision", return_value={"nodes": [], "edges": [], "error": "fail"}),
            patch("tools.network.ocr_fallback.extract_topology_via_ocr", return_value=mock_ocr_result),
        ):
            from tools.network.network_ingester import _extract_from_pdf

            result = _extract_from_pdf(str(pdf_path))

        assert len(result["nodes"]) == 1
        assert result["method"] == "ocr_tesseract"

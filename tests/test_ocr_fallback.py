# CUI // SP-CTI
"""Tests for tools/network/ocr_fallback.py — OCR-based diagram extraction fallback."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.network.ocr_fallback import (
    OCRResult,
    RapidOCRProvider,
    TesseractOCRProvider,
    _compute_iou,
    _group_adjacent_words,
    _infer_connections_from_spatial,
    _merge_ocr_results,
    _ocr_results_to_topology,
    check_ocr_availability,
    extract_topology_via_ocr,
    get_ocr_provider_chain,
)


# ── OCRResult dataclass ────────────────────────────────────────────────────


class TestOCRResult:
    def test_center(self):
        r = OCRResult(text="Router-1", confidence=0.95, bbox=(100, 200, 300, 400))
        assert r.center_x == 200.0
        assert r.center_y == 300.0

    def test_dimensions(self):
        r = OCRResult(text="Switch", confidence=0.8, bbox=(10, 20, 50, 80))
        assert r.width == 40
        assert r.height == 60
        assert r.area == 2400


# ── Provider availability ──────────────────────────────────────────────────


class TestTesseractOCRProvider:
    def test_check_availability_no_tesseract(self):
        provider = TesseractOCRProvider()
        with patch.dict("sys.modules", {"pytesseract": None}):
            assert provider.check_availability() is False

    def test_check_availability_with_tesseract(self):
        mock_pytesseract = MagicMock()
        mock_pytesseract.get_tesseract_version.return_value = "5.3.0"
        with patch.dict("sys.modules", {"pytesseract": mock_pytesseract}):
            provider = TesseractOCRProvider()
            # Re-import inside the mock context
            assert provider.check_availability() is True

    def test_extract_mocked(self, tmp_path):
        """Mock pytesseract.image_to_data to return known results."""
        mock_pytesseract = MagicMock()
        mock_pytesseract.Output.DICT = "dict"
        mock_pytesseract.image_to_data.return_value = {
            "text": ["Core", "Router", "", "Firewall-1"],
            "conf": [92.0, 88.0, -1.0, 95.0],
            "left": [100, 160, 0, 400],
            "top": [50, 50, 0, 50],
            "width": [50, 60, 0, 80],
            "height": [20, 20, 0, 20],
        }

        # Create a dummy image
        from PIL import Image
        img_path = tmp_path / "test.png"
        Image.new("RGB", (800, 600), "white").save(str(img_path))

        with patch.dict("sys.modules", {"pytesseract": mock_pytesseract}):
            provider = TesseractOCRProvider()
            results = provider.extract_text_with_boxes(img_path)

        # "Core" and "Router" should be grouped into "Core Router"
        texts = [r.text for r in results]
        assert "Firewall-1" in texts
        # Core and Router are adjacent, should be grouped
        assert any("Core" in t and "Router" in t for t in texts)


class TestRapidOCRProvider:
    def test_check_availability_no_rapidocr(self):
        provider = RapidOCRProvider()
        with patch.dict("sys.modules", {"rapidocr_onnxruntime": None}):
            assert provider.check_availability() is False

    def test_extract_mocked(self, tmp_path):
        """Mock RapidOCR engine to return known results."""
        mock_rapid = MagicMock()
        mock_engine_instance = MagicMock()
        mock_engine_instance.return_value = (
            [
                ([[100, 50], [200, 50], [200, 70], [100, 70]], "Switch-A", 0.91),
                ([[400, 50], [500, 50], [500, 70], [400, 70]], "Router-B", 0.87),
            ],
            None,
        )
        mock_rapid.RapidOCR.return_value = mock_engine_instance

        from PIL import Image
        img_path = tmp_path / "test.png"
        Image.new("RGB", (800, 600), "white").save(str(img_path))

        with patch.dict("sys.modules", {"rapidocr_onnxruntime": mock_rapid}):
            provider = RapidOCRProvider()
            results = provider.extract_text_with_boxes(img_path)

        assert len(results) == 2
        assert results[0].text == "Switch-A"
        assert results[0].bbox == (100, 50, 200, 70)
        assert results[1].text == "Router-B"


# ── Word grouping ──────────────────────────────────────────────────────────


class TestGroupAdjacentWords:
    def test_groups_same_line(self):
        words = [
            OCRResult("Core", 0.9, (100, 50, 150, 70)),
            OCRResult("Router", 0.9, (155, 50, 220, 70)),
            OCRResult("Firewall", 0.9, (400, 50, 490, 70)),
        ]
        grouped = _group_adjacent_words(words)
        texts = [r.text for r in grouped]
        assert "Core Router" in texts
        assert "Firewall" in texts

    def test_no_group_different_lines(self):
        words = [
            OCRResult("Router", 0.9, (100, 50, 200, 70)),
            OCRResult("Switch", 0.9, (100, 200, 200, 220)),
        ]
        grouped = _group_adjacent_words(words)
        assert len(grouped) == 2

    def test_empty_input(self):
        assert _group_adjacent_words([]) == []


# ── IoU computation ────────────────────────────────────────────────────────


class TestComputeIoU:
    def test_identical_boxes(self):
        assert _compute_iou((0, 0, 100, 100), (0, 0, 100, 100)) == 1.0

    def test_no_overlap(self):
        assert _compute_iou((0, 0, 50, 50), (100, 100, 200, 200)) == 0.0

    def test_partial_overlap(self):
        iou = _compute_iou((0, 0, 100, 100), (50, 50, 150, 150))
        assert 0.1 < iou < 0.5  # 2500 / (10000 + 10000 - 2500) = 0.143

    def test_zero_area_box(self):
        assert _compute_iou((0, 0, 0, 0), (0, 0, 100, 100)) == 0.0


# ── Ensemble merging ──────────────────────────────────────────────────────


class TestMergeOCRResults:
    def test_overlapping_detections_keep_higher_confidence(self):
        a = [OCRResult("Router-1", 0.8, (100, 50, 200, 70))]
        b = [OCRResult("Router-1", 0.9, (105, 48, 198, 72))]
        merged = _merge_ocr_results(a, b, iou_threshold=0.3, confidence_boost=0.1)
        assert len(merged) == 1
        assert merged[0].confidence == 1.0  # 0.9 + 0.1 boost

    def test_non_overlapping_union(self):
        a = [OCRResult("Router", 0.9, (100, 50, 200, 70))]
        b = [OCRResult("Switch", 0.8, (400, 50, 500, 70))]
        merged = _merge_ocr_results(a, b, iou_threshold=0.5)
        assert len(merged) == 2
        texts = {r.text for r in merged}
        assert texts == {"Router", "Switch"}

    def test_mixed_overlap_and_unique(self):
        a = [
            OCRResult("Router", 0.9, (100, 50, 200, 70)),
            OCRResult("Server", 0.7, (300, 200, 400, 220)),
        ]
        b = [
            OCRResult("Router-1", 0.85, (102, 48, 198, 72)),  # Overlaps a[0]
            OCRResult("Firewall", 0.8, (500, 50, 600, 70)),   # Unique
        ]
        merged = _merge_ocr_results(a, b, iou_threshold=0.3, confidence_boost=0.2)
        assert len(merged) == 3
        texts = {r.text for r in merged}
        assert "Server" in texts
        assert "Firewall" in texts

    def test_empty_inputs(self):
        assert _merge_ocr_results([], []) == []
        a = [OCRResult("Router", 0.9, (100, 50, 200, 70))]
        assert len(_merge_ocr_results(a, [])) == 1
        assert len(_merge_ocr_results([], a)) == 1


# ── Spatial inference ──────────────────────────────────────────────────────


class TestSpatialInference:
    def test_close_devices_connected(self):
        """Two devices within proximity threshold should be connected."""
        devices = [
            {"id": "a", "label": "Router-1", "type": "router", "center_x": 100, "center_y": 100},
            {"id": "b", "label": "Switch-1", "type": "switch", "center_x": 250, "center_y": 100},
        ]
        edges = _infer_connections_from_spatial(devices, [], proximity_threshold_px=300)
        assert len(edges) == 1
        assert edges[0]["source"] == "a"
        assert edges[0]["target"] == "b"

    def test_far_devices_not_connected(self):
        """Two devices beyond proximity threshold should not be connected."""
        devices = [
            {"id": "a", "label": "Router-1", "type": "router", "center_x": 100, "center_y": 100},
            {"id": "b", "label": "Switch-1", "type": "switch", "center_x": 1000, "center_y": 1000},
        ]
        edges = _infer_connections_from_spatial(devices, [], proximity_threshold_px=300)
        assert len(edges) == 0

    def test_annotation_on_line_sets_link_type(self):
        """Annotation text between two devices should set the edge label/type."""
        devices = [
            {"id": "a", "label": "Router-1", "type": "router", "center_x": 100, "center_y": 100},
            {"id": "b", "label": "Switch-1", "type": "switch", "center_x": 300, "center_y": 100},
        ]
        annotations = [
            {"text": "10G Fiber", "center_x": 200, "center_y": 100},
        ]
        edges = _infer_connections_from_spatial(devices, annotations, proximity_threshold_px=300)
        assert len(edges) == 1
        assert edges[0]["label"] == "10G Fiber"
        assert edges[0]["type"] == "fiber"

    def test_max_edges_per_device_pruning(self):
        """Devices should not exceed max_edges_per_device (8)."""
        # Create a star topology: one center + 10 leaves
        devices = [{"id": "center", "label": "Core-Router", "type": "router", "center_x": 500, "center_y": 500}]
        for i in range(10):
            angle = i * 36  # Spread around circle
            import math
            x = 500 + int(100 * math.cos(math.radians(angle)))
            y = 500 + int(100 * math.sin(math.radians(angle)))
            devices.append({"id": f"leaf-{i}", "label": f"Switch-{i}", "type": "switch", "center_x": x, "center_y": y})

        edges = _infer_connections_from_spatial(devices, [], proximity_threshold_px=300)
        center_edges = [e for e in edges if e["source"] == "center" or e["target"] == "center"]
        assert len(center_edges) <= 8

    def test_single_device_no_edges(self):
        devices = [{"id": "a", "label": "Router", "type": "router", "center_x": 100, "center_y": 100}]
        edges = _infer_connections_from_spatial(devices, [])
        assert len(edges) == 0

    def test_no_duplicate_edges(self):
        """Should not create duplicate edges for the same pair."""
        devices = [
            {"id": "a", "label": "Router", "type": "router", "center_x": 100, "center_y": 100},
            {"id": "b", "label": "Switch", "type": "switch", "center_x": 200, "center_y": 100},
        ]
        edges = _infer_connections_from_spatial(devices, [], proximity_threshold_px=300)
        pairs = [(e["source"], e["target"]) for e in edges]
        assert len(pairs) == len(set(tuple(sorted(p)) for p in pairs))


# ── OCR-to-topology conversion ────────────────────────────────────────────


class TestOCRResultsToTopology:
    def test_basic_conversion(self):
        ocr_results = [
            OCRResult("Core Router", 0.9, (100, 50, 250, 70)),
            OCRResult("Firewall-1", 0.85, (300, 50, 420, 70)),
            OCRResult("10G Fiber", 0.7, (200, 55, 290, 65)),  # Annotation
        ]
        config = {"spatial_inference": {"min_confidence": 0.4, "proximity_threshold_px": 400}}
        topo = _ocr_results_to_topology(ocr_results, config)

        assert len(topo["nodes"]) == 2
        types = {n["type"] for n in topo["nodes"]}
        assert "router" in types
        assert "firewall" in types
        assert topo["confidence"] > 0

    def test_filters_low_confidence(self):
        ocr_results = [
            OCRResult("Router", 0.9, (100, 50, 200, 70)),
            OCRResult("noise", 0.1, (500, 500, 520, 510)),  # Below threshold
        ]
        config = {"spatial_inference": {"min_confidence": 0.4}}
        topo = _ocr_results_to_topology(ocr_results, config)
        assert len(topo["nodes"]) == 1

    def test_empty_results(self):
        topo = _ocr_results_to_topology([], {})
        assert topo["nodes"] == []
        assert topo["edges"] == []


# ── Provider chain ─────────────────────────────────────────────────────────


class TestProviderChain:
    def test_empty_when_nothing_installed(self):
        with patch.dict("sys.modules", {"pytesseract": None, "rapidocr_onnxruntime": None}):
            chain = get_ocr_provider_chain({})
            assert len(chain) == 0

    def test_respects_config_order(self):
        config = {"provider_chain": ["rapidocr", "tesseract"]}
        # Even with custom order, only available providers are returned
        chain = get_ocr_provider_chain(config)
        # Result depends on installed libs — just verify it doesn't crash
        assert isinstance(chain, list)


# ── extract_topology_via_ocr integration ───────────────────────────────────


class TestExtractTopologyViaOCR:
    def test_disabled_in_config(self):
        result = extract_topology_via_ocr("dummy.png", config={"enabled": False})
        assert result["nodes"] == []
        assert "disabled" in result["error"]

    def test_no_providers_available(self):
        with patch("tools.network.ocr_fallback.get_ocr_provider_chain", return_value=[]):
            result = extract_topology_via_ocr("dummy.png", config={"enabled": True})
            assert result["nodes"] == []
            assert "No OCR providers" in result["error"]

    def test_single_provider_mode(self, tmp_path):
        """Mock a single provider returning known detections."""
        from PIL import Image
        img_path = tmp_path / "test.png"
        Image.new("RGB", (800, 600), "white").save(str(img_path))

        mock_provider = MagicMock()
        mock_provider.provider_name = "test_ocr"
        mock_provider.check_availability.return_value = True
        mock_provider.extract_text_with_boxes.return_value = [
            OCRResult("Router-1", 0.9, (100, 50, 200, 70)),
            OCRResult("Switch-A", 0.85, (250, 50, 350, 70)),
        ]

        with patch("tools.network.ocr_fallback.get_ocr_provider_chain", return_value=[mock_provider]):
            result = extract_topology_via_ocr(
                str(img_path),
                config={"enabled": True, "ensemble": False, "spatial_inference": {"min_confidence": 0.4, "proximity_threshold_px": 400}},
            )

        assert len(result["nodes"]) == 2
        assert result["method"] == "ocr_test_ocr"

    def test_ensemble_mode(self, tmp_path):
        """Mock two providers for ensemble extraction."""
        from PIL import Image
        img_path = tmp_path / "test.png"
        Image.new("RGB", (800, 600), "white").save(str(img_path))

        provider_a = MagicMock()
        provider_a.provider_name = "tesseract"
        provider_a.extract_text_with_boxes.return_value = [
            OCRResult("Router-1", 0.9, (100, 50, 200, 70)),
        ]

        provider_b = MagicMock()
        provider_b.provider_name = "rapidocr"
        provider_b.extract_text_with_boxes.return_value = [
            OCRResult("Router-1", 0.85, (102, 48, 198, 72)),  # Overlaps
            OCRResult("Firewall-A", 0.8, (400, 50, 500, 70)),  # Unique
        ]

        with patch("tools.network.ocr_fallback.get_ocr_provider_chain", return_value=[provider_a, provider_b]):
            result = extract_topology_via_ocr(
                str(img_path),
                config={
                    "enabled": True,
                    "ensemble": True,
                    "ensemble_settings": {"iou_threshold": 0.3, "confidence_boost": 0.2},
                    "spatial_inference": {"min_confidence": 0.4, "proximity_threshold_px": 500},
                },
            )

        assert result["method"] == "ocr_ensemble"
        assert len(result["nodes"]) == 2  # Router + Firewall (both classified)


# ── Availability check ─────────────────────────────────────────────────────


class TestCheckAvailability:
    def test_returns_status_dict(self):
        status = check_ocr_availability()
        assert "available_providers" in status
        assert "ensemble_possible" in status
        assert "tesseract_available" in status
        assert "rapidocr_available" in status
        assert isinstance(status["available_providers"], list)

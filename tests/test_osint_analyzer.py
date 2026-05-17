# CUI // SP-CTI
"""
Unit tests for tools.strategos.osint_analyzer.

Acceptance criterion: running this suite on a sample satellite image dataset
generates 'potential manipulation' flags for known synthetic images and passes
real imagery without false positives.

Coverage:
  - Metadata checks: completeness, timestamp consistency, sensor calibration
  - Pixel checks: block uniformity, noise consistency, histogram anomaly
  - Combined analyze() pipeline
  - Sample dataset end-to-end acceptance test
"""
from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pytest

from tools.strategos.osint_analyzer import (
    SEVERITY_CRITICAL,
    SEVERITY_HIGH,
    SEVERITY_MEDIUM,
    AnalysisResult,
    analyze,
    analyze_metadata,
    analyze_pixels,
    check_block_uniformity,
    check_histogram_anomaly,
    check_metadata_completeness,
    check_noise_consistency,
    check_sensor_calibration,
    check_timestamp_consistency,
)

# ---------------------------------------------------------------------------
# Fixtures — synthetic image generators
# ---------------------------------------------------------------------------

RNG = np.random.default_rng(42)


def _real_image(h: int = 64, w: int = 64) -> np.ndarray:
    """Simulate real satellite sensor noise: gaussian around 128, std=30."""
    return np.clip(RNG.normal(128, 30, (h, w)), 0, 255).astype(int)


def _uniform_image(h: int = 64, w: int = 64, value: int = 128) -> np.ndarray:
    """Synthetic constant image — all pixels identical (AI fill or copy-paste)."""
    return np.full((h, w), value, dtype=int)


def _spliced_image(h: int = 64, w: int = 64) -> np.ndarray:
    """
    Spliced image: left half constant (std=0), right half gaussian noise (std≈30).
    Simulates a scene where part of the image was replaced with synthetic content.
    """
    left = np.full((h, w // 2), 50, dtype=int)
    right = np.clip(RNG.normal(150, 30, (h, w // 2)), 0, 255).astype(int)
    return np.hstack([left, right])


def _overquantized_image(h: int = 64, w: int = 64, n_values: int = 6) -> np.ndarray:
    """
    Over-quantized image: only n_values unique pixel values across the image.
    Simulates aggressive lossy compression or palette-mapped synthetic output.
    """
    palette = np.linspace(0, 255, n_values, dtype=int)
    indices = RNG.integers(0, n_values, (h, w))
    return palette[indices]


# ---------------------------------------------------------------------------
# Metadata completeness
# ---------------------------------------------------------------------------

class TestMetadataCompleteness:
    def test_complete_metadata_no_flags(self):
        meta = {"scene_id": "S2A_001", "satellite": "Sentinel-2", "sensing_date": "2025-06-01"}
        flags = check_metadata_completeness(meta)
        assert flags == []

    def test_missing_scene_id_flagged(self):
        meta = {"satellite": "Sentinel-2", "sensing_date": "2025-06-01"}
        flags = check_metadata_completeness(meta)
        assert len(flags) == 1
        assert flags[0].rule == "metadata_missing_required_fields"
        assert flags[0].severity == SEVERITY_HIGH
        assert "scene_id" in flags[0].detail

    def test_missing_all_required_fields(self):
        flags = check_metadata_completeness({})
        assert len(flags) == 1
        assert "scene_id" in flags[0].detail
        assert "satellite" in flags[0].detail
        assert "sensing_date" in flags[0].detail

    def test_empty_string_fields_flagged(self):
        meta = {"scene_id": "", "satellite": "Sentinel-2", "sensing_date": "2025-06-01"}
        flags = check_metadata_completeness(meta)
        assert any("scene_id" in f.detail for f in flags)


# ---------------------------------------------------------------------------
# Timestamp consistency
# ---------------------------------------------------------------------------

class TestTimestampConsistency:
    def test_valid_date_no_flags(self):
        meta = {"sensing_date": "2024-03-15"}
        flags = check_timestamp_consistency(meta)
        assert flags == []

    def test_future_date_critical(self):
        future = (date.today() + timedelta(days=30)).isoformat()
        meta = {"sensing_date": future}
        flags = check_timestamp_consistency(meta)
        assert any(f.rule == "timestamp_future" for f in flags)
        assert any(f.severity == SEVERITY_CRITICAL for f in flags)

    def test_pre_satellite_era_flagged(self):
        meta = {"sensing_date": "1950-01-01"}
        flags = check_timestamp_consistency(meta)
        assert any(f.rule == "timestamp_pre_satellite_era" for f in flags)
        assert any(f.severity == SEVERITY_HIGH for f in flags)

    def test_processing_before_sensing_flagged(self):
        meta = {
            "sensing_date": "2025-06-15",
            "created_at": "2025-06-01",  # before sensing_date
        }
        flags = check_timestamp_consistency(meta)
        assert any(f.rule == "timestamp_processing_before_sensing" for f in flags)
        assert any(f.severity == SEVERITY_HIGH for f in flags)

    def test_processing_after_sensing_ok(self):
        meta = {
            "sensing_date": "2025-06-01",
            "created_at": "2025-06-02",
        }
        flags = check_timestamp_consistency(meta)
        assert flags == []

    def test_missing_sensing_date_returns_empty(self):
        assert check_timestamp_consistency({}) == []

    def test_unparseable_date_flagged(self):
        meta = {"sensing_date": "not-a-date"}
        flags = check_timestamp_consistency(meta)
        assert any(f.rule == "timestamp_parse_error" for f in flags)
        assert any(f.severity == SEVERITY_HIGH for f in flags)


# ---------------------------------------------------------------------------
# Sensor calibration
# ---------------------------------------------------------------------------

class TestSensorCalibration:
    def test_valid_sensor_values_no_flags(self):
        meta = {"cloud_pct": 15.0, "relevance_score": 0.85, "resolution_m": 10.0}
        assert check_sensor_calibration(meta) == []

    def test_cloud_pct_above_100_flagged(self):
        flags = check_sensor_calibration({"cloud_pct": 110.0})
        assert any(f.rule == "sensor_cloud_pct_out_of_range" for f in flags)
        assert any(f.severity == SEVERITY_HIGH for f in flags)

    def test_cloud_pct_negative_flagged(self):
        flags = check_sensor_calibration({"cloud_pct": -5.0})
        assert any(f.rule == "sensor_cloud_pct_out_of_range" for f in flags)

    def test_cloud_pct_non_numeric_flagged(self):
        flags = check_sensor_calibration({"cloud_pct": "heavy"})
        assert any(f.rule == "sensor_cloud_pct_invalid" for f in flags)
        assert any(f.severity == SEVERITY_MEDIUM for f in flags)

    def test_relevance_score_above_1_flagged(self):
        flags = check_sensor_calibration({"relevance_score": 1.5})
        assert any(f.rule == "sensor_relevance_score_out_of_range" for f in flags)

    def test_resolution_zero_flagged(self):
        flags = check_sensor_calibration({"resolution_m": 0.0})
        assert any(f.rule == "sensor_resolution_non_positive" for f in flags)
        assert any(f.severity == SEVERITY_HIGH for f in flags)

    def test_resolution_sub_decimeter_flagged(self):
        flags = check_sensor_calibration({"resolution_m": 0.05})
        assert any(f.rule == "sensor_resolution_implausibly_high" for f in flags)
        assert any(f.severity == SEVERITY_MEDIUM for f in flags)

    def test_incidence_angle_out_of_range_flagged(self):
        flags = check_sensor_calibration({"incidence_angle": 95.0})
        assert any(f.rule == "sensor_incidence_angle_out_of_range" for f in flags)
        assert any(f.severity == SEVERITY_HIGH for f in flags)

    def test_empty_metadata_no_flags(self):
        assert check_sensor_calibration({}) == []


# ---------------------------------------------------------------------------
# Pixel — block uniformity
# ---------------------------------------------------------------------------

class TestBlockUniformity:
    def test_real_image_not_flagged(self):
        flags = check_block_uniformity(_real_image())
        assert flags == []

    def test_uniform_synthetic_flagged(self):
        flags = check_block_uniformity(_uniform_image())
        assert len(flags) == 1
        assert flags[0].rule == "pixel_block_uniformity_anomaly"
        assert flags[0].severity == SEVERITY_HIGH

    def test_image_too_small_returns_empty(self):
        small = np.array([[128, 64], [64, 128]])
        assert check_block_uniformity(small) == []

    def test_1d_array_returns_empty(self):
        assert check_block_uniformity(np.arange(64)) == []


# ---------------------------------------------------------------------------
# Pixel — noise consistency
# ---------------------------------------------------------------------------

class TestNoiseConsistency:
    def test_real_image_not_flagged(self):
        flags = check_noise_consistency(_real_image())
        assert flags == []

    def test_uniform_image_not_flagged(self):
        # All quadrants flat → ratio is undefined; no flag expected (both sides quiet)
        flags = check_noise_consistency(_uniform_image())
        assert flags == []

    def test_spliced_image_critical_flag(self):
        flags = check_noise_consistency(_spliced_image())
        assert len(flags) == 1
        assert flags[0].rule == "pixel_noise_region_mismatch"
        assert flags[0].severity == SEVERITY_CRITICAL

    def test_image_too_small_returns_empty(self):
        small = np.array([[1, 2], [3, 4]])
        assert check_noise_consistency(small) == []


# ---------------------------------------------------------------------------
# Pixel — histogram anomaly
# ---------------------------------------------------------------------------

class TestHistogramAnomaly:
    def test_real_image_not_flagged(self):
        flags = check_histogram_anomaly(_real_image())
        assert flags == []

    def test_uniform_image_flagged(self):
        flags = check_histogram_anomaly(_uniform_image())
        assert any(f.rule == "pixel_histogram_over_quantized" for f in flags)
        assert any(f.severity == SEVERITY_HIGH for f in flags)

    def test_overquantized_image_flagged(self):
        flags = check_histogram_anomaly(_overquantized_image(n_values=6))
        assert any(f.rule == "pixel_histogram_over_quantized" for f in flags)

    def test_palette_of_exactly_16_values_not_flagged(self):
        palette = np.arange(0, 256, 16, dtype=int)  # 16 values
        indices = RNG.integers(0, 16, (64, 64))
        img = palette[indices]
        flags = check_histogram_anomaly(img)
        assert flags == []

    def test_empty_array_returns_empty(self):
        assert check_histogram_anomaly(np.array([])) == []


# ---------------------------------------------------------------------------
# Combined analyze_metadata
# ---------------------------------------------------------------------------

class TestAnalyzeMetadata:
    def test_clean_metadata_result(self):
        meta = {
            "scene_id": "S2A_T32TQR_20250601",
            "satellite": "Sentinel-2",
            "sensing_date": "2025-06-01",
            "cloud_pct": 5.0,
            "relevance_score": 0.95,
        }
        result = analyze_metadata(meta)
        assert isinstance(result, AnalysisResult)
        assert result.manipulation_likely is False
        assert result.confidence == 0.0
        assert result.flags == []

    def test_future_date_manipulation_likely(self):
        meta = {
            "scene_id": "FAKE_001",
            "satellite": "Unknown",
            "sensing_date": (date.today() + timedelta(days=365)).isoformat(),
        }
        result = analyze_metadata(meta)
        assert result.manipulation_likely is True
        assert result.confidence > 0.0

    def test_to_dict_structure(self):
        meta = {"scene_id": "X", "satellite": "S", "sensing_date": "2030-01-01"}
        result = analyze_metadata(meta)
        d = result.to_dict()
        assert "manipulation_likely" in d
        assert "confidence" in d
        assert "flags" in d
        assert isinstance(d["flags"], list)


# ---------------------------------------------------------------------------
# Combined analyze_pixels
# ---------------------------------------------------------------------------

class TestAnalyzePixels:
    def test_real_image_clean(self):
        result = analyze_pixels(_real_image())
        assert result.manipulation_likely is False

    def test_uniform_image_flagged(self):
        result = analyze_pixels(_uniform_image())
        assert result.manipulation_likely is True
        rules = {f.rule for f in result.flags}
        assert "pixel_block_uniformity_anomaly" in rules

    def test_spliced_image_flagged(self):
        result = analyze_pixels(_spliced_image())
        assert result.manipulation_likely is True
        rules = {f.rule for f in result.flags}
        assert "pixel_noise_region_mismatch" in rules

    def test_overquantized_image_flagged(self):
        result = analyze_pixels(_overquantized_image())
        assert result.manipulation_likely is True
        rules = {f.rule for f in result.flags}
        assert "pixel_histogram_over_quantized" in rules


# ---------------------------------------------------------------------------
# Combined analyze() pipeline
# ---------------------------------------------------------------------------

class TestAnalyzePipeline:
    def test_raises_with_no_inputs(self):
        with pytest.raises(ValueError):
            analyze()

    def test_clean_pipeline_returns_no_manipulation(self):
        meta = {
            "scene_id": "S2A_001",
            "satellite": "Sentinel-2",
            "sensing_date": "2025-06-01",
            "cloud_pct": 10.0,
        }
        result = analyze(metadata=meta, pixels=_real_image())
        assert result.manipulation_likely is False

    def test_manipulated_metadata_and_pixels(self):
        future = (date.today() + timedelta(days=100)).isoformat()
        meta = {"scene_id": "FAKE", "satellite": "Unknown", "sensing_date": future}
        result = analyze(metadata=meta, pixels=_uniform_image())
        assert result.manipulation_likely is True
        assert result.confidence >= 0.5

    def test_metadata_only_pipeline(self):
        meta = {"scene_id": "S", "satellite": "S2", "sensing_date": "2024-01-01"}
        result = analyze(metadata=meta)
        assert result.manipulation_likely is False

    def test_pixels_only_pipeline(self):
        result = analyze(pixels=_spliced_image())
        assert result.manipulation_likely is True


# ---------------------------------------------------------------------------
# ACCEPTANCE CRITERION: sample satellite image dataset
# ---------------------------------------------------------------------------

class TestSampleDatasetAcceptanceCriterion:
    """
    Validates the acceptance criterion:

    'Running unit tests on a sample satellite image dataset generates
    potential manipulation flags for known synthetic images.'

    The dataset contains four images: one real, three known synthetic/manipulated.
    All synthetic images must be flagged; the real image must not be.
    """

    DATASET = [
        # (label, image_fn, expect_flagged)
        ("real — natural sensor noise",        _real_image,          False),
        ("synthetic — uniform constant fill",   _uniform_image,       True),
        ("spliced — two-region noise mismatch", _spliced_image,       True),
        ("over-quantized — palette-mapped",     _overquantized_image, True),
    ]

    @pytest.mark.parametrize("label,image_fn,expect_flagged", DATASET)
    def test_image_classification(self, label, image_fn, expect_flagged):
        image = image_fn()
        result = analyze_pixels(image)
        assert result.manipulation_likely == expect_flagged, (
            f"[{label}] expected manipulation_likely={expect_flagged}, "
            f"got {result.manipulation_likely}. "
            f"Flags: {[(f.rule, f.severity) for f in result.flags]}"
        )

    def test_all_synthetic_images_flagged_in_batch(self):
        """End-to-end: ensure every known-synthetic image generates at least one flag."""
        synthetic_images = [_uniform_image(), _spliced_image(), _overquantized_image()]
        for i, img in enumerate(synthetic_images):
            result = analyze_pixels(img)
            assert result.manipulation_likely, (
                f"Synthetic image #{i + 1} was not flagged. "
                f"Flags: {result.flags}"
            )
            assert len(result.flags) > 0, f"Synthetic image #{i + 1} produced no flags"

    def test_real_image_produces_no_flags(self):
        """Real imagery with natural sensor noise must not generate false positives."""
        result = analyze_pixels(_real_image())
        assert not result.manipulation_likely
        assert result.flags == []
        assert result.confidence == 0.0

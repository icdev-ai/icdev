#!/usr/bin/env python3
# CUI // SP-CTI
"""Tests for auto_indexer size anomaly detection (AI-ify opportunity 5457).

Validates the ``hardcoded_threshold -> anomaly_detection`` refactor that
replaces the fixed ``max_file_size_mb`` gate with a config-driven adaptive
cutoff that also excludes statistically anomalous large files.
"""

from __future__ import annotations

from tools.rag.auto_indexer import (
    _ANOMALY_MIN_SAMPLES,
    _ANOMALY_SIGMA_MULTIPLIER,
    _ANOMALY_SIZE_FLOOR_MB,
    _MAX_FILE_SIZE_MB_DEFAULT,
    AutoIndexer,
    _compute_size_threshold_mb,
)


# ---------------------------------------------------------------------------
# Constant sanity
# ---------------------------------------------------------------------------


class TestAnomalyConstants:
    def test_defaults_sane(self):
        assert _MAX_FILE_SIZE_MB_DEFAULT > 0
        assert _ANOMALY_SIGMA_MULTIPLIER > 0
        assert _ANOMALY_MIN_SAMPLES > 0
        assert _ANOMALY_SIZE_FLOOR_MB > 0


# ---------------------------------------------------------------------------
# Adaptive size threshold
# ---------------------------------------------------------------------------


class TestComputeSizeThreshold:
    def test_disabled_returns_static(self):
        out, computed = _compute_size_threshold_mb(
            [0.1] * 50, 10, {"enabled": False}
        )
        assert out == 10
        assert computed is False

    def test_too_few_samples_returns_static(self):
        # Below min_samples -> cannot model spread -> static fallback.
        out, computed = _compute_size_threshold_mb([0.1, 5.0, 0.2], 10, {})
        assert out == 10
        assert computed is False

    def test_tiny_corpus_does_not_block(self):
        # Uniform tiny files: anomaly_cutoff (~mean) falls below size_floor, so
        # the gate stays at the static ceiling instead of blocking everything.
        out, computed = _compute_size_threshold_mb([0.05] * 30, 10, {})
        assert out == 10
        assert computed is False

    def test_outlier_tightens_threshold(self):
        # Many ~2MB files with sane spread above the floor: the adaptive cutoff
        # is derived and stays at or below the static ceiling.
        sizes = [2.0] * 40 + [2.5, 1.5, 3.0, 1.0]
        out, computed = _compute_size_threshold_mb(
            sizes, 50, {"sigma_multiplier": 1.0}
        )
        assert computed is True
        assert out <= 50
        assert out >= _ANOMALY_SIZE_FLOOR_MB

    def test_never_more_permissive_than_static(self):
        # A corpus with a huge spread could push mean+sigma*std above the static
        # ceiling; the effective cutoff must still be capped at static_max.
        sizes = [1.0] * 20 + [100.0] * 20
        out, computed = _compute_size_threshold_mb(
            sizes, 10, {"sigma_multiplier": 3.0, "size_floor_mb": 1.0}
        )
        assert out <= 10

    def test_returns_float_and_bool(self):
        out, computed = _compute_size_threshold_mb([2.0] * 25, 50, {})
        assert isinstance(out, float)
        assert isinstance(computed, bool)


# ---------------------------------------------------------------------------
# End-to-end scan behavior
# ---------------------------------------------------------------------------


class TestScanAnomalyGate:
    def _write(self, directory, name, size_kb):
        p = directory / name
        p.write_bytes(b"x" * int(size_kb * 1024))
        return p

    def test_anomalous_large_file_gated_out(self, tmp_path):
        # 30 small ~50KB files + one anomalous 5MB file. With a low sigma the
        # adaptive cutoff drops below 5MB and excludes the outlier even though
        # the absolute ceiling (10MB) would have admitted it.
        for i in range(30):
            self._write(tmp_path, f"f{i}.txt", 50)
        self._write(tmp_path, "huge.txt", 5 * 1024)  # 5 MB

        idx = AutoIndexer(
            project_dir=str(tmp_path),
            classification="CUI",
            config={
                "max_file_size_mb": 10,
                "cui_boundary_enforcement": False,
                "size_anomaly_detection": {
                    "enabled": True,
                    "min_samples": 5,
                    "sigma_multiplier": 1.0,
                    "size_floor_mb": 0.5,
                },
            },
        )
        result = idx.scan()
        assert result["size_threshold_adaptive"] is True
        assert result["size_threshold_mb"] < 5.0
        assert result["too_large"] >= 1
        names = {f["name"] for f in result["files"]}
        assert "huge.txt" not in names
        assert "f0.txt" in names

    def test_disabled_uses_static_ceiling(self, tmp_path):
        for i in range(30):
            self._write(tmp_path, f"f{i}.txt", 50)
        self._write(tmp_path, "huge.txt", 5 * 1024)  # 5 MB, under 10MB ceiling

        idx = AutoIndexer(
            project_dir=str(tmp_path),
            classification="CUI",
            config={
                "max_file_size_mb": 10,
                "cui_boundary_enforcement": False,
                "size_anomaly_detection": {"enabled": False},
            },
        )
        result = idx.scan()
        assert result["size_threshold_adaptive"] is False
        assert result["size_threshold_mb"] == 10
        names = {f["name"] for f in result["files"]}
        assert "huge.txt" in names  # under static ceiling -> admitted

    def test_absolute_ceiling_still_enforced(self, tmp_path):
        # Even with anomaly detection disabled, a file above max_file_size_mb is
        # rejected by the absolute ceiling.
        self._write(tmp_path, "ok.txt", 100)
        self._write(tmp_path, "over.txt", 2 * 1024)  # 2 MB > 1 MB ceiling

        idx = AutoIndexer(
            project_dir=str(tmp_path),
            classification="CUI",
            config={
                "max_file_size_mb": 1,
                "cui_boundary_enforcement": False,
                "size_anomaly_detection": {"enabled": False},
            },
        )
        result = idx.scan()
        names = {f["name"] for f in result["files"]}
        assert "over.txt" not in names
        assert "ok.txt" in names
        assert result["too_large"] >= 1

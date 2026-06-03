#!/usr/bin/env python3
# CUI // SP-CTI
"""Tests for SQLite vector store anomaly detection + extracted thresholds (D-RAG-1).

Covers the aiify modernization (aiify-rm-6efad-phase-5523): hardcoded thresholds
in tools/rag/sqlite_vector_store.py extracted to named, config-overridable
constants and an adaptive anomaly-detection relevance floor derived from
historical vector searches in rag_retrieval_log.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.rag.sqlite_vector_store import (  # noqa: E402
    _ANOMALY_STDDEV_K,
    _BUSY_TIMEOUT_MS,
    _DEFAULT_TOP_K,
    _VECTOR_SCORE_FLOOR,
    SQLiteVectorStore,
    _compute_vector_anomaly_thresholds,
    _load_vector_anomaly_config,
)
from tools.rag.vector_store_provider import SearchResult  # noqa: E402


def _result(score: float, chunk_id: str = "c") -> SearchResult:
    return SearchResult(
        chunk_id=chunk_id,
        content="x",
        source_type="doc",
        source_id="s",
        source_table="t",
        chunk_index=0,
        score=score,
        final_score=score,
        metadata={},
        tier="hot",
        classification="CUI",
    )


# ---------------------------------------------------------------------------
# Extracted constants
# ---------------------------------------------------------------------------


class TestConstants:
    def test_constants_have_sane_defaults(self) -> None:
        assert _DEFAULT_TOP_K == 50
        assert _BUSY_TIMEOUT_MS == 5000

    def test_anomaly_floor_in_unit_range(self) -> None:
        assert 0.0 <= _VECTOR_SCORE_FLOOR <= 1.0
        assert _ANOMALY_STDDEV_K > 0.0


# ---------------------------------------------------------------------------
# Config-driven overrides
# ---------------------------------------------------------------------------


class TestConfigOverrides:
    def test_busy_timeout_reads_from_config(self, tmp_path: Path) -> None:
        store = SQLiteVectorStore(
            db_path=tmp_path / "v.db", config={"busy_timeout_ms": 1234}
        )
        assert store._busy_timeout == 1234

    def test_busy_timeout_defaults_when_config_empty(self, tmp_path: Path) -> None:
        store = SQLiteVectorStore(db_path=tmp_path / "v.db")
        assert store._busy_timeout == _BUSY_TIMEOUT_MS


# ---------------------------------------------------------------------------
# _load_vector_anomaly_config
# ---------------------------------------------------------------------------


class TestLoadAnomalyConfig:
    def test_inline_anomaly_block_wins(self) -> None:
        block = {"enabled": False, "min_samples": 99}
        assert _load_vector_anomaly_config({"anomaly_detection": block}) == block

    def test_inline_none_block_returns_empty(self) -> None:
        assert _load_vector_anomaly_config({"anomaly_detection": None}) == {}

    def test_falls_back_to_yaml(self) -> None:
        # The shipped rag_config.yaml carries sqlite_vector_store.anomaly_detection.
        cfg = _load_vector_anomaly_config(None)
        assert isinstance(cfg, dict)
        assert cfg.get("enabled") is True


# ---------------------------------------------------------------------------
# _compute_vector_anomaly_thresholds
# ---------------------------------------------------------------------------


class TestComputeThresholds:
    def test_disabled_uses_fallback_floor(self) -> None:
        th = _compute_vector_anomaly_thresholds({"enabled": False})
        assert th["computed"] is False
        assert th["score_floor"] == _VECTOR_SCORE_FLOOR

    def test_custom_fallback_floor_honored(self) -> None:
        th = _compute_vector_anomaly_thresholds(
            {"enabled": False, "fallback_score_floor": 0.42}
        )
        assert th["score_floor"] == 0.42

    def test_none_config_returns_valid_floor(self) -> None:
        # With None config the shipped yaml drives behaviour: either an adaptive
        # floor (when ≥ min_samples vector searches exist in rag_retrieval_log)
        # or the module fallback. Either way the floor is a sane unit-range value.
        th = _compute_vector_anomaly_thresholds(None)
        assert 0.0 <= th["score_floor"] <= 1.0
        assert isinstance(th["computed"], bool)

    def test_high_min_samples_forces_fallback(self) -> None:
        th = _compute_vector_anomaly_thresholds(
            {"enabled": True, "min_samples": 10**9}
        )
        assert th["computed"] is False
        assert th["score_floor"] == _VECTOR_SCORE_FLOOR


# ---------------------------------------------------------------------------
# flag_anomaly
# ---------------------------------------------------------------------------


@pytest.fixture
def store(tmp_path: Path) -> SQLiteVectorStore:
    # Disable adaptive calibration so the floor is deterministic at 0.30.
    s = SQLiteVectorStore(db_path=tmp_path / "v.db")
    s.configure_anomaly_detection({"anomaly_detection": {"enabled": False}})
    return s


class TestFlagAnomaly:
    def test_low_top_score_is_flagged(self, store: SQLiteVectorStore) -> None:
        result = store.flag_anomaly([_result(0.10), _result(0.05)])
        assert result["anomalous"] is True
        assert len(result["reasons"]) == 1
        assert result["score_floor"] == _VECTOR_SCORE_FLOOR

    def test_high_top_score_is_clean(self, store: SQLiteVectorStore) -> None:
        result = store.flag_anomaly([_result(0.90), _result(0.10)])
        assert result["anomalous"] is False
        assert result["reasons"] == []

    def test_empty_results_is_clean(self, store: SQLiteVectorStore) -> None:
        result = store.flag_anomaly([])
        assert result["anomalous"] is False
        assert result["reasons"] == []

    def test_exact_floor_not_flagged(self, store: SQLiteVectorStore) -> None:
        # Strict "<" comparison: equal-to-floor top score passes.
        result = store.flag_anomaly([_result(_VECTOR_SCORE_FLOOR)])
        assert result["anomalous"] is False

    def test_lazy_configuration_on_first_call(self, tmp_path: Path) -> None:
        # A store with no explicit configure call still flags via lazy init.
        s = SQLiteVectorStore(db_path=tmp_path / "v2.db")
        result = s.flag_anomaly([_result(0.99)])
        assert "score_floor" in result
        assert result["anomalous"] is False


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))

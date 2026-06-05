"""Tests for config-driven dedup threshold in genesis/promoter.py.

Verifies that the hardcoded 0.85 is replaced by a value loaded from
genesis_config.yaml (promoter.dedup.similarity_threshold).
"""
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch


BASE_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(BASE_DIR))

from tools.genesis.promoter import (
    _DEDUP_SIMILARITY_THRESHOLD_DEFAULT,
    _load_dedup_threshold,
)


class TestDeduplicityThresholdConstant:
    def test_default_constant_exists(self):
        assert isinstance(_DEDUP_SIMILARITY_THRESHOLD_DEFAULT, float)

    def test_default_value_is_0_85(self):
        assert _DEDUP_SIMILARITY_THRESHOLD_DEFAULT == 0.85


class TestLoadDeduplicityThreshold:
    def test_returns_config_value_when_present(self):
        fake_config = {"dedup": {"similarity_threshold": 0.92}}
        with patch("tools.genesis.promoter._load_promoter_config", return_value=fake_config):
            assert _load_dedup_threshold() == 0.92

    def test_returns_default_when_dedup_key_missing(self):
        with patch("tools.genesis.promoter._load_promoter_config", return_value={}):
            assert _load_dedup_threshold() == _DEDUP_SIMILARITY_THRESHOLD_DEFAULT

    def test_returns_default_when_similarity_threshold_missing(self):
        with patch("tools.genesis.promoter._load_promoter_config", return_value={"dedup": {}}):
            assert _load_dedup_threshold() == _DEDUP_SIMILARITY_THRESHOLD_DEFAULT

    def test_returns_default_on_config_error(self):
        with patch("tools.genesis.promoter._load_promoter_config", side_effect=Exception("yaml error")):
            assert _load_dedup_threshold() == _DEDUP_SIMILARITY_THRESHOLD_DEFAULT

    def test_respects_low_threshold_from_config(self):
        fake_config = {"dedup": {"similarity_threshold": 0.50}}
        with patch("tools.genesis.promoter._load_promoter_config", return_value=fake_config):
            assert _load_dedup_threshold() == 0.50


class TestCheckDuplicateUsesConfigThreshold:
    """_check_duplicate must not embed 0.85 as a literal default — it must call _load_dedup_threshold."""

    def _make_conn(self, row=None):
        conn = MagicMock()
        conn.execute.return_value.fetchone.return_value = row
        return conn

    def test_no_duplicate_returns_none(self):
        from tools.genesis.promoter import _check_duplicate

        with patch("tools.genesis.promoter.get_connection", return_value=self._make_conn(None)):
            result = _check_duplicate("abc123", "research_signal")
        assert result is None

    def test_exact_match_returns_existing_id(self):
        from tools.genesis.promoter import _check_duplicate

        fake_row = MagicMock()
        fake_row.__getitem__ = lambda self, k: "gkp-abc123" if k == "id" else None
        with patch("tools.genesis.promoter.get_connection", return_value=self._make_conn(fake_row)):
            result = _check_duplicate("abc123", "research_signal")
        assert result == "gkp-abc123"

    def test_threshold_is_loaded_from_config_not_hardcoded(self):
        """Ensures _load_dedup_threshold is invoked, meaning 0.85 is not hardcoded."""
        from tools.genesis.promoter import _check_duplicate

        with patch("tools.genesis.promoter._load_dedup_threshold", return_value=0.90) as mock_load, \
             patch("tools.genesis.promoter.get_connection", return_value=self._make_conn(None)):
            _check_duplicate("some_hash", "research_signal")
        mock_load.assert_called_once()

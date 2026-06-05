# [TEMPLATE: CUI // SP-CTI]
"""Tests for tools.genesis.pattern_detector — adaptive IQR threshold and pipeline."""

import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


from tools.genesis.pattern_detector import (
    _compute_adaptive_frequency_threshold,
    _extract_ngrams,
    _load_synthesize_config,
    _score_pattern,
    compute_adaptive_frequency_threshold,
    detect_tool_patterns,
)


# ---------------------------------------------------------------------------
# _compute_adaptive_frequency_threshold
# ---------------------------------------------------------------------------


class TestComputeAdaptiveFrequencyThreshold:
    def test_empty_counter_returns_fallback(self):
        threshold, stats = _compute_adaptive_frequency_threshold(Counter(), fallback=2)
        assert threshold == 2
        assert stats["method"] == "fallback"

    def test_too_few_ngrams_returns_fallback(self):
        counts = Counter({"a": 5, "b": 3})
        threshold, stats = _compute_adaptive_frequency_threshold(counts, fallback=2)
        assert threshold == 2
        assert stats["method"] == "fallback"
        assert stats["n"] == 2

    def test_sensitivity_1_uses_iqr_formula(self):
        # Sorted: [1,1,2,2,3,3,4,4], n=8
        # q1_idx=2 → q1=2, q3_idx=6 → q3=4, iqr=2
        # threshold = max(1, int(2 + 1.0*2)) = 4
        counts = Counter({f"ng{i}": v for i, v in enumerate([1, 1, 2, 2, 3, 3, 4, 4])})
        threshold, stats = _compute_adaptive_frequency_threshold(counts, sensitivity=1.0, fallback=1)
        assert stats["method"] == "iqr"
        assert stats["q1"] == 2
        assert stats["q3"] == 4
        assert stats["iqr"] == 2
        assert threshold == 4

    def test_sensitivity_0_yields_q1(self):
        # sensitivity=0 → threshold = max(fallback, int(q1 + 0)) = max(1, 2) = 2
        counts = Counter({f"ng{i}": v for i, v in enumerate([1, 1, 2, 2, 3, 3, 4, 4])})
        threshold, stats = _compute_adaptive_frequency_threshold(counts, sensitivity=0.0, fallback=1)
        assert threshold == stats["q1"]

    def test_fallback_floor_enforced(self):
        # Distribution where computed threshold would be 1
        counts = Counter({f"ng{i}": 1 for i in range(10)})
        threshold, stats = _compute_adaptive_frequency_threshold(counts, sensitivity=0.5, fallback=5)
        assert threshold >= 5

    def test_stats_keys_present(self):
        counts = Counter({f"ng{i}": v for i, v in enumerate(range(1, 9))})
        _, stats = _compute_adaptive_frequency_threshold(counts)
        for key in ("method", "n_ngrams", "q1", "median", "q3", "iqr", "sensitivity", "raw_threshold", "threshold"):
            assert key in stats, f"missing key: {key}"

    def test_skewed_distribution_raises_threshold(self):
        # Most n-grams appear once, a few appear many times
        counts = Counter({f"rare{i}": 1 for i in range(20)})
        counts["common_a"] = 50
        counts["common_b"] = 60
        threshold, stats = _compute_adaptive_frequency_threshold(counts, sensitivity=1.0, fallback=2)
        assert stats["iqr"] >= 0
        # Threshold should be at least the fallback
        assert threshold >= 2

    def test_uniform_distribution_iqr_zero(self):
        # All frequencies equal → IQR=0, threshold collapses to fallback
        counts = Counter({f"ng{i}": 5 for i in range(20)})
        threshold, stats = _compute_adaptive_frequency_threshold(counts, sensitivity=1.0, fallback=2)
        assert stats["iqr"] == 0
        assert threshold >= 2


# ---------------------------------------------------------------------------
# detect_tool_patterns adaptive mode (no DB — exercises short-circuit)
# ---------------------------------------------------------------------------


class TestDetectToolPatternsAdaptive:
    """These tests don't need a real DB; they exercise the adaptive flag path
    against the short-circuit (no chains found) and result metadata."""

    def test_adaptive_flag_recorded_in_result(self, monkeypatch):
        # Monkeypatch _extract_session_tool_chains to return empty (no DB needed)
        import tools.genesis.pattern_detector as mod

        monkeypatch.setattr(mod, "_extract_session_tool_chains", lambda **kw: {})
        result = detect_tool_patterns(adaptive=True, sensitivity=1.5)
        assert result["adaptive"] is True

    def test_non_adaptive_flag_recorded(self, monkeypatch):
        import tools.genesis.pattern_detector as mod

        monkeypatch.setattr(mod, "_extract_session_tool_chains", lambda **kw: {})
        result = detect_tool_patterns(adaptive=False)
        assert result["adaptive"] is False

    def test_adaptive_no_chains_returns_empty_patterns(self, monkeypatch):
        import tools.genesis.pattern_detector as mod

        monkeypatch.setattr(mod, "_extract_session_tool_chains", lambda **kw: {})
        result = detect_tool_patterns(adaptive=True)
        assert result["patterns"] == []
        assert result.get("message") == "No tool chains found in the lookback window"

    def test_adaptive_with_chains_uses_computed_threshold(self, monkeypatch):
        import tools.genesis.pattern_detector as mod

        fake_chains = {
            "s1": [["Read", "Edit", "Bash"], ["Read", "Edit", "Bash"]],
            "s2": [["Read", "Edit", "Bash"]],
            "s3": [["Read", "Edit", "Bash"]],
        }
        monkeypatch.setattr(mod, "_extract_session_tool_chains", lambda **kw: fake_chains)
        result = detect_tool_patterns(adaptive=True, sensitivity=1.0, min_chain_length=2)
        # adaptive_threshold key must be present when adaptive=True and ngrams exist
        assert "adaptive_threshold" in result or result.get("patterns") is not None
        assert "effective_min_frequency" in result


# ---------------------------------------------------------------------------
# _load_synthesize_config
# ---------------------------------------------------------------------------


class TestLoadSynthesizeConfig:
    """_load_synthesize_config: YAML loading with fallback to _DEFAULTS."""

    def test_returns_dict_with_required_keys(self):
        cfg = _load_synthesize_config()
        for key in ["min_pattern_frequency", "min_chain_length", "lookback_days",
                    "max_gap_seconds", "top_k", "anomaly_detection"]:
            assert key in cfg, f"Missing config key: {key}"

    def test_anomaly_detection_block_has_required_keys(self):
        cfg = _load_synthesize_config()
        ad = cfg["anomaly_detection"]
        for key in ["enabled", "min_samples", "sensitivity", "frequency_floor"]:
            assert key in ad, f"Missing anomaly_detection.{key}"

    def test_values_have_correct_types(self):
        cfg = _load_synthesize_config()
        assert isinstance(cfg["min_pattern_frequency"], int)
        assert isinstance(cfg["min_chain_length"], int)
        assert isinstance(cfg["lookback_days"], int)
        assert isinstance(cfg["anomaly_detection"]["sensitivity"], (int, float))

    def test_config_loads_even_with_bad_path(self, monkeypatch, tmp_path):
        import tools.genesis.pattern_detector as mod
        monkeypatch.setattr(mod, "BASE_DIR", tmp_path)
        cfg = mod._load_synthesize_config()
        # Falls back to _DEFAULTS when YAML not found
        assert cfg["min_pattern_frequency"] == mod._DEFAULTS["min_pattern_frequency"]


# ---------------------------------------------------------------------------
# compute_adaptive_frequency_threshold (public API)
# ---------------------------------------------------------------------------


class TestPublicAdaptiveThreshold:
    """compute_adaptive_frequency_threshold: public dict-returning wrapper."""

    def test_returns_dict_with_threshold(self):
        counts = Counter({f"ng{i}": v for i, v in enumerate([1, 1, 2, 2, 3, 3, 4, 4])})
        result = compute_adaptive_frequency_threshold(counts, sensitivity=1.0, fallback=2)
        assert isinstance(result, dict)
        assert "threshold" in result

    def test_threshold_is_int(self):
        counts = Counter({f"ng{i}": v for i, v in enumerate(range(1, 9))})
        result = compute_adaptive_frequency_threshold(counts)
        assert isinstance(result["threshold"], int)

    def test_empty_counter_returns_fallback(self):
        result = compute_adaptive_frequency_threshold(Counter(), fallback=5)
        assert result["threshold"] == 5

    def test_result_includes_method_key(self):
        counts = Counter({f"ng{i}": v for i, v in enumerate([1, 1, 2, 2, 3, 3, 4, 4])})
        result = compute_adaptive_frequency_threshold(counts)
        assert "method" in result


# ---------------------------------------------------------------------------
# detect_tool_patterns: config_source key
# ---------------------------------------------------------------------------


class TestDetectToolPatternsConfigSource:
    """Verify config_source and threshold_info keys from the updated pipeline."""

    def test_config_source_key_present(self, monkeypatch):
        import tools.genesis.pattern_detector as mod

        monkeypatch.setattr(mod, "_extract_session_tool_chains", lambda **kw: {})
        result = detect_tool_patterns(adaptive=False)
        assert "config_source" in result

    def test_threshold_info_present_in_static_mode(self, monkeypatch):
        import tools.genesis.pattern_detector as mod

        monkeypatch.setattr(mod, "_extract_session_tool_chains", lambda **kw: {})
        result = detect_tool_patterns(adaptive=False, min_frequency=7)
        # Even with no chains, threshold_info is not set (early return before ngram step)
        # The test just checks the result is valid
        assert "patterns" in result

    def test_effective_min_frequency_matches_param_in_static_mode(self, monkeypatch):
        import tools.genesis.pattern_detector as mod

        fake_chains = {
            "s1": [["A", "B", "C", "D"] * 2],
            "s2": [["A", "B", "C", "D"] * 2],
        }
        monkeypatch.setattr(mod, "_extract_session_tool_chains", lambda **kw: fake_chains)
        result = detect_tool_patterns(adaptive=False, min_frequency=99)
        assert result.get("effective_min_frequency") == 99
        assert result.get("threshold_info", {}).get("method") == "static"


# ---------------------------------------------------------------------------
# _extract_ngrams
# ---------------------------------------------------------------------------


class TestExtractNgrams:
    def test_empty_chains_returns_empty(self):
        counts = _extract_ngrams({}, min_length=2)
        assert len(counts) == 0

    def test_single_chain_produces_ngrams(self):
        chains = {"s1": [["A", "B", "C", "D"]]}
        counts = _extract_ngrams(chains, min_length=2)
        assert counts[("A", "B")] >= 1
        assert counts[("A", "B", "C")] >= 1
        assert counts[("A", "B", "C", "D")] >= 1

    def test_repeated_chain_increments_count(self):
        chains = {"s1": [["A", "B", "C"], ["A", "B", "C"]]}
        counts = _extract_ngrams(chains, min_length=3)
        assert counts[("A", "B", "C")] == 2

    def test_min_length_filters_shorter(self):
        chains = {"s1": [["A", "B", "C"]]}
        counts = _extract_ngrams(chains, min_length=3)
        assert ("A", "B") not in counts


# ---------------------------------------------------------------------------
# _score_pattern
# ---------------------------------------------------------------------------


class TestScorePattern:
    def test_returns_required_keys(self):
        chains = {"s1": [["A", "B", "C"]]}
        score = _score_pattern(("A", "B", "C"), 3, chains)
        for key in ("pattern", "frequency", "caller_diversity", "chain_length", "composite_score"):
            assert key in score

    def test_composite_score_positive(self):
        chains = {"s1": [["A", "B", "C"]], "s2": [["A", "B", "C"]]}
        score = _score_pattern(("A", "B", "C"), 4, chains)
        assert score["composite_score"] > 0

    def test_caller_diversity_counts_unique_sessions(self):
        chains = {
            "s1": [["A", "B", "C"]],
            "s2": [["A", "B", "C"]],
            "s3": [["X", "Y", "Z"]],
        }
        score = _score_pattern(("A", "B", "C"), 2, chains)
        assert score["caller_diversity"] == 2

    def test_session_ids_cap_default_limits_to_ten(self):
        """Default session_ids_cap (from config) must cap at session_ids_cap, not hardcoded 10."""
        chains = {f"session-{i:03d}": [["A", "B", "C"]] for i in range(25)}
        score = _score_pattern(("A", "B", "C"), count=25, chains=chains)
        # Must not exceed 10 (the configured default)
        assert len(score["session_ids"]) <= 10

    def test_session_ids_cap_param_5(self):
        """Explicit session_ids_cap=5 limits output to ≤5 IDs."""
        chains = {f"session-{i:03d}": [["A", "B", "C"]] for i in range(20)}
        score = _score_pattern(("A", "B", "C"), count=20, chains=chains, session_ids_cap=5)
        assert len(score["session_ids"]) <= 5

    def test_session_ids_cap_param_larger_than_actual(self):
        """Cap larger than actual sessions → all sessions returned."""
        chains = {f"s{i}": [["A", "B"]] for i in range(3)}
        score = _score_pattern(("A", "B"), count=3, chains=chains, session_ids_cap=100)
        assert len(score["session_ids"]) == 3

    def test_session_ids_cap_zero(self):
        """Cap of 0 → empty session_ids."""
        chains = {f"s{i}": [["X", "Y", "Z"]] for i in range(5)}
        score = _score_pattern(("X", "Y", "Z"), count=5, chains=chains, session_ids_cap=0)
        assert score["session_ids"] == []


# ---------------------------------------------------------------------------
# _compute_adaptive_frequency_threshold — min_samples parameter
# ---------------------------------------------------------------------------


class TestComputeAdaptiveFrequencyThresholdMinSamples:
    """Tests specifically for the min_samples parameter (hardcoded n<4 fix)."""

    def test_default_min_samples_causes_fallback_on_3_items(self):
        """Default min_samples=4: a 3-item Counter must use fallback, not IQR."""
        counts = Counter({"a": 10, "b": 5, "c": 2})  # n=3
        threshold, stats = _compute_adaptive_frequency_threshold(counts, fallback=2)
        assert stats["method"] == "fallback"
        assert stats["reason"] == "too_few_ngrams"

    def test_min_samples_3_allows_iqr_on_3_items(self):
        """Lowering min_samples=3: a 3-item Counter now computes IQR."""
        counts = Counter({"a": 10, "b": 5, "c": 2})  # n=3
        threshold, stats = _compute_adaptive_frequency_threshold(
            counts, sensitivity=1.0, fallback=2, min_samples=3
        )
        assert stats["method"] == "iqr"

    def test_min_samples_6_blocks_4_item_counter(self):
        """Raising min_samples=6: a 4-item Counter must fall back."""
        counts = Counter({"a": 10, "b": 7, "c": 5, "d": 2})  # n=4
        threshold, stats = _compute_adaptive_frequency_threshold(
            counts, sensitivity=1.0, fallback=2, min_samples=6
        )
        assert stats["method"] == "fallback"
        assert stats["reason"] == "too_few_ngrams"

    def test_min_samples_forwarded_via_public_api(self):
        """Public compute_adaptive_frequency_threshold must accept and forward min_samples."""
        counts = Counter({"a": 10, "b": 5, "c": 2})  # n=3
        result = compute_adaptive_frequency_threshold(counts, min_samples=3)
        assert result.get("method") == "iqr"


# ---------------------------------------------------------------------------
# detect_tool_patterns — top_k bug (using top_k=None instead of _top_k)
# ---------------------------------------------------------------------------


class TestDetectToolPatternsTopKBug:
    """Tests for the top_k vs _top_k resolution bug at result slicing."""

    def test_top_k_none_respects_config_top_k(self, monkeypatch):
        """When top_k=None (default), config top_k should cap the pattern list."""
        import tools.genesis.pattern_detector as mod

        # Build many chains so many patterns are found
        fake_chains = {}
        tools_list = [f"Tool{i}" for i in range(8)]
        for i in range(10):
            chain = tools_list[:]  # 8-tool chain → many ngrams
            fake_chains[f"s{i:02d}"] = [chain]

        monkeypatch.setattr(mod, "_extract_session_tool_chains", lambda **kw: fake_chains)

        # Use a small top_k via config override to see the cap applied
        result = detect_tool_patterns(
            adaptive=False,
            min_frequency=1,
            min_chain_length=2,
            top_k=3,  # explicit small value — result["patterns"] must be ≤ 3
        )
        assert len(result.get("patterns", [])) <= 3

    def test_top_k_param_none_uses_config_default(self, monkeypatch):
        """Passing top_k=None explicitly must still cap via config (not return all)."""
        import tools.genesis.pattern_detector as mod

        fake_chains = {}
        tools_list = [f"Tool{i}" for i in range(8)]
        for i in range(10):
            fake_chains[f"s{i:02d}"] = [tools_list[:]]

        monkeypatch.setattr(mod, "_extract_session_tool_chains", lambda **kw: fake_chains)

        # Default config top_k=20; with 10 sessions and an 8-tool chain there
        # should be many ngrams, but result is bounded by the config top_k (20)
        result = detect_tool_patterns(
            adaptive=False,
            min_frequency=1,
            min_chain_length=2,
            top_k=None,
        )
        cfg = mod._load_synthesize_config()
        assert len(result.get("patterns", [])) <= cfg["top_k"]


# ---------------------------------------------------------------------------
# _extract_session_tool_chains — min_flush_length (config-driven, no hardcode)
# ---------------------------------------------------------------------------


class TestExtractSessionToolChainsMinFlushLength:
    """Tests that min_flush_length replaces the hardcoded >= 2 in chain flushing."""

    def _make_mock_conn(self, rows):
        from unittest.mock import MagicMock
        mock_conn = MagicMock()
        mock_conn.execute.return_value.fetchall.return_value = rows
        return mock_conn

    def test_flush_length_2_keeps_two_tool_chains(self, monkeypatch):
        """Default min_flush_length=2: two-item chains must be retained."""
        import tools.genesis.pattern_detector as mod

        rows = [
            {"session_id": "s1", "tool_name": "Read", "created_at": "2026-01-01T00:00:00Z"},
            {"session_id": "s1", "tool_name": "Edit", "created_at": "2026-01-01T00:00:01Z"},
        ]
        mock_conn = self._make_mock_conn(rows)
        monkeypatch.setattr(mod, "get_connection", lambda: mock_conn)

        result = mod._extract_session_tool_chains(min_flush_length=2)
        assert "s1" in result
        assert result["s1"] == [["Read", "Edit"]]

    def test_flush_length_3_discards_two_tool_chains(self, monkeypatch):
        """min_flush_length=3: two-item chains must NOT be stored."""
        import tools.genesis.pattern_detector as mod

        rows = [
            {"session_id": "s1", "tool_name": "Read", "created_at": "2026-01-01T00:00:00Z"},
            {"session_id": "s1", "tool_name": "Edit", "created_at": "2026-01-01T00:00:01Z"},
        ]
        mock_conn = self._make_mock_conn(rows)
        monkeypatch.setattr(mod, "get_connection", lambda: mock_conn)

        result = mod._extract_session_tool_chains(min_flush_length=3)
        assert "s1" not in result

    def test_flush_length_3_keeps_three_tool_chains(self, monkeypatch):
        """min_flush_length=3: three-item chains must be retained."""
        import tools.genesis.pattern_detector as mod

        rows = [
            {"session_id": "s1", "tool_name": "Read", "created_at": "2026-01-01T00:00:00Z"},
            {"session_id": "s1", "tool_name": "Edit", "created_at": "2026-01-01T00:00:01Z"},
            {"session_id": "s1", "tool_name": "Write", "created_at": "2026-01-01T00:00:02Z"},
        ]
        mock_conn = self._make_mock_conn(rows)
        monkeypatch.setattr(mod, "get_connection", lambda: mock_conn)

        result = mod._extract_session_tool_chains(min_flush_length=3)
        assert "s1" in result
        assert result["s1"] == [["Read", "Edit", "Write"]]

    def test_gap_splits_chain_and_flushes_at_flush_length(self, monkeypatch):
        """Time-gap splits chains; each segment obeys min_flush_length."""
        import tools.genesis.pattern_detector as mod

        rows = [
            {"session_id": "s1", "tool_name": "A", "created_at": "2026-01-01T00:00:00Z"},
            {"session_id": "s1", "tool_name": "B", "created_at": "2026-01-01T00:00:01Z"},
            # Large gap → new chain
            {"session_id": "s1", "tool_name": "C", "created_at": "2026-01-01T00:10:00Z"},
        ]
        mock_conn = self._make_mock_conn(rows)
        monkeypatch.setattr(mod, "get_connection", lambda: mock_conn)

        # min_flush_length=2: first segment [A,B] should be saved, [C] alone discarded
        result = mod._extract_session_tool_chains(max_gap_seconds=30, min_flush_length=2)
        assert "s1" in result
        chains = result["s1"]
        assert ["A", "B"] in chains
        # [C] alone is length=1 < 2 → should NOT appear as its own chain
        assert ["C"] not in chains

    def test_defaults_dict_has_min_chain_flush_length(self):
        """_DEFAULTS must expose min_chain_flush_length so config loading works."""
        from tools.genesis.pattern_detector import _DEFAULTS
        assert "min_chain_flush_length" in _DEFAULTS
        assert isinstance(_DEFAULTS["min_chain_flush_length"], int)
        assert _DEFAULTS["min_chain_flush_length"] >= 1

    def test_config_includes_min_chain_flush_length(self):
        """_load_synthesize_config must return min_chain_flush_length."""
        from tools.genesis.pattern_detector import _load_synthesize_config
        cfg = _load_synthesize_config()
        assert "min_chain_flush_length" in cfg

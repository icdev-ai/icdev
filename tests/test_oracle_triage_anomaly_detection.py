# CUI // SP-CTI
"""oracle_triage anomaly-detection thresholds are STATIC config, not adaptive.

There used to be an "adaptive" path (aiify-5330) that recomputed the high/low
confidence bands as mean +/- 1 std over recent oracle_predictions.confidence
values. It was removed, because in the awareness lens `confidence` is not a
per-prediction estimate — it is a per-rule constant copied out of
awareness_config.yaml (gap::orphan_db_table is always 0.85, gap::route_no_e2e
always 0.70, ...). The mean of a bag of ~7 such constants, weighted by how often
each rule fired, is a popularity contest among unrelated rules, not a
distribution. Live it drove low_confidence_threshold from 0.30 to 0.825 purely
because high-confidence rules had fired a lot — setting gap::route_no_e2e's
evidence bar from the firing rate of gap::tool_not_in_manifest.

Crucially, removing it changed NO outcome (TestRemovalIsBehaviourPreserving):
the only consumer, `_verify_orphan_db_table`, only ever sees orphan_db_table
predictions at confidence 0.85, and 0.85 >= high_threshold(0.85) returns 0 in
both modes — the inflated low bar was never read.
"""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def reset_config(monkeypatch):
    """Reset the config cache before and after each test."""
    import tools.genesis.reflexes.oracle_triage as ot

    monkeypatch.setattr(ot, "_TRIAGE_CONFIG", None)
    yield
    monkeypatch.setattr(ot, "_TRIAGE_CONFIG", None)


@pytest.fixture
def ot():
    import tools.genesis.reflexes.oracle_triage as m

    return m


class TestAdaptivePathIsGone:
    """The removed symbols must stay removed — reintroducing the mean/std over
    constants would reintroduce the category error."""

    def test_compute_adaptive_thresholds_is_removed(self, ot):
        assert not hasattr(ot, "_compute_adaptive_thresholds")

    def test_fetch_confidence_history_is_removed(self, ot):
        assert not hasattr(ot, "_fetch_confidence_history")

    def test_adaptive_cache_global_is_removed(self, ot):
        assert not hasattr(ot, "_ADAPTIVE_THRESHOLDS_CACHE")


class TestGetActiveAnomalyThresholds:
    def test_returns_static_config(self, ot):
        thresholds = ot._get_active_anomaly_thresholds()
        assert thresholds["high_confidence_threshold"] == pytest.approx(0.85)
        assert thresholds["low_confidence_threshold"] == pytest.approx(0.3)
        assert thresholds["orphan_min_refs_low_confidence"] == 3

    def test_reads_config_overrides(self, ot, monkeypatch):
        monkeypatch.setattr(ot, "_TRIAGE_CONFIG", {
            "orphan_min_refs": 1,
            "anomaly_detection": {
                "high_confidence_threshold": 0.90,
                "low_confidence_threshold": 0.40,
                "orphan_min_refs_low_confidence": 5,
            },
        })
        thresholds = ot._get_active_anomaly_thresholds()
        assert thresholds["high_confidence_threshold"] == pytest.approx(0.90)
        assert thresholds["low_confidence_threshold"] == pytest.approx(0.40)

    def test_returns_a_copy_not_the_cached_dict(self, ot):
        """Callers must not be able to mutate the shared config through the
        returned dict."""
        ot._load_triage_config()  # prime the cache
        a = ot._get_active_anomaly_thresholds()
        a["high_confidence_threshold"] = 0.01
        b = ot._get_active_anomaly_thresholds()
        assert b["high_confidence_threshold"] == pytest.approx(0.85)


class TestGetOrphanMinRefs:
    """The static behaviour that survived the removal, unchanged."""

    def test_none_confidence_returns_base(self, ot):
        assert ot._get_orphan_min_refs(None) == 1

    def test_high_confidence_returns_zero(self, ot):
        assert ot._get_orphan_min_refs(0.9) == 0

    def test_exactly_at_high_threshold_returns_zero(self, ot):
        assert ot._get_orphan_min_refs(0.85) == 0

    def test_low_confidence_returns_elevated_refs(self, ot):
        assert ot._get_orphan_min_refs(0.1) == 3

    def test_just_below_low_threshold_returns_elevated_refs(self, ot):
        assert ot._get_orphan_min_refs(0.29) == 3

    def test_mid_confidence_returns_base(self, ot):
        assert ot._get_orphan_min_refs(0.5) == 1

    def test_thresholds_are_honored_from_config(self, ot, monkeypatch):
        monkeypatch.setattr(ot, "_TRIAGE_CONFIG", {
            "orphan_min_refs": 1,
            "anomaly_detection": {
                "high_confidence_threshold": 0.95,
                "low_confidence_threshold": 0.40,
                "orphan_min_refs_low_confidence": 5,
            },
        })
        # 0.85 now below the (config) high bar -> not bypassed, base refs
        assert ot._get_orphan_min_refs(0.85) == 1
        # 0.96 above it -> bypassed
        assert ot._get_orphan_min_refs(0.96) == 0
        # 0.35 below the low bar -> elevated refs from config
        assert ot._get_orphan_min_refs(0.35) == 5


class TestRemovalIsBehaviourPreserving:
    """The whole justification for deleting the adaptive path: it never changed
    an outcome, because its only reachable input is orphan_db_table @ 0.85."""

    def test_orphan_db_table_constant_clears_the_high_bar(self, ot):
        """gap::orphan_db_table's per-rule constant is 0.85, exactly the high
        threshold, so the refs check is skipped (return 0). Adaptive clamped
        high to min(0.85, mean+std) == 0.85 too, so this was 0 in both modes."""
        assert ot._get_orphan_min_refs(0.85) == 0

    def test_the_only_consumer_is_the_orphan_verifier(self, ot):
        """The only caller of _get_orphan_min_refs is _verify_orphan_db_table,
        whose dispatch is gated on lens == 'orphan_db_table'. So 0.85 is the only
        confidence that ever reaches this code, and the low branch — the one
        adaptive inflated to 0.825 — is dead for every prediction that gets here.
        """
        import inspect
        src = inspect.getsource(ot)
        callers = [
            line.strip() for line in src.splitlines()
            if "_get_orphan_min_refs(" in line and "def _get_orphan_min_refs" not in line
        ]
        assert callers, "expected at least one caller"
        assert "orphan_db_table" in src


class TestConfigLoading:
    def test_static_defaults_when_yaml_missing(self, ot, monkeypatch, tmp_path):
        monkeypatch.setattr(ot, "BASE_DIR", tmp_path)
        cfg = ot._load_triage_config()
        assert cfg["orphan_min_refs"] == 1
        assert cfg["anomaly_detection"]["high_confidence_threshold"] == pytest.approx(0.85)
        assert cfg["anomaly_detection"]["low_confidence_threshold"] == pytest.approx(0.3)
        assert cfg["anomaly_detection"]["orphan_min_refs_low_confidence"] == 3

    def test_defaults_no_longer_carry_adaptive_key(self, ot, monkeypatch, tmp_path):
        """min_history_for_adaptive was the adaptive path's only config knob."""
        monkeypatch.setattr(ot, "BASE_DIR", tmp_path)
        cfg = ot._load_triage_config()
        assert "min_history_for_adaptive" not in cfg["anomaly_detection"]

    def test_yaml_overrides_apply(self, ot, monkeypatch, tmp_path):
        try:
            import yaml
        except ImportError:
            pytest.skip("pyyaml not installed")

        args_dir = tmp_path / "args"
        args_dir.mkdir()
        (args_dir / "oracle_triage_config.yaml").write_text(
            yaml.dump({
                "orphan_min_refs": 2,
                "anomaly_detection": {
                    "high_confidence_threshold": 0.90,
                    "low_confidence_threshold": 0.20,
                    "orphan_min_refs_low_confidence": 4,
                },
            }),
            encoding="utf-8",
        )
        monkeypatch.setattr(ot, "BASE_DIR", tmp_path)
        cfg = ot._load_triage_config()
        assert cfg["orphan_min_refs"] == 2
        assert cfg["anomaly_detection"]["high_confidence_threshold"] == pytest.approx(0.90)
        assert cfg["anomaly_detection"]["low_confidence_threshold"] == pytest.approx(0.20)
        assert cfg["anomaly_detection"]["orphan_min_refs_low_confidence"] == 4

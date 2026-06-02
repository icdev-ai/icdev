# CUI // SP-CTI
"""Tests for Oracle Migration Lens anomaly detection.

Covers _MigrationAnomalyThresholds (IQR + config-driven fallbacks)
and MigrationLens.score() prediction generation.
"""
import json
import pytest

from tools.oracle.lenses.lens_migration import _MigrationAnomalyThresholds, MigrationLens


# ---------------------------------------------------------------------------
# _MigrationAnomalyThresholds — small-sample (fallback) path
# ---------------------------------------------------------------------------

class TestAnomalyThresholdsSmallSample:
    """Fewer than min_samples entries → use config fallback values."""

    def _make(self, cfg=None):
        return _MigrationAnomalyThresholds(
            scores=[55.0, 70.0],         # 2 samples < default min_samples=5
            readiness=[50.0, 80.0],
            source_counts=[2, 3],
            orphan_counts=[1, 2],
            cfg=cfg,
        )

    def test_score_below_fallback_is_anomaly(self):
        t = self._make()
        assert t.score_is_anomaly(55.0) is True

    def test_score_above_fallback_not_anomaly(self):
        t = self._make()
        assert t.score_is_anomaly(75.0) is False

    def test_readiness_below_fallback_is_anomaly(self):
        t = self._make()
        assert t.readiness_is_anomaly(45.0) is True

    def test_readiness_above_fallback_not_anomaly(self):
        t = self._make()
        assert t.readiness_is_anomaly(65.0) is False

    def test_source_count_at_fallback_is_anomaly(self):
        t = self._make()
        assert t.source_count_is_anomaly(5) is True

    def test_source_count_below_fallback_not_anomaly(self):
        t = self._make()
        assert t.source_count_is_anomaly(4) is False

    def test_orphan_count_at_fallback_is_anomaly(self):
        t = self._make()
        assert t.orphan_count_is_anomaly(3) is True

    def test_orphan_count_below_fallback_not_anomaly(self):
        t = self._make()
        assert t.orphan_count_is_anomaly(2) is False

    def test_custom_cfg_overrides_fallback_score(self):
        cfg = {"min_samples": 5, "fallback_score": 75.0, "fallback_readiness": 60.0,
               "fallback_source_count": 5.0, "fallback_orphan_count": 3.0}
        t = self._make(cfg=cfg)
        # 70.0 < 75.0 custom fallback → anomaly
        assert t.score_is_anomaly(70.0) is True

    def test_custom_cfg_min_samples_zero_activates_iqr(self):
        """min_samples=0 forces IQR path even with 2 samples."""
        cfg = {"min_samples": 0, "fallback_score": 60.0, "fallback_readiness": 60.0,
               "fallback_source_count": 5.0, "fallback_orphan_count": 3.0}
        t = self._make(cfg=cfg)
        # IQR of [55.0, 70.0]: Q1=55+0.25*(70-55)=58.75, Q3=66.25, fence=58.75-1.5*7.5=47.5
        # 55.0 > 47.5 → not anomalous under IQR
        assert t.score_is_anomaly(55.0) is False


# ---------------------------------------------------------------------------
# _MigrationAnomalyThresholds — large-sample (IQR) path
# ---------------------------------------------------------------------------

class TestAnomalyThresholdsIQR:
    """Sufficient samples → IQR governs, fallback is ignored."""

    def _portfolio(self):
        # 10 assessments clustered around 80-90
        scores = [80.0, 82.0, 85.0, 87.0, 88.0, 89.0, 90.0, 91.0, 92.0, 95.0]
        readiness = [70.0, 72.0, 75.0, 78.0, 80.0, 82.0, 85.0, 87.0, 90.0, 92.0]
        # source counts clustered around 2-4
        source_counts = [2, 2, 3, 3, 3, 4, 4, 4, 4, 5]
        orphan_counts = [0, 0, 0, 1, 1, 1, 1, 2, 2, 2]
        return _MigrationAnomalyThresholds(scores, readiness, source_counts, orphan_counts)

    def test_very_low_score_is_anomaly(self):
        t = self._portfolio()
        assert t.score_is_anomaly(40.0) is True

    def test_normal_score_not_anomaly(self):
        t = self._portfolio()
        assert t.score_is_anomaly(85.0) is False

    def test_very_high_source_count_is_anomaly(self):
        t = self._portfolio()
        # IQR fence for source counts: Q1=2.75, Q3=4, IQR=1.25, high=4+1.5*1.25=5.875
        # 10 >= 5.875 → anomaly
        assert t.source_count_is_anomaly(10) is True

    def test_normal_source_count_not_anomaly(self):
        t = self._portfolio()
        assert t.source_count_is_anomaly(3) is False


# ---------------------------------------------------------------------------
# MigrationLens.score() — prediction generation without a real DB
# ---------------------------------------------------------------------------

class TestMigrationLensScore:
    """score() generates correct prediction categories from mocked analysis data."""

    def _lens(self):
        lens = MigrationLens()
        # Inject a small config so thresholds are deterministic
        lens._test_cfg = {"min_samples": 5, "fallback_score": 60.0,
                          "fallback_readiness": 60.0, "fallback_source_count": 5.0,
                          "fallback_orphan_count": 3.0}
        return lens

    def _graph(self, sources=1, targets=1, controls=0, orphans=0):
        nodes = []
        edges = []
        nid = 0
        src_ids, tgt_ids, ctl_ids, connected = [], [], [], set()
        for _ in range(sources):
            nid += 1
            n = {"id": str(nid), "type": "src-legacy"}
            nodes.append(n)
            src_ids.append(str(nid))
        for _ in range(targets):
            nid += 1
            n = {"id": str(nid), "type": "tgt-cloud"}
            nodes.append(n)
            tgt_ids.append(str(nid))
        for _ in range(controls):
            nid += 1
            n = {"id": str(nid), "type": "ctl-ato-gate"}
            nodes.append(n)
            ctl_ids.append(str(nid))
        for _ in range(orphans):
            nid += 1
            nodes.append({"id": str(nid), "type": "unknown"})
        # Connect src→tgt
        for s, t in zip(src_ids, tgt_ids):
            edges.append({"source": s, "target": t})
            connected.update([s, t])
        for c in ctl_ids:
            edges.append({"source": src_ids[0], "target": c})
            connected.update([src_ids[0], c])
        return {"nodes": nodes, "edges": edges}

    def _analysis(self, designs, assessments=None):
        return {"designs": designs, "assessments": assessments or [], "waves": []}

    def test_no_controls_emits_compliance_gap(self):
        lens = self._lens()
        graph = self._graph(sources=2, targets=1, controls=0)
        design = {"id": "d1", "name": "Test Design", "graph_json": json.dumps(graph)}
        analysis = self._analysis([design])
        # Monkey-patch thresholds to use test config
        import tools.oracle.lenses.lens_migration as _mod
        orig = _mod._LENS_CFG
        _mod._LENS_CFG = lens._test_cfg
        try:
            preds = lens.score(analysis)
        finally:
            _mod._LENS_CFG = orig
        categories = {p.category for p in preds}
        assert "compliance_gap" in categories

    def test_low_score_emits_assessment_risk(self):
        lens = self._lens()
        graph = self._graph(sources=1, targets=1, controls=1)
        design = {"id": "d2", "name": "Low Score", "graph_json": json.dumps(graph)}
        assessments = [{"design_id": "d2", "score": 40.0, "grade": "F",
                        "cat1_findings": 2, "cat2_findings": 3, "readiness_score": 80.0}]
        analysis = self._analysis([design], assessments)
        import tools.oracle.lenses.lens_migration as _mod
        orig = _mod._LENS_CFG
        _mod._LENS_CFG = lens._test_cfg
        try:
            preds = lens.score(analysis)
        finally:
            _mod._LENS_CFG = orig
        categories = {p.category for p in preds}
        assert "assessment_risk" in categories

    def test_low_readiness_emits_readiness_gap(self):
        lens = self._lens()
        graph = self._graph(sources=1, targets=1, controls=1)
        design = {"id": "d3", "name": "Low Readiness", "graph_json": json.dumps(graph)}
        assessments = [{"design_id": "d3", "score": 80.0, "grade": "B",
                        "cat1_findings": 0, "cat2_findings": 1, "readiness_score": 30.0}]
        analysis = self._analysis([design], assessments)
        import tools.oracle.lenses.lens_migration as _mod
        orig = _mod._LENS_CFG
        _mod._LENS_CFG = lens._test_cfg
        try:
            preds = lens.score(analysis)
        finally:
            _mod._LENS_CFG = orig
        categories = {p.category for p in preds}
        assert "readiness_gap" in categories

    def test_unassessed_design_with_sources_emits_unassessed(self):
        lens = self._lens()
        graph = self._graph(sources=2, targets=1, controls=1)
        design = {"id": "d4", "name": "Unassessed", "graph_json": json.dumps(graph)}
        analysis = self._analysis([design], assessments=[])
        import tools.oracle.lenses.lens_migration as _mod
        orig = _mod._LENS_CFG
        _mod._LENS_CFG = lens._test_cfg
        try:
            preds = lens.score(analysis)
        finally:
            _mod._LENS_CFG = orig
        categories = {p.category for p in preds}
        assert "unassessed" in categories

    def test_high_orphan_count_emits_design_quality(self):
        lens = self._lens()
        graph = self._graph(sources=1, targets=1, controls=1, orphans=5)
        design = {"id": "d5", "name": "Orphaned", "graph_json": json.dumps(graph)}
        analysis = self._analysis([design])
        import tools.oracle.lenses.lens_migration as _mod
        orig = _mod._LENS_CFG
        _mod._LENS_CFG = lens._test_cfg
        try:
            preds = lens.score(analysis)
        finally:
            _mod._LENS_CFG = orig
        categories = {p.category for p in preds}
        assert "design_quality" in categories

    def test_empty_analysis_returns_no_predictions(self):
        lens = self._lens()
        preds = lens.score({"designs": [], "assessments": [], "waves": []})
        assert preds == []

    def test_design_with_no_nodes_skipped(self):
        lens = self._lens()
        design = {"id": "d6", "name": "Empty", "graph_json": json.dumps({"nodes": [], "edges": []})}
        analysis = self._analysis([design])
        preds = lens.score(analysis)
        assert preds == []

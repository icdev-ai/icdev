# CUI // SP-CTI
"""Unit tests for tools/oracle/lenses/lens_migration.py

Covers:
- _MigrationAnomalyThresholds: IQR percentile, low/high boundary, fallback, all predicate methods
- MigrationLens.score(): predictions generated from mock analysis data (no DB required)
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

BASE_DIR = Path(__file__).resolve().parents[1]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.oracle.lenses.lens_migration import (  # noqa: E402
    MigrationLens,
    _MigrationAnomalyThresholds,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _thresholds(
    scores=None,
    readiness=None,
    source_counts=None,
    orphan_counts=None,
    cfg=None,
):
    """Build a _MigrationAnomalyThresholds with sensible defaults."""
    return _MigrationAnomalyThresholds(
        scores=scores or [],
        readiness=readiness or [],
        source_counts=source_counts or [],
        orphan_counts=orphan_counts or [],
        cfg=cfg or {"min_samples": 5, "iqr_multiplier": 1.5,
                    "fallback_score": 60.0, "fallback_readiness": 60.0,
                    "fallback_source_count": 5.0, "fallback_orphan_count": 3.0},
    )


def _make_design(
    did: str = "d1",
    name: str = "TestDesign",
    nodes: list | None = None,
    edges: list | None = None,
) -> dict:
    """Return a minimal design dict with serialized graph_json."""
    return {
        "id": did,
        "name": name,
        "graph_json": json.dumps({"nodes": nodes or [], "edges": edges or []}),
        "updated_at": "2024-01-01T00:00:00",
    }


# ---------------------------------------------------------------------------
# _MigrationAnomalyThresholds — percentile
# ---------------------------------------------------------------------------

class TestPercentile:
    def test_empty_returns_zero(self):
        t = _thresholds()
        assert t._percentile([], 0.5) == 0.0

    def test_single_value(self):
        assert _thresholds()._percentile([42.0], 0.0) == 42.0
        assert _thresholds()._percentile([42.0], 1.0) == 42.0

    def test_two_values_median(self):
        result = _thresholds()._percentile([10.0, 20.0], 0.5)
        assert result == pytest.approx(15.0)

    def test_quartiles_five_values(self):
        # [70, 75, 80, 85, 90]: Q1=75, Q3=85
        vals = [70.0, 75.0, 80.0, 85.0, 90.0]
        t = _thresholds()
        assert t._percentile(vals, 0.25) == pytest.approx(75.0)
        assert t._percentile(vals, 0.75) == pytest.approx(85.0)

    def test_fractional_interpolation(self):
        # 10 values: idx at Q1 = 9*0.25 = 2.25
        vals = [10.0, 20.0, 30.0, 40.0, 50.0, 60.0, 70.0, 80.0, 90.0, 100.0]
        q1 = _thresholds()._percentile(vals, 0.25)
        # lo=2(30), hi=3(40), frac=0.25 → 30 + 10*0.25 = 32.5
        assert q1 == pytest.approx(32.5)


# ---------------------------------------------------------------------------
# _MigrationAnomalyThresholds — low_boundary / high_boundary
# ---------------------------------------------------------------------------

class TestBoundaries:
    """Tests for low_boundary (lower Tukey fence) and high_boundary (upper fence)."""

    # Five scores: [70,75,80,85,90] → Q1=75, Q3=85, IQR=10
    # Lower fence = 75 - 1.5*10 = 60;  Upper fence = 85 + 1.5*10 = 100
    FIVE_SCORES = [70.0, 75.0, 80.0, 85.0, 90.0]

    def test_low_boundary_with_enough_samples(self):
        t = _thresholds(scores=self.FIVE_SCORES)
        assert t._low_boundary(self.FIVE_SCORES, fallback=999.0) == pytest.approx(60.0)

    def test_high_boundary_with_enough_samples(self):
        t = _thresholds(source_counts=[1, 2, 3, 4, 5])
        # [1,2,3,4,5] → Q1=2, Q3=4, IQR=2 → upper fence = 4 + 1.5*2 = 7
        vals = [1.0, 2.0, 3.0, 4.0, 5.0]
        assert t._high_boundary(vals, fallback=999.0) == pytest.approx(7.0)

    def test_low_boundary_fallback_when_too_few(self):
        # 4 samples < min_samples=5 → fallback
        t = _thresholds(scores=[70.0, 75.0, 80.0, 85.0])
        assert t._low_boundary([70.0, 75.0, 80.0, 85.0], fallback=50.0) == pytest.approx(50.0)

    def test_high_boundary_fallback_when_too_few(self):
        t = _thresholds(source_counts=[1, 2, 3])
        assert t._high_boundary([1.0, 2.0, 3.0], fallback=7.0) == pytest.approx(7.0)

    def test_low_boundary_exactly_at_min_samples(self):
        # min_samples=5, exactly 5 values → uses IQR (not fallback)
        t = _thresholds(scores=self.FIVE_SCORES)
        bound = t._low_boundary(self.FIVE_SCORES, fallback=999.0)
        assert bound != pytest.approx(999.0)

    def test_custom_iqr_multiplier(self):
        # iqr_multiplier=3.0 (far outlier)
        cfg = {"min_samples": 5, "iqr_multiplier": 3.0,
               "fallback_score": 60.0, "fallback_readiness": 60.0,
               "fallback_source_count": 5.0, "fallback_orphan_count": 3.0}
        t = _MigrationAnomalyThresholds(
            scores=self.FIVE_SCORES, readiness=[], source_counts=[], orphan_counts=[], cfg=cfg
        )
        # Q1=75, Q3=85, IQR=10 → lower fence = 75 - 3*10 = 45
        assert t._low_boundary(self.FIVE_SCORES, 999.0) == pytest.approx(45.0)


# ---------------------------------------------------------------------------
# _MigrationAnomalyThresholds — predicate methods
# ---------------------------------------------------------------------------

class TestPredicates:
    SCORES = [70.0, 75.0, 80.0, 85.0, 90.0]  # lower fence = 60

    def test_score_is_anomaly_below_fence(self):
        t = _thresholds(scores=self.SCORES)
        assert t.score_is_anomaly(55.0) is True

    def test_score_is_anomaly_above_fence(self):
        t = _thresholds(scores=self.SCORES)
        assert t.score_is_anomaly(65.0) is False

    def test_score_is_anomaly_at_fence(self):
        t = _thresholds(scores=self.SCORES)
        assert t.score_is_anomaly(60.0) is False

    def test_score_uses_fallback_when_few_samples(self):
        # 3 samples → fallback_score=60 used
        t = _thresholds(scores=[70.0, 75.0, 80.0])
        assert t.score_is_anomaly(55.0) is True   # below fallback 60
        assert t.score_is_anomaly(65.0) is False  # above fallback 60

    def test_readiness_is_anomaly_below_fence(self):
        readiness = [60.0, 65.0, 70.0, 75.0, 80.0]  # Q1=65, Q3=75, IQR=10 → fence=50
        t = _thresholds(readiness=readiness)
        assert t.readiness_is_anomaly(45.0) is True

    def test_readiness_is_anomaly_above_fence(self):
        readiness = [60.0, 65.0, 70.0, 75.0, 80.0]
        t = _thresholds(readiness=readiness)
        assert t.readiness_is_anomaly(55.0) is False

    def test_readiness_fallback_when_few_samples(self):
        t = _thresholds(readiness=[70.0, 80.0])
        assert t.readiness_is_anomaly(55.0) is True   # below fallback_readiness=60

    def test_source_count_is_anomaly_above_fence(self):
        # [1,2,3,4,5] → upper fence=7
        t = _thresholds(source_counts=[1, 2, 3, 4, 5])
        assert t.source_count_is_anomaly(8) is True

    def test_source_count_is_anomaly_at_fence(self):
        t = _thresholds(source_counts=[1, 2, 3, 4, 5])
        assert t.source_count_is_anomaly(7) is True  # >= fence → anomaly

    def test_source_count_is_not_anomaly_below_fence(self):
        t = _thresholds(source_counts=[1, 2, 3, 4, 5])
        assert t.source_count_is_anomaly(6) is False

    def test_source_count_fallback_when_few_samples(self):
        # 2 samples → fallback_source_count=5
        t = _thresholds(source_counts=[1, 2])
        assert t.source_count_is_anomaly(5) is True   # >= 5
        assert t.source_count_is_anomaly(4) is False

    def test_orphan_count_is_anomaly_above_fence(self):
        # [0,0,1,1,2] → Q1=0.25, Q3=1.0, IQR=0.75 → upper fence = 1.0 + 1.5*0.75 = 2.125
        t = _thresholds(orphan_counts=[0, 0, 1, 1, 2])
        assert t.orphan_count_is_anomaly(3) is True

    def test_orphan_count_fallback_when_few_samples(self):
        # 2 samples → fallback_orphan_count=3
        t = _thresholds(orphan_counts=[1, 2])
        assert t.orphan_count_is_anomaly(3) is True   # >= 3
        assert t.orphan_count_is_anomaly(2) is False


# ---------------------------------------------------------------------------
# MigrationLens.score() — prediction generation from mock analysis
# ---------------------------------------------------------------------------

class TestMigrationLensScore:
    """Tests for MigrationLens.score() using pre-built analysis dicts.

    No DB connection is needed — score() operates purely on the analysis dict.
    """

    def _lens(self):
        return MigrationLens()

    def test_empty_analysis_returns_no_predictions(self):
        lens = self._lens()
        preds = lens.score({"designs": [], "assessments": [], "waves": []})
        assert preds == []

    def test_compliance_gap_when_sources_no_controls(self):
        nodes = [
            {"id": "n1", "type": "src-system"},
            {"id": "n2", "type": "tgt-system"},
        ]
        edges = [{"source": "n1", "target": "n2"}]
        analysis = {
            "designs": [_make_design(nodes=nodes, edges=edges)],
            "assessments": [],
            "waves": [],
        }
        preds = self._lens().score(analysis)
        categories = [p.category for p in preds]
        assert "compliance_gap" in categories
        gap = next(p for p in preds if p.category == "compliance_gap")
        assert gap.severity == "critical"
        assert gap.confidence > 0.9

    def test_no_compliance_gap_when_controls_present(self):
        nodes = [
            {"id": "n1", "type": "src-system"},
            {"id": "n2", "type": "tgt-system"},
            {"id": "n3", "type": "ctl-security-scan"},
        ]
        edges = [
            {"source": "n1", "target": "n2"},
            {"source": "n2", "target": "n3"},
        ]
        analysis = {
            "designs": [_make_design(nodes=nodes, edges=edges)],
            "assessments": [],
            "waves": [],
        }
        preds = self._lens().score(analysis)
        assert not any(p.category == "compliance_gap" for p in preds)

    def test_unassessed_warning_when_sources_and_no_assessment(self):
        nodes = [
            {"id": "n1", "type": "src-system"},
            {"id": "n2", "type": "tgt-system"},
        ]
        edges = [{"source": "n1", "target": "n2"}]
        analysis = {
            "designs": [_make_design(nodes=nodes, edges=edges)],
            "assessments": [],
            "waves": [],
        }
        preds = self._lens().score(analysis)
        assert any(p.category == "unassessed" for p in preds)

    def test_no_unassessed_when_assessment_present(self):
        nodes = [
            {"id": "n1", "type": "src-system"},
            {"id": "n2", "type": "tgt-system"},
            {"id": "n3", "type": "ctl-test-gate"},
        ]
        edges = [{"source": "n1", "target": "n2"}, {"source": "n2", "target": "n3"}]
        analysis = {
            "designs": [_make_design(did="d1", nodes=nodes, edges=edges)],
            "assessments": [
                {"design_id": "d1", "score": 88.0, "grade": "B",
                 "cat1_findings": 0, "cat2_findings": 1, "cat3_findings": 2,
                 "readiness_score": 85.0, "created_at": "2024-01-01"}
            ],
            "waves": [],
        }
        preds = self._lens().score(analysis)
        assert not any(p.category == "unassessed" for p in preds)

    def test_assessment_risk_triggered_for_low_score(self):
        # Provide 5+ assessments so IQR fires; one design has a very low score
        high_scores = [
            {"design_id": f"dx{i}", "score": float(80 + i * 2), "grade": "A",
             "cat1_findings": 0, "cat2_findings": 0, "cat3_findings": 0,
             "readiness_score": 90.0, "created_at": "2024-01-01"}
            for i in range(4)
        ]
        low_score_assess = {
            "design_id": "d1", "score": 20.0, "grade": "F",
            "cat1_findings": 3, "cat2_findings": 5, "cat3_findings": 2,
            "readiness_score": 90.0, "created_at": "2024-01-02"
        }
        nodes = [
            {"id": "n1", "type": "src-system"},
            {"id": "n2", "type": "tgt-system"},
            {"id": "n3", "type": "ctl-security-scan"},
        ]
        edges = [{"source": "n1", "target": "n2"}, {"source": "n2", "target": "n3"}]
        analysis = {
            "designs": [_make_design(did="d1", nodes=nodes, edges=edges)],
            "assessments": [low_score_assess] + high_scores,
            "waves": [],
        }
        preds = self._lens().score(analysis)
        assert any(p.category == "assessment_risk" for p in preds)

    def test_readiness_gap_triggered_for_low_readiness(self):
        high_readiness = [
            {"design_id": f"dx{i}", "score": 85.0, "grade": "A",
             "cat1_findings": 0, "cat2_findings": 0, "cat3_findings": 0,
             "readiness_score": float(80 + i * 2), "created_at": "2024-01-01"}
            for i in range(4)
        ]
        low_readiness_assess = {
            "design_id": "d1", "score": 85.0, "grade": "A",
            "cat1_findings": 0, "cat2_findings": 0, "cat3_findings": 0,
            "readiness_score": 10.0, "created_at": "2024-01-02"
        }
        nodes = [
            {"id": "n1", "type": "src-system"},
            {"id": "n2", "type": "tgt-system"},
            {"id": "n3", "type": "ctl-security-scan"},
        ]
        edges = [{"source": "n1", "target": "n2"}, {"source": "n2", "target": "n3"}]
        analysis = {
            "designs": [_make_design(did="d1", nodes=nodes, edges=edges)],
            "assessments": [low_readiness_assess] + high_readiness,
            "waves": [],
        }
        preds = self._lens().score(analysis)
        assert any(p.category == "readiness_gap" for p in preds)

    def test_planning_gap_for_large_migration_without_waves(self):
        # Build a design with enough sources to be anomalous (> IQR upper fence)
        # Use 5 designs with 1 source each → fallback_source_count = 5 triggers
        many_sources = [{"id": f"src-{i}", "type": "src-system"} for i in range(6)]
        tgt = [{"id": "tgt-0", "type": "tgt-system"}]
        ctl = [{"id": "ctl-0", "type": "ctl-security-scan"}]
        nodes = many_sources + tgt + ctl
        edges = [{"source": "src-0", "target": "tgt-0"}, {"source": "tgt-0", "target": "ctl-0"}]
        analysis = {
            "designs": [_make_design(nodes=nodes, edges=edges)],
            "assessments": [],
            "waves": [],
        }
        preds = self._lens().score(analysis)
        assert any(p.category == "planning_gap" for p in preds)

    def test_no_planning_gap_when_wave_groups_present(self):
        many_sources = [{"id": f"src-{i}", "type": "src-system"} for i in range(6)]
        wave = {"id": "wave-1", "type": "wave-group"}
        tgt = {"id": "tgt-0", "type": "tgt-system"}
        ctl = {"id": "ctl-0", "type": "ctl-security-scan"}
        nodes = many_sources + [wave, tgt, ctl]
        edges = [{"source": "src-0", "target": "tgt-0"}, {"source": "tgt-0", "target": "ctl-0"},
                 {"source": "wave-1", "target": "src-0"}]
        analysis = {
            "designs": [_make_design(nodes=nodes, edges=edges)],
            "assessments": [],
            "waves": [],
        }
        preds = self._lens().score(analysis)
        assert not any(p.category == "planning_gap" for p in preds)

    def test_design_quality_for_orphan_nodes(self):
        # 1 connected src+tgt pair + 4 orphans (>= fallback_orphan_count=3)
        nodes = [
            {"id": "n1", "type": "src-system"},
            {"id": "n2", "type": "tgt-system"},
            {"id": "n3", "type": "ctl-security-scan"},
            {"id": "orphan-1", "type": "other"},
            {"id": "orphan-2", "type": "other"},
            {"id": "orphan-3", "type": "other"},
        ]
        edges = [
            {"source": "n1", "target": "n2"},
            {"source": "n2", "target": "n3"},
        ]
        analysis = {
            "designs": [_make_design(nodes=nodes, edges=edges)],
            "assessments": [],
            "waves": [],
        }
        preds = self._lens().score(analysis)
        assert any(p.category == "design_quality" for p in preds)
        dq = next(p for p in preds if p.category == "design_quality")
        assert dq.severity == "info"

    def test_no_design_quality_when_few_orphans(self):
        nodes = [
            {"id": "n1", "type": "src-system"},
            {"id": "n2", "type": "tgt-system"},
            {"id": "orphan-1", "type": "other"},   # only 1 orphan < fallback=3
        ]
        edges = [{"source": "n1", "target": "n2"}]
        analysis = {
            "designs": [_make_design(nodes=nodes, edges=edges)],
            "assessments": [],
            "waves": [],
        }
        preds = self._lens().score(analysis)
        assert not any(p.category == "design_quality" for p in preds)

    def test_designs_with_no_nodes_are_skipped(self):
        analysis = {
            "designs": [_make_design(nodes=[], edges=[])],
            "assessments": [],
            "waves": [],
        }
        preds = self._lens().score(analysis)
        assert preds == []

    def test_invalid_graph_json_is_skipped_gracefully(self):
        design = {"id": "d1", "name": "Bad", "graph_json": "not-json", "updated_at": "2024-01-01"}
        analysis = {"designs": [design], "assessments": [], "waves": []}
        preds = self._lens().score(analysis)
        # Empty graph → no nodes → skip. Should not raise.
        assert preds == []

    def test_predictions_have_required_fields(self):
        nodes = [
            {"id": "n1", "type": "src-system"},
            {"id": "n2", "type": "tgt-system"},
        ]
        edges = [{"source": "n1", "target": "n2"}]
        analysis = {
            "designs": [_make_design(nodes=nodes, edges=edges)],
            "assessments": [],
            "waves": [],
        }
        preds = self._lens().score(analysis)
        assert len(preds) > 0
        for p in preds:
            assert p.lens == "migration"
            assert p.title
            assert p.description
            assert 0.0 <= p.confidence <= 1.0
            assert p.severity in ("critical", "warning", "info")
            assert p.category
            assert isinstance(p.data, dict)


# ---------------------------------------------------------------------------
# MigrationLens.propose() — enrichment with recommendations
# ---------------------------------------------------------------------------

class TestMigrationLensPropose:
    def test_propose_adds_recommendations_to_all_predictions(self):
        nodes = [{"id": "n1", "type": "src-system"}, {"id": "n2", "type": "tgt-system"}]
        edges = [{"source": "n1", "target": "n2"}]
        lens = MigrationLens()
        analysis = {"designs": [_make_design(nodes=nodes, edges=edges)],
                    "assessments": [], "waves": []}
        preds = lens.score(analysis)
        enriched = lens.propose(preds)
        for p in enriched:
            assert p.recommendations, f"no recommendations for category={p.category}"
            assert len(p.recommendations) >= 1

    def test_propose_known_category(self):
        nodes = [{"id": "n1", "type": "src-system"}, {"id": "n2", "type": "tgt-system"}]
        edges = [{"source": "n1", "target": "n2"}]
        lens = MigrationLens()
        analysis = {"designs": [_make_design(nodes=nodes, edges=edges)],
                    "assessments": [], "waves": []}
        preds = lens.score(analysis)
        enriched = lens.propose(preds)
        gap = next((p for p in enriched if p.category == "compliance_gap"), None)
        if gap:
            assert any("ATO" in r or "ctl-" in r or "Rollback" in r for r in gap.recommendations)

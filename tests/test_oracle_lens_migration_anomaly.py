# CUI // SP-CTI
"""Tests for oracle migration lens anomaly detection (opportunity aiify-rm-1d233-phase-5200).

Covers _MigrationAnomalyThresholds IQR logic and MigrationLens.score() predictions
without requiring a live DB connection.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.oracle.lenses.lens_migration import (
    MigrationLens,
    _MigrationAnomalyThresholds,
)


# ---------------------------------------------------------------------------
# _MigrationAnomalyThresholds — IQR logic
# ---------------------------------------------------------------------------

class TestMigrationAnomalyThresholds:
    """Validate IQR-based anomaly detection for score/readiness/counts."""

    def _thresholds(self, scores, readiness, sources, orphans, cfg=None):
        return _MigrationAnomalyThresholds(
            scores=scores,
            readiness=readiness,
            source_counts=sources,
            orphan_counts=orphans,
            cfg=cfg or {"min_samples": 5, "iqr_multiplier": 1.5,
                        "fallback_score": 60.0, "fallback_readiness": 60.0,
                        "fallback_source_count": 5.0, "fallback_orphan_count": 3.0},
        )

    def test_fallback_used_when_sample_too_small(self):
        t = self._thresholds([50], [50], [2], [1])
        # Only 1 sample — falls back to config baselines
        assert t.score_is_anomaly(59.9)
        assert not t.score_is_anomaly(60.0)
        assert t.readiness_is_anomaly(59.9)
        assert not t.readiness_is_anomaly(60.0)
        assert t.source_count_is_anomaly(5)
        assert not t.source_count_is_anomaly(4)
        assert t.orphan_count_is_anomaly(3)
        assert not t.orphan_count_is_anomaly(2)

    def test_iqr_low_boundary_flags_outlier(self):
        # Scores tightly clustered around 80; a score of 20 is an outlier
        scores = [75, 78, 80, 82, 85, 79, 81]
        t = self._thresholds(scores, scores, [2, 3, 3, 4, 4, 3, 3], [0, 0, 1, 0, 0, 0, 0])
        assert t.score_is_anomaly(20)

    def test_iqr_low_boundary_does_not_flag_normal(self):
        scores = [75, 78, 80, 82, 85, 79, 81]
        t = self._thresholds(scores, scores, [2, 3, 3, 4, 4, 3, 3], [0, 0, 1, 0, 0, 0, 0])
        assert not t.score_is_anomaly(78)

    def test_iqr_high_boundary_flags_outlier(self):
        # Source counts mostly 2–4; a design with 50 sources is an outlier
        sources = [2, 3, 3, 4, 4, 3, 2]
        t = self._thresholds([80] * 7, [80] * 7, sources, [0] * 7)
        assert t.source_count_is_anomaly(50)

    def test_iqr_high_boundary_does_not_flag_normal(self):
        sources = [2, 3, 3, 4, 4, 3, 2]
        t = self._thresholds([80] * 7, [80] * 7, sources, [0] * 7)
        assert not t.source_count_is_anomaly(3)

    def test_percentile_single_element(self):
        # Edge: single-element list for percentile (no IndexError)
        t = self._thresholds([70], [70], [3], [1])
        # sample < min_samples(5) — fallback applies, no exception
        assert isinstance(t.score_is_anomaly(65.0), bool)

    def test_iqr_multiplier_respected(self):
        # scores = [70,71,72,73,73,74,75]; Q1=71.5, Q3=73.5, IQR=2.0
        # Standard fence (1.5): Q1 - 1.5*2 = 68.5  → 67 < 68.5 → anomaly
        # Far fence     (3.0): Q1 - 3.0*2 = 65.5  → 67 > 65.5 → NOT anomaly
        scores = [70, 72, 73, 74, 75, 71, 73]
        cfg_standard = {"min_samples": 5, "iqr_multiplier": 1.5,
                        "fallback_score": 60.0, "fallback_readiness": 60.0,
                        "fallback_source_count": 5.0, "fallback_orphan_count": 3.0}
        cfg_far = {**cfg_standard, "iqr_multiplier": 3.0}
        t_std = _MigrationAnomalyThresholds(scores, scores, [2]*7, [0]*7, cfg=cfg_standard)
        t_far = _MigrationAnomalyThresholds(scores, scores, [2]*7, [0]*7, cfg=cfg_far)
        borderline = 67.0
        assert t_std.score_is_anomaly(borderline)
        assert not t_far.score_is_anomaly(borderline)


# ---------------------------------------------------------------------------
# MigrationLens.score() — prediction generation
# ---------------------------------------------------------------------------

def _make_design(did, name, graph_json_dict):
    return {"id": did, "name": name, "graph_json": json.dumps(graph_json_dict)}


def _make_assessment(did, score, readiness, grade="B", cat1=0, cat2=0):
    return {"design_id": did, "score": score, "grade": grade,
            "cat1_findings": cat1, "cat2_findings": cat2,
            "readiness_score": readiness, "created_at": "2026-01-01"}


def _run_score(designs, assessments, waves=None):
    lens = MigrationLens()
    analysis = {"designs": designs, "assessments": assessments or [], "waves": waves or []}
    return lens.score(analysis)


class TestMigrationLensScore:
    def test_no_designs_returns_empty(self):
        preds = _run_score([], [])
        assert preds == []

    def test_compliance_gap_flagged_when_no_controls(self):
        design = _make_design("d1", "TestDesign", {
            "nodes": [
                {"id": "s1", "type": "src-server"},
                {"id": "t1", "type": "tgt-cloud"},
            ],
            "edges": [{"source": "s1", "target": "t1"}],
        })
        preds = _run_score([design], [])
        cats = [p.category for p in preds]
        assert "compliance_gap" in cats

    def test_no_compliance_gap_when_controls_present(self):
        design = _make_design("d1", "TestDesign", {
            "nodes": [
                {"id": "s1", "type": "src-server"},
                {"id": "c1", "type": "ctl-ato-gate"},
            ],
            "edges": [],
        })
        preds = _run_score([design], [])
        cats = [p.category for p in preds]
        assert "compliance_gap" not in cats

    def test_unassessed_design_flagged(self):
        design = _make_design("d1", "Unassessed", {
            "nodes": [{"id": "s1", "type": "src-server"}],
            "edges": [],
        })
        preds = _run_score([design], [])
        cats = [p.category for p in preds]
        assert "unassessed" in cats

    def test_low_score_anomaly_flagged_via_fallback(self):
        # 1 design, 1 assessment — sample < min_samples(5), fallback=60
        design = _make_design("d1", "LowScoreDesign", {
            "nodes": [{"id": "s1", "type": "src-server"},
                      {"id": "c1", "type": "ctl-ato-gate"}],
            "edges": [],
        })
        assess = _make_assessment("d1", 45, 70)
        preds = _run_score([design], [assess])
        cats = [p.category for p in preds]
        assert "assessment_risk" in cats

    def test_normal_score_not_flagged_via_fallback(self):
        design = _make_design("d1", "GoodDesign", {
            "nodes": [{"id": "s1", "type": "src-server"},
                      {"id": "c1", "type": "ctl-ato-gate"}],
            "edges": [],
        })
        assess = _make_assessment("d1", 85, 85)
        preds = _run_score([design], [assess])
        cats = [p.category for p in preds]
        assert "assessment_risk" not in cats

    def test_low_readiness_anomaly_flagged_via_fallback(self):
        design = _make_design("d1", "LowReadiness", {
            "nodes": [{"id": "s1", "type": "src-server"},
                      {"id": "c1", "type": "ctl-ato-gate"}],
            "edges": [],
        })
        assess = _make_assessment("d1", 75, 40)
        preds = _run_score([design], [assess])
        cats = [p.category for p in preds]
        assert "readiness_gap" in cats

    def test_planning_gap_flagged_for_large_design_without_waves(self):
        # fallback_source_count=5 → >= 5 sources + no wave-group nodes
        nodes = [{"id": f"s{i}", "type": "src-server"} for i in range(6)]
        design = _make_design("d1", "BigMigration", {"nodes": nodes, "edges": []})
        preds = _run_score([design], [])
        cats = [p.category for p in preds]
        assert "planning_gap" in cats

    def test_planning_gap_not_flagged_when_wave_groups_present(self):
        nodes = [{"id": f"s{i}", "type": "src-server"} for i in range(6)]
        nodes.append({"id": "wg1", "type": "wave-group"})
        design = _make_design("d1", "WavedMigration", {"nodes": nodes, "edges": []})
        preds = _run_score([design], [])
        cats = [p.category for p in preds]
        assert "planning_gap" not in cats

    def test_design_quality_flagged_for_high_orphan_count(self):
        # fallback_orphan_count=3 → >= 3 disconnected nodes
        nodes = [{"id": f"n{i}", "type": "unknown"} for i in range(4)]
        design = _make_design("d1", "Messy", {"nodes": nodes, "edges": []})
        preds = _run_score([design], [])
        cats = [p.category for p in preds]
        assert "design_quality" in cats

    def test_design_quality_not_flagged_when_nodes_connected(self):
        nodes = [{"id": "a", "type": "src-server"}, {"id": "b", "type": "tgt-cloud"},
                 {"id": "c", "type": "ctl-ato-gate"}, {"id": "d", "type": "ctl-rollback"}]
        edges = [{"source": "a", "target": "b"}, {"source": "b", "target": "c"},
                 {"source": "c", "target": "d"}]
        design = _make_design("d1", "Tidy", {"nodes": nodes, "edges": edges})
        preds = _run_score([design], [])
        cats = [p.category for p in preds]
        assert "design_quality" not in cats

    def test_empty_graph_json_skipped_gracefully(self):
        design = {"id": "d1", "name": "Empty", "graph_json": "{}"}
        preds = _run_score([design], [])
        assert preds == []

    def test_invalid_graph_json_skipped_gracefully(self):
        design = {"id": "d1", "name": "Bad", "graph_json": "{not valid json"}
        preds = _run_score([design], [])
        assert preds == []

    def test_prediction_fields_complete(self):
        design = _make_design("d1", "TestDesign", {
            "nodes": [{"id": "s1", "type": "src-server"}],
            "edges": [],
        })
        preds = _run_score([design], [])
        assert len(preds) > 0
        for p in preds:
            assert p.lens == "migration"
            assert p.title
            assert p.description
            assert 0.0 < p.confidence <= 1.0
            assert p.severity in ("critical", "warning", "info")
            assert p.category


# ---------------------------------------------------------------------------
# MigrationLens.propose() — recommendations
# ---------------------------------------------------------------------------

class TestMigrationLensPropose:
    def test_recommendations_attached_for_all_categories(self):
        from tools.oracle.prediction import OraclePrediction

        lens = MigrationLens()
        categories = [
            "compliance_gap", "assessment_risk", "readiness_gap",
            "unassessed", "planning_gap", "design_quality",
        ]
        preds = []
        for cat in categories:
            p = OraclePrediction(
                lens="migration", title=f"t_{cat}", description="d",
                confidence=0.8, severity="warning", category=cat, data={},
            )
            preds.append(p)

        enriched = lens.propose(preds)
        for p in enriched:
            assert p.recommendations, f"No recommendations for category '{p.category}'"
            assert len(p.recommendations) >= 1

    def test_unknown_category_gets_default_recommendation(self):
        from tools.oracle.prediction import OraclePrediction

        lens = MigrationLens()
        p = OraclePrediction(
            lens="migration", title="t", description="d",
            confidence=0.5, severity="info", category="__unknown__", data={},
        )
        enriched = lens.propose([p])
        assert enriched[0].recommendations  # not empty

# CUI // SP-CTI
"""Oracle Migration Lens — Anticipatory intelligence for migration designs.

Analyzes migration designs to predict risks, readiness gaps, and
optimization opportunities before they become critical.

Three-phase pipeline: analyze → score → propose
Threshold configuration: args/migration_intelligence_config.yaml (oracle_lens section)
"""

from __future__ import annotations
from tools.logging.icdev_logger import get_logger

import json
from pathlib import Path
from typing import Any

import yaml

from tools.oracle.base_lens import BaseLens, OraclePrediction

logger = get_logger("icdev.oracle.migration")

_ICDEV_ROOT = Path(__file__).resolve().parents[3]
_CONFIG_PATH = _ICDEV_ROOT / "args" / "migration_intelligence_config.yaml"


def _load_oracle_lens_config() -> dict[str, Any]:
    """Load oracle_lens section from args/migration_intelligence_config.yaml."""
    try:
        with open(_CONFIG_PATH, encoding="utf-8") as fh:
            cfg = yaml.safe_load(fh) or {}
        return cfg.get("oracle_lens", {})
    except Exception as exc:
        logger.warning("migration_intelligence_config.yaml unreadable, using defaults: %s", exc)
        return {}


_LENS_CFG = _load_oracle_lens_config()


class _MigrationAnomalyThresholds:
    """IQR-based anomaly thresholds derived from observed migration portfolio data.

    Replaces hardcoded cutoffs with statistical outlier detection: values outside
    Q1 - k*IQR (low) or Q3 + k*IQR (high) are flagged as anomalies.
    Falls back to config-driven baselines (args/migration_intelligence_config.yaml
    oracle_lens section) when the sample is too small for reliable IQR.
    """

    def __init__(
        self,
        scores: list[float],
        readiness: list[float],
        source_counts: list[int],
        orphan_counts: list[int],
        cfg: dict[str, Any] | None = None,
    ) -> None:
        _cfg = cfg if cfg is not None else _LENS_CFG
        self._min_samples: int = int(_cfg.get("min_samples", 5))
        self._iqr_multiplier: float = float(_cfg.get("iqr_multiplier", 1.5))
        self._fallback_score: float = float(_cfg.get("fallback_score", 60.0))
        self._fallback_readiness: float = float(_cfg.get("fallback_readiness", 60.0))
        self._fallback_source_count: float = float(_cfg.get("fallback_source_count", 5.0))
        self._fallback_orphan_count: float = float(_cfg.get("fallback_orphan_count", 3.0))

        self._scores = scores
        self._readiness = readiness
        self._source_counts = [float(x) for x in source_counts]
        self._orphan_counts = [float(x) for x in orphan_counts]

    @staticmethod
    def _percentile(values: list[float], p: float) -> float:
        n = len(values)
        if n == 0:
            return 0.0
        sv = sorted(values)
        idx = (n - 1) * p
        lo = int(idx)
        hi = min(lo + 1, n - 1)
        return sv[lo] + (sv[hi] - sv[lo]) * (idx - lo)

    def _low_boundary(self, values: list[float], fallback: float) -> float:
        """Lower fence: Q1 - k*IQR; values below this are anomalously low."""
        if len(values) < self._min_samples:
            return fallback
        q1 = self._percentile(values, 0.25)
        q3 = self._percentile(values, 0.75)
        return q1 - self._iqr_multiplier * (q3 - q1)

    def _high_boundary(self, values: list[float], fallback: float) -> float:
        """Upper fence: Q3 + k*IQR; values at/above this are anomalously high."""
        if len(values) < self._min_samples:
            return fallback
        q1 = self._percentile(values, 0.25)
        q3 = self._percentile(values, 0.75)
        return q3 + self._iqr_multiplier * (q3 - q1)

    def score_is_anomaly(self, score: float) -> bool:
        return score < self._low_boundary(self._scores, self._fallback_score)

    def readiness_is_anomaly(self, readiness: float) -> bool:
        return readiness < self._low_boundary(self._readiness, self._fallback_readiness)

    def source_count_is_anomaly(self, count: int) -> bool:
        return count >= self._high_boundary(self._source_counts, self._fallback_source_count)

    def orphan_count_is_anomaly(self, count: int) -> bool:
        return count >= self._high_boundary(self._orphan_counts, self._fallback_orphan_count)


class MigrationLens(BaseLens):
    """Predict migration risks and readiness gaps."""

    name = "migration"
    description = "Anticipates migration risks, wave sequencing issues, and compliance gaps"

    def _get_db(self):
        """Get migration canvas DB connection."""
        try:
            from tools.migration_canvas.db.init_db import get_connection
            return get_connection()
        except Exception:
            return None

    def analyze(self) -> dict[str, Any]:
        """Phase 1: Gather data from migration designs and assessments."""
        conn = self._get_db()
        if not conn:
            return {"designs": [], "assessments": [], "waves": []}

        designs_limit = int(_LENS_CFG.get("designs_limit", 20))
        assessments_limit = int(_LENS_CFG.get("assessments_limit", 50))

        try:
            designs = [dict(r) for r in conn.execute(
                "SELECT id, name, graph_json, updated_at FROM migration_designs "
                f"ORDER BY updated_at DESC LIMIT {designs_limit}"
            ).fetchall()]

            assessments = [dict(r) for r in conn.execute(
                "SELECT design_id, score, grade, cat1_findings, cat2_findings, cat3_findings, "
                f"readiness_score, created_at FROM mc_assessments ORDER BY created_at DESC LIMIT {assessments_limit}"
            ).fetchall()]

            waves = [dict(r) for r in conn.execute(
                "SELECT design_id, wave_number, status, risk_score FROM mc_wave_plans ORDER BY design_id, wave_number"
            ).fetchall()]

            return {"designs": designs, "assessments": assessments, "waves": waves}
        except Exception as exc:
            logger.warning("Migration lens analyze failed: %s", exc)
            return {"designs": [], "assessments": [], "waves": []}
        finally:
            conn.close()

    def score(self, analysis: dict[str, Any]) -> list[OraclePrediction]:
        """Phase 2: Score analysis into predictions using anomaly detection.

        Two-pass approach:
          Pass 1 — parse all design graphs and collect portfolio-wide metrics.
          Pass 2 — generate predictions using IQR anomaly thresholds derived
                   from the full portfolio; falls back to config baselines
                   when the sample is too small (min_samples in oracle_lens config).
        """
        predictions = []
        designs = analysis.get("designs", [])
        assessments = analysis.get("assessments", [])

        confidence_cfg = _LENS_CFG.get("confidence", {})

        def _conf(category: str, default: float) -> float:
            return float(confidence_cfg.get(category, default))

        # Build assessment index: design_id -> latest assessment
        latest_assess: dict[str, dict] = {}
        for a in assessments:
            did = a.get("design_id", "")
            if did not in latest_assess:
                latest_assess[did] = a

        # --- Pass 1: extract per-design metrics for anomaly threshold computation ---
        design_metrics: list[dict[str, Any]] = []
        for design in designs:
            try:
                graph = json.loads(design.get("graph_json", "{}"))
            except (json.JSONDecodeError, TypeError):
                graph = {"nodes": [], "edges": []}

            nodes = graph.get("nodes", [])
            edges = graph.get("edges", [])

            if not nodes:
                continue

            sources = [n for n in nodes if n.get("type", "").startswith("src-")]
            targets = [n for n in nodes if n.get("type", "").startswith("tgt-")]
            controls = [n for n in nodes if n.get("type", "").startswith("ctl-")]
            connected: set[str] = set()
            for e in edges:
                connected.add(e.get("source", ""))
                connected.add(e.get("target", ""))
            orphans = [n for n in nodes if n.get("id") not in connected]

            design_metrics.append({
                "design": design,
                "nodes": nodes,
                "edges": edges,
                "sources": sources,
                "targets": targets,
                "controls": controls,
                "orphans": orphans,
            })

        # Build IQR anomaly thresholds from the full portfolio
        assess_scores = [a["score"] for a in assessments if a.get("score") is not None]
        assess_readiness = [a["readiness_score"] for a in assessments if a.get("readiness_score") is not None]
        all_source_counts = [len(m["sources"]) for m in design_metrics]
        all_orphan_counts = [len(m["orphans"]) for m in design_metrics]

        thresholds = _MigrationAnomalyThresholds(
            scores=assess_scores,
            readiness=assess_readiness,
            source_counts=all_source_counts,
            orphan_counts=all_orphan_counts,
        )

        # --- Pass 2: generate predictions using dynamic thresholds ---
        for m in design_metrics:
            design = m["design"]
            did = design.get("id", "")
            name = design.get("name", "Unknown")
            sources = m["sources"]
            targets = m["targets"]
            controls = m["controls"]
            nodes = m["nodes"]
            orphans = m["orphans"]

            # Check: designs with sources but no controls
            if sources and not controls:
                predictions.append(OraclePrediction(
                    lens=self.name,
                    title=f"No compliance controls in '{name}'",
                    description=f"Design '{name}' has {len(sources)} source systems but no compliance gates, "
                                "rollback points, or security scans.",
                    confidence=_conf("compliance_gap", 0.92),
                    severity="critical",
                    category="compliance_gap",
                    data={"design_id": did, "sources": len(sources)},
                ))

            assess = latest_assess.get(did)
            if assess:
                score_val = assess.get("score")
                if score_val is not None and thresholds.score_is_anomaly(score_val):
                    predictions.append(OraclePrediction(
                        lens=self.name,
                        title=f"Low assessment score for '{name}'",
                        description=f"Design '{name}' scored {score_val} ({assess.get('grade', '?')}). "
                                    f"CAT1: {assess.get('cat1_findings', 0)}, "
                                    f"CAT2: {assess.get('cat2_findings', 0)}.",
                        confidence=_conf("assessment_risk", 0.88),
                        severity="warning",
                        category="assessment_risk",
                        data={"design_id": did, "score": score_val, "grade": assess.get("grade", "?")},
                    ))

                readiness_val = assess.get("readiness_score")
                if readiness_val is not None and thresholds.readiness_is_anomaly(readiness_val):
                    predictions.append(OraclePrediction(
                        lens=self.name,
                        title=f"Low readiness for '{name}'",
                        description=f"Migration readiness at {readiness_val}% — "
                                    "significant gaps in completeness, compliance, or risk controls.",
                        confidence=_conf("readiness_gap", 0.85),
                        severity="warning",
                        category="readiness_gap",
                        data={"design_id": did, "readiness": readiness_val},
                    ))
            elif sources:
                predictions.append(OraclePrediction(
                    lens=self.name,
                    title=f"Unassessed migration design: '{name}'",
                    description=f"Design '{name}' has {len(sources)} sources and {len(targets)} targets "
                                "but has never been assessed. Risks are unknown.",
                    confidence=_conf("unassessed", 0.80),
                    severity="warning",
                    category="unassessed",
                    data={"design_id": did},
                ))

            # Check: anomalously large migration without wave groups
            if thresholds.source_count_is_anomaly(len(sources)):
                waves_in_design = [n for n in nodes if n.get("type") == "wave-group"]
                if not waves_in_design:
                    predictions.append(OraclePrediction(
                        lens=self.name,
                        title=f"Large migration without wave planning: '{name}'",
                        description=f"Design '{name}' has {len(sources)} source systems but no wave groups. "
                                    "Migrations of this size typically need phased execution.",
                        confidence=_conf("planning_gap", 0.82),
                        severity="warning",
                        category="planning_gap",
                        data={"design_id": did, "source_count": len(sources)},
                    ))

            # Check: anomalously high orphan node count
            if thresholds.orphan_count_is_anomaly(len(orphans)):
                predictions.append(OraclePrediction(
                    lens=self.name,
                    title=f"{len(orphans)} disconnected nodes in '{name}'",
                    description=f"Design '{name}' has {len(orphans)} nodes not connected to the migration flow. "
                                "These may be forgotten components or incomplete mappings.",
                    confidence=_conf("design_quality", 0.75),
                    severity="info",
                    category="design_quality",
                    data={"design_id": did, "orphan_count": len(orphans)},
                ))

        return predictions

    def propose(self, predictions: list[OraclePrediction]) -> list[OraclePrediction]:
        """Phase 3: Enrich predictions with recommendations."""
        recommendations_map = {
            "compliance_gap": [
                "Add ATO Compliance Gate (ctl-ato-gate) for GovCloud targets",
                "Add Security Scan (ctl-security-scan) before migration patterns",
                "Add Test Gate (ctl-test-gate) after migration targets",
                "Add Rollback Point (ctl-rollback) for safe reversal",
            ],
            "assessment_risk": [
                "Address all CAT1 findings immediately (blocking)",
                "Review CAT2 findings and create remediation plan",
                "Re-run assessment after fixes to verify score improvement",
            ],
            "readiness_gap": [
                "Map all source systems to migration patterns",
                "Add compliance bridge for NIST control inheritance",
                "Add wave groups to phase the migration",
                "Run Oracle anticipation analysis to identify remaining gaps",
            ],
            "unassessed": [
                "Run compliance assessment (Assess button in canvas editor)",
                "Review findings and address before proceeding to execution",
            ],
            "planning_gap": [
                "Create wave groups (Wave 1, Wave 2, ...) and assign sources",
                "Add milestones for phase gates between waves",
                "Add compliance gate between each wave",
            ],
            "design_quality": [
                "Connect orphan nodes to the migration flow or remove them",
                "Review design for completeness — ensure all paths lead to targets",
            ],
        }

        for pred in predictions:
            recs = recommendations_map.get(pred.category, ["Review and address this finding."])
            pred.recommendations = recs

        return predictions

    def persist(self, predictions: list[OraclePrediction]) -> int:
        """Save predictions to mc_oracle_predictions table."""
        conn = self._get_db()
        if not conn:
            return 0
        try:
            count = 0
            for pred in predictions:
                conn.execute(
                    "INSERT INTO mc_oracle_predictions "
                    "(id, design_id, lens_id, title, description, confidence, severity, "
                    "category, recommendations, data_json, created_at) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (
                        pred.id,
                        pred.data.get("design_id", ""),
                        pred.lens,
                        pred.title,
                        pred.description,
                        pred.confidence,
                        pred.severity,
                        pred.category,
                        json.dumps(pred.recommendations),
                        json.dumps(pred.data),
                        pred.created_at,
                    ),
                )
                count += 1
            conn.commit()
            return count
        except Exception as exc:
            logger.warning("Failed to persist migration predictions: %s", exc)
            return 0
        finally:
            conn.close()

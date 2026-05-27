# CUI // SP-CTI
"""Oracle Migration Lens — Anticipatory intelligence for migration designs.

Analyzes migration designs to predict risks, readiness gaps, and
optimization opportunities before they become critical.

Three-phase pipeline: analyze → score → propose
"""

from __future__ import annotations
from tools.logging.icdev_logger import get_logger

import json
from pathlib import Path
from typing import Any

from tools.oracle.base_lens import BaseLens, OraclePrediction

logger = get_logger("icdev.oracle.migration")

_ICDEV_ROOT = Path(__file__).resolve().parents[3]


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

        try:
            designs = [dict(r) for r in conn.execute(
                "SELECT id, name, graph_json, updated_at FROM migration_designs ORDER BY updated_at DESC LIMIT 20"
            ).fetchall()]

            assessments = [dict(r) for r in conn.execute(
                "SELECT design_id, score, grade, cat1_findings, cat2_findings, cat3_findings, "
                "readiness_score, created_at FROM mc_assessments ORDER BY created_at DESC LIMIT 50"
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
        """Phase 2: Score analysis into predictions."""
        predictions = []
        designs = analysis.get("designs", [])
        assessments = analysis.get("assessments", [])

        # Build assessment index: design_id -> latest assessment
        latest_assess = {}
        for a in assessments:
            did = a.get("design_id", "")
            if did not in latest_assess:
                latest_assess[did] = a

        for design in designs:
            did = design.get("id", "")
            name = design.get("name", "Unknown")

            # Parse graph for structural analysis
            try:
                graph = json.loads(design.get("graph_json", "{}"))
            except (json.JSONDecodeError, TypeError):
                graph = {"nodes": [], "edges": []}

            nodes = graph.get("nodes", [])
            edges = graph.get("edges", [])

            if not nodes:
                continue

            sources = [n for n in nodes if (n.get("type", "").startswith("src-"))]
            targets = [n for n in nodes if (n.get("type", "").startswith("tgt-"))]
            controls = [n for n in nodes if (n.get("type", "").startswith("ctl-"))]

            # Check: designs with sources but no controls
            if sources and not controls:
                predictions.append(OraclePrediction(
                    lens=self.name,
                    title=f"No compliance controls in '{name}'",
                    description=f"Design '{name}' has {len(sources)} source systems but no compliance gates, "
                                "rollback points, or security scans.",
                    confidence=0.92,
                    severity="critical",
                    category="compliance_gap",
                    data={"design_id": did, "sources": len(sources)},
                ))

            # Check: stale assessment (design updated after last assessment)
            assess = latest_assess.get(did)
            if assess:
                if assess.get("score", 100) < 60:
                    predictions.append(OraclePrediction(
                        lens=self.name,
                        title=f"Low assessment score for '{name}'",
                        description=f"Design '{name}' scored {assess['score']} ({assess['grade']}). "
                                    f"CAT1: {assess.get('cat1_findings', 0)}, "
                                    f"CAT2: {assess.get('cat2_findings', 0)}.",
                        confidence=0.88,
                        severity="warning",
                        category="assessment_risk",
                        data={"design_id": did, "score": assess["score"], "grade": assess["grade"]},
                    ))

                if assess.get("readiness_score", 100) < 60:
                    predictions.append(OraclePrediction(
                        lens=self.name,
                        title=f"Low readiness for '{name}'",
                        description=f"Migration readiness at {assess['readiness_score']}% — "
                                    "significant gaps in completeness, compliance, or risk controls.",
                        confidence=0.85,
                        severity="warning",
                        category="readiness_gap",
                        data={"design_id": did, "readiness": assess["readiness_score"]},
                    ))
            elif sources:
                # Design exists but never assessed
                predictions.append(OraclePrediction(
                    lens=self.name,
                    title=f"Unassessed migration design: '{name}'",
                    description=f"Design '{name}' has {len(sources)} sources and {len(targets)} targets "
                                "but has never been assessed. Risks are unknown.",
                    confidence=0.80,
                    severity="warning",
                    category="unassessed",
                    data={"design_id": did},
                ))

            # Check: large migration without waves
            if len(sources) >= 5:
                waves_in_design = [n for n in nodes if n.get("type") == "wave-group"]
                if not waves_in_design:
                    predictions.append(OraclePrediction(
                        lens=self.name,
                        title=f"Large migration without wave planning: '{name}'",
                        description=f"Design '{name}' has {len(sources)} source systems but no wave groups. "
                                    "Migrations of this size typically need phased execution.",
                        confidence=0.82,
                        severity="warning",
                        category="planning_gap",
                        data={"design_id": did, "source_count": len(sources)},
                    ))

            # Check: orphan nodes (potential forgotten components)
            connected = set()
            for e in edges:
                connected.add(e.get("source", ""))
                connected.add(e.get("target", ""))
            orphans = [n for n in nodes if n.get("id") not in connected]
            if len(orphans) >= 3:
                predictions.append(OraclePrediction(
                    lens=self.name,
                    title=f"{len(orphans)} disconnected nodes in '{name}'",
                    description=f"Design '{name}' has {len(orphans)} nodes not connected to the migration flow. "
                                "These may be forgotten components or incomplete mappings.",
                    confidence=0.75,
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
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
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

# CUI // SP-CTI
"""Oracle Quality Lens — Predicts quality regressions and improvement opportunities.

Analyzes QDC gate execution history, UQS trends, and Genesis quality snapshots
to anticipate quality issues before they manifest as CI failures or security incidents.

Predictions feed into:
  - Genesis Quality Reflex (auto-remediation)
  - QDC Dashboard (trend alerts)
  - Innovation Engine (quality improvement signals)

Scanner-tier only (zero Claude tokens).
"""

from __future__ import annotations
from tools.logging.icdev_logger import get_logger

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from tools.db.storage import get_connection

from tools.oracle.base_lens import BaseLens, OraclePrediction

logger = get_logger(__name__)

ICDEV_ROOT = Path(__file__).resolve().parents[3]
QDC_DB = ICDEV_ROOT / "data" / "qdc_canvas.db"
GENESIS_QDB = ICDEV_ROOT / "data" / "genesis_quality.db"


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


class QualityLens(BaseLens):
    """Predicts quality trends, regressions, and improvement opportunities."""

    name = "quality"
    description = "Anticipates quality regressions from QDC gate history and UQS trends"

    def analyze(self) -> dict[str, Any]:
        """Gather quality data from QDC and Genesis quality DBs."""
        data: dict[str, Any] = {
            "uqs_history": [],
            "gate_history": [],
            "quality_snapshots": [],
            "recent_trends": [],
        }

        # UQS history from QDC canvas DB
        if QDC_DB.exists():
            try:
                conn = get_connection(str(QDC_DB))
                rows = conn.execute(
                    "SELECT uqs_score, dimension_scores, computed_at "
                    "FROM qdc_uqs_history ORDER BY computed_at DESC LIMIT 20"
                ).fetchall()
                data["uqs_history"] = [dict(r) for r in rows]

                gate_rows = conn.execute(
                    "SELECT gate_id, sa11_control, status, executed_at "
                    "FROM qdc_gate_results ORDER BY executed_at DESC LIMIT 50"
                ).fetchall()
                data["gate_history"] = [dict(r) for r in gate_rows]
                conn.close()
            except Exception as e:
                logger.warning("Failed to read QDC DB: %s", e)

        # Genesis quality snapshots
        if GENESIS_QDB.exists():
            try:
                conn = get_connection(str(GENESIS_QDB))
                rows = conn.execute(
                    "SELECT uqs_score, grade, total_findings, auto_fixed, snapshot_at "
                    "FROM quality_snapshots ORDER BY snapshot_at DESC LIMIT 10"
                ).fetchall()
                data["quality_snapshots"] = [dict(r) for r in rows]

                trends = conn.execute(
                    "SELECT trend_type, dimension, direction, severity, detail, detected_at "
                    "FROM quality_trends WHERE resolved = 0 ORDER BY detected_at DESC LIMIT 10"
                ).fetchall()
                data["recent_trends"] = [dict(r) for r in trends]
                conn.close()
            except Exception as e:
                logger.warning("Failed to read Genesis quality DB: %s", e)

        return data

    def score(self, analysis: dict[str, Any]) -> list[OraclePrediction]:
        """Score quality data into predictions."""
        predictions: list[OraclePrediction] = []

        # Predict UQS trajectory
        uqs_history = analysis.get("uqs_history", [])
        if len(uqs_history) >= 3:
            scores = [h.get("uqs_score", 0) for h in uqs_history[:5]]
            avg_recent = sum(scores[:3]) / 3
            avg_older = sum(scores[3:]) / len(scores[3:]) if len(scores) > 3 else avg_recent

            if avg_recent < avg_older - 5:
                predictions.append(
                    OraclePrediction(
                        lens=self.name,
                        title="UQS Declining Trend",
                        description=f"UQS has dropped from {avg_older:.1f} to {avg_recent:.1f} over recent assessments",
                        confidence=min(0.95, 0.5 + abs(avg_older - avg_recent) / 20),
                        severity="warning" if avg_recent >= 70 else "critical",
                        category="quality_regression",
                        data={
                            "avg_recent": avg_recent,
                            "avg_older": avg_older,
                            "delta": round(avg_recent - avg_older, 1),
                        },
                    )
                )
            elif avg_recent > avg_older + 5:
                predictions.append(
                    OraclePrediction(
                        lens=self.name,
                        title="UQS Improving Trend",
                        description=f"UQS has improved from {avg_older:.1f} to {avg_recent:.1f}",
                        confidence=min(0.95, 0.5 + abs(avg_recent - avg_older) / 20),
                        severity="info",
                        category="quality_improvement",
                        data={
                            "avg_recent": avg_recent,
                            "avg_older": avg_older,
                            "delta": round(avg_recent - avg_older, 1),
                        },
                    )
                )

        # Predict gate failure patterns
        gate_history = analysis.get("gate_history", [])
        gate_failures: dict[str, int] = {}
        for g in gate_history:
            if g.get("status") == "fail":
                gid = g.get("gate_id", "unknown")
                gate_failures[gid] = gate_failures.get(gid, 0) + 1

        for gate_id, count in gate_failures.items():
            if count >= 3:
                predictions.append(
                    OraclePrediction(
                        lens=self.name,
                        title=f"Recurring Gate Failure: {gate_id}",
                        description=f"Gate '{gate_id}' has failed {count} times in recent history",
                        confidence=min(0.90, 0.4 + count * 0.1),
                        severity="warning" if count < 5 else "critical",
                        category="gate_failure_pattern",
                        data={"gate_id": gate_id, "failure_count": count},
                    )
                )

        # Predict findings growth
        snapshots = analysis.get("quality_snapshots", [])
        if len(snapshots) >= 2:
            current_findings = snapshots[0].get("total_findings", 0)
            prev_findings = snapshots[1].get("total_findings", 0)
            if current_findings > prev_findings * 1.5 and current_findings > 10:
                predictions.append(
                    OraclePrediction(
                        lens=self.name,
                        title="Findings Growth Spike",
                        description=f"Quality findings jumped from {prev_findings} to {current_findings} (+{current_findings - prev_findings})",  # noqa: E501
                        confidence=0.75,
                        severity="warning",
                        category="findings_spike",
                        data={"current": current_findings, "previous": prev_findings},
                    )
                )

        # Flag unresolved trends
        for trend in analysis.get("recent_trends", []):
            if trend.get("severity") == "critical":
                predictions.append(
                    OraclePrediction(
                        lens=self.name,
                        title=f"Unresolved Critical Trend: {trend.get('trend_type')}",
                        description=trend.get("detail", ""),
                        confidence=0.85,
                        severity="critical",
                        category="unresolved_trend",
                        data=trend,
                    )
                )

        return predictions

    def propose(self, predictions: list[OraclePrediction]) -> list[OraclePrediction]:
        """Enrich predictions with actionable recommendations."""
        for pred in predictions:
            if pred.category == "quality_regression":
                pred.recommendations = [
                    "Run full gate scan: python tools/qdc_canvas/gate_executor.py --all --json",
                    "Review worst-scoring gates and prioritize fixes",
                    "Check if recent code changes introduced regressions",
                    "Consider tightening gate thresholds after remediation",
                ]
            elif pred.category == "gate_failure_pattern":
                gate_id = pred.data.get("gate_id", "")
                pred.recommendations = [
                    f"Execute gate: python tools/qdc_canvas/gate_executor.py --gate {gate_id} --json",
                    f"Review {gate_id} findings and apply auto-fix if available",
                    "Check if the underlying tool needs configuration updates",
                    "Consider adding this gate to the Genesis auto-remediation list",
                ]
            elif pred.category == "findings_spike":
                pred.recommendations = [
                    "Run auto-remediation: python tools/genesis/reflexes/quality.py --remediate --json",
                    "Review recent commits for quality-impacting changes",
                    "Consider reverting problematic changes if spike is severe",
                ]
            elif pred.category == "quality_improvement":
                pred.recommendations = [
                    "Document what drove the improvement for knowledge base",
                    "Consider raising gate thresholds to lock in gains",
                    "Share improvement patterns with child apps via genome",
                ]

        return predictions


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    lens = QualityLens()
    predictions = lens.run()
    print(json.dumps([p.to_dict() for p in predictions], indent=2, default=str))
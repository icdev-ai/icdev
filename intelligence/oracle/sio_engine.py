# CUI // SP-CTI
"""SIO Engine — Strategic Intelligence Operations orchestrator.

Runs all 4 analytical lenses in sequence and produces a composite
OracleAssessment:

  1. threat_posture    (weight 0.30)
  2. behavior_pattern  (weight 0.25)
  3. intent_assessment (weight 0.25)
  4. convergence       (weight 0.20)

composite_score = weighted average of the four lens scores (0-10).

Usage:
    from intelligence.oracle.sio_engine import SIOEngine, get_latest_assessment
    result = SIOEngine().run_all()

CLI:
    python -m intelligence.oracle.sio_engine --run-all --json
"""

from __future__ import annotations

import json
import logging
import sys
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger("icdev.intelligence.oracle.sio_engine")

# ── Lens weights ──────────────────────────────────────────────────────────────
_LENS_WEIGHTS: dict[str, float] = {
    "threat_posture":   0.30,
    "behavior_pattern": 0.25,
    "intent_assessment": 0.25,
    "convergence":       0.20,
}


# ── OracleAssessment ──────────────────────────────────────────────────────────

@dataclass
class OracleAssessment:
    """Composite result from all SIO lenses."""
    lenses: dict[str, dict[str, Any]] = field(default_factory=dict)
    composite_score: float = 0.0
    iw_triggered: bool = False
    top_narrative: str = ""
    timestamp: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2)


# ── DB helper ─────────────────────────────────────────────────────────────────

def _get_conn():
    from tools.db.storage import get_connection
    return get_connection()


# ── Engine ────────────────────────────────────────────────────────────────────

class SIOEngine:
    """Orchestrates all four SIO lenses and returns a composite OracleAssessment."""

    def run_all(self) -> OracleAssessment:
        """Run all lenses in dependency order and return composite assessment."""
        results: dict[str, dict[str, Any]] = {}

        # 1. Threat Posture
        try:
            from intelligence.oracle.lenses.threat_posture import run as tp_run
            results["threat_posture"] = tp_run()
            logger.info(
                "threat_posture: score=%.2f confidence=%.3f",
                results["threat_posture"]["score"],
                results["threat_posture"]["confidence"],
            )
        except Exception as exc:
            logger.warning("threat_posture lens failed: %s", exc)
            results["threat_posture"] = _fallback_result("threat_posture", exc)

        # 2. Behavior Pattern
        try:
            from intelligence.oracle.lenses.behavior_pattern import run as bp_run
            results["behavior_pattern"] = bp_run()
            logger.info(
                "behavior_pattern: score=%.2f confidence=%.3f",
                results["behavior_pattern"]["score"],
                results["behavior_pattern"]["confidence"],
            )
        except Exception as exc:
            logger.warning("behavior_pattern lens failed: %s", exc)
            results["behavior_pattern"] = _fallback_result("behavior_pattern", exc)

        # 3. Intent Assessment
        try:
            from intelligence.oracle.lenses.intent_assessment import run as ia_run
            results["intent_assessment"] = ia_run()
            logger.info(
                "intent_assessment: score=%.2f confidence=%.3f",
                results["intent_assessment"]["score"],
                results["intent_assessment"]["confidence"],
            )
        except Exception as exc:
            logger.warning("intent_assessment lens failed: %s", exc)
            results["intent_assessment"] = _fallback_result("intent_assessment", exc)

        # 4. Convergence (depends on the 3 above having written to DB)
        try:
            from intelligence.oracle.lenses.convergence import run as cv_run
            results["convergence"] = cv_run()
            logger.info(
                "convergence: score=%.2f iw=%s",
                results["convergence"]["score"],
                results["convergence"].get("iw_triggered", False),
            )
        except Exception as exc:
            logger.warning("convergence lens failed: %s", exc)
            results["convergence"] = _fallback_result("convergence", exc)

        # ── Composite score ──────────────────────────────────────────────────
        composite_score = _weighted_composite(results)

        # ── I&W triggered? ───────────────────────────────────────────────────
        iw_triggered = bool(results.get("convergence", {}).get("iw_triggered", False))

        # ── Top narrative: highest-confidence non-fallback lens ──────────────
        top_narrative = _select_top_narrative(results)

        timestamp = datetime.now(timezone.utc).isoformat()

        return OracleAssessment(
            lenses=results,
            composite_score=round(composite_score, 2),
            iw_triggered=iw_triggered,
            top_narrative=top_narrative,
            timestamp=timestamp,
        )


# ── Helpers ───────────────────────────────────────────────────────────────────

def _fallback_result(lens_name: str, exc: Exception) -> dict[str, Any]:
    """Return a neutral placeholder when a lens fails."""
    return {
        "score": 0.0,
        "confidence": 0.0,
        "nato_reliability": "F6",
        "narrative": f"{lens_name} lens unavailable: {exc}",
        "error": str(exc),
    }


def _weighted_composite(results: dict[str, dict[str, Any]]) -> float:
    """Compute weighted average composite score."""
    total_weight = 0.0
    weighted_sum = 0.0
    for lens, weight in _LENS_WEIGHTS.items():
        score = float(results.get(lens, {}).get("score", 0.0))
        weighted_sum += score * weight
        total_weight += weight
    if total_weight == 0.0:
        return 0.0
    return weighted_sum / total_weight


def _select_top_narrative(results: dict[str, dict[str, Any]]) -> str:
    """Pick the narrative from the lens with highest confidence (excluding fallbacks)."""
    best_conf = -1.0
    best_narrative = "No assessment available."
    for lens_result in results.values():
        if "error" in lens_result:
            continue
        conf = float(lens_result.get("confidence", 0.0))
        if conf > best_conf:
            best_conf = conf
            best_narrative = lens_result.get("narrative", "")
    return best_narrative


# ── get_latest_assessment ─────────────────────────────────────────────────────

def get_latest_assessment() -> dict[str, Any]:
    """Read the last row per lens from sg_sio_assessments and return summary.

    Returns
    -------
    dict with keys:
        lenses         — dict of lens_source → {score, nato_reliability, narrative}
        composite_score — float
        iw_triggered   — bool
        timestamp      — str
    """
    conn = _get_conn()
    lens_data: dict[str, dict[str, Any]] = {}
    try:
        for lens in _LENS_WEIGHTS:
            try:
                row = conn.execute(
                    "SELECT score, nato_reliability, narrative, confidence, created_at "
                    "FROM sg_sio_assessments "
                    "WHERE lens_source = ? "
                    "ORDER BY created_at DESC LIMIT 1",
                    (lens,),
                ).fetchone()
                if row:
                    lens_data[lens] = {
                        "score": float(row[0]) if row[0] is not None else 0.0,
                        "nato_reliability": row[1] or "F6",
                        "narrative": row[2] or "",
                        "confidence": float(row[3]) if row[3] is not None else 0.0,
                        "created_at": row[4] or "",
                    }
            except Exception as exc:
                logger.debug("Could not read lens %s: %s", lens, exc)
    finally:
        conn.close()

    composite = _weighted_composite(
        {k: {"score": v["score"]} for k, v in lens_data.items()}
    )
    iw_triggered = bool(
        lens_data.get("convergence", {}).get("score", 0.0) > 7.5
        and composite > 6.0
    )
    timestamp = datetime.now(timezone.utc).isoformat()

    return {
        "lenses": lens_data,
        "composite_score": round(composite, 2),
        "iw_triggered": iw_triggered,
        "timestamp": timestamp,
    }


# ── MCP handler ──────────────────────────────────────────────────────────────

def run_all_json(pmesii_vector: list[float] | None = None) -> dict[str, Any]:
    """MCP-callable wrapper — runs all lenses and returns assessment as dict."""
    engine = SIOEngine()
    assessment = engine.run_all()
    return assessment.to_dict()


# ── CLI entrypoint ────────────────────────────────────────────────────────────

def _main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description="SIO Engine — Oracle assessment runner")
    parser.add_argument("--run-all", action="store_true", help="Run all lenses")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s %(name)s %(message)s",
        stream=sys.stderr,
    )

    if args.run_all:
        engine = SIOEngine()
        assessment = engine.run_all()
        if args.json:
            print(assessment.to_json())
        else:
            print("\n── Oracle Assessment ──────────────────────────────────────────")
            print(f"Composite Score : {assessment.composite_score}/10")
            print(f"I&W Status      : {'TRIGGERED' if assessment.iw_triggered else 'NOMINAL'}")
            print(f"Timestamp       : {assessment.timestamp}")
            print()
            print("Lens Scores:")
            for lens, data in assessment.lenses.items():
                score = data.get("score", 0.0)
                nato = data.get("nato_reliability", "F6")
                print(f"  {lens:<20} {score:.2f}/10  [{nato}]")
            print()
            print("Top Narrative:")
            print(assessment.top_narrative)
    else:
        parser.print_help()


if __name__ == "__main__":
    _main()

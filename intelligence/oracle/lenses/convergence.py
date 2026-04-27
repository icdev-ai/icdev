# CUI // SP-CTI
"""Cross-Domain Convergence Lens — multi-lens agreement and I&W trigger.

Reads the latest score from each of the other 3 lenses in sg_sio_assessments,
computes cross-lens agreement via standard deviation, and emits an I&W
(Indications & Warnings) trigger when convergence AND average threat are high.

convergence_score = 10 - (std_dev * 2), clamped 0-10
I&W trigger: convergence_score > 7.5 AND avg_score > 6.0

Feeds into SIOEngine via sg_sio_assessments with lens_source='convergence'.
"""

from __future__ import annotations

import json
import logging
import math
import uuid
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger("icdev.intelligence.oracle.convergence")

# The three peer lenses this lens aggregates
_PEER_LENSES = ["threat_posture", "behavior_pattern", "intent_assessment"]

# ── NATO Admiral/Source Reliability ─────────────────────────────────────────
_NATO_RELIABILITY_MAP = [
    (0.80, "A1"),
    (0.65, "B2"),
    (0.50, "C3"),
    (0.35, "D4"),
    (0.00, "E5"),
]


def _nato_reliability(confidence: float) -> str:
    for threshold, code in _NATO_RELIABILITY_MAP:
        if confidence >= threshold:
            return code
    return "F6"


# ── DB helper ────────────────────────────────────────────────────────────────

def _get_conn():
    from tools.db.storage import get_connection
    return get_connection()


# ── Read peer lens scores ─────────────────────────────────────────────────────

def _read_peer_scores() -> dict[str, float]:
    """Read the latest score from each peer lens in sg_sio_assessments."""
    conn = _get_conn()
    scores: dict[str, float] = {}
    try:
        for lens in _PEER_LENSES:
            try:
                row = conn.execute(
                    "SELECT score FROM sg_sio_assessments "
                    "WHERE lens_source = ? "
                    "ORDER BY created_at DESC LIMIT 1",
                    (lens,),
                ).fetchone()
                if row is not None and row[0] is not None:
                    scores[lens] = float(row[0])
            except Exception:
                pass
    finally:
        conn.close()
    return scores


# ── Statistics helpers ────────────────────────────────────────────────────────

def _std_dev(values: list[float]) -> float:
    n = len(values)
    if n < 2:
        return 0.0
    mean = sum(values) / n
    variance = sum((v - mean) ** 2 for v in values) / n
    return math.sqrt(variance)


# ── Convergence computation ───────────────────────────────────────────────────

def _compute_convergence(scores: dict[str, float]) -> dict[str, Any]:
    """Derive convergence metrics from peer lens scores."""
    if not scores:
        return {
            "convergence_score": 0.0,
            "avg_score": 0.0,
            "std_dev": 0.0,
            "iw_triggered": False,
            "lens_scores": {},
            "lens_count": 0,
        }

    values = list(scores.values())
    avg_score = sum(values) / len(values)
    std = _std_dev(values)

    convergence_score = max(0.0, min(10.0, 10.0 - (std * 2.0)))
    convergence_score = round(convergence_score, 2)

    iw_triggered = convergence_score > 7.5 and avg_score > 6.0

    return {
        "convergence_score": convergence_score,
        "avg_score": round(avg_score, 2),
        "std_dev": round(std, 4),
        "iw_triggered": iw_triggered,
        "lens_scores": scores,
        "lens_count": len(scores),
    }


# ── Confidence ────────────────────────────────────────────────────────────────

def _compute_confidence(std_dev: float, lens_count: int) -> float:
    """Confidence = 1 - (std_dev / 10), penalised for missing lenses."""
    base = max(0.0, 1.0 - (std_dev / 10.0))
    # Full confidence only if all 3 peer lenses provided a score
    completeness = lens_count / len(_PEER_LENSES)
    return round(base * completeness, 3)


# ── Narrative ────────────────────────────────────────────────────────────────

def _build_narrative(
    metrics: dict[str, Any],
    nato_code: str,
) -> str:
    cs = metrics["convergence_score"]
    avg = metrics["avg_score"]
    std = metrics["std_dev"]
    iw = metrics["iw_triggered"]
    scores = metrics["lens_scores"]
    pct = round(cs * 10, 0)

    score_parts = ", ".join(
        f"{lens.replace('_', ' ').title()}: {val:.1f}"
        for lens, val in scores.items()
    )
    iw_status = "TRIGGERED" if iw else "NOMINAL"

    return (
        f"Multi-lens convergence at {int(pct)}% "
        f"(std dev {std:.2f}): [{score_parts}]. "
        f"Average threat index: {avg:.1f}/10. "
        f"I&W status: {iw_status}. "
        f"Convergence Score: {cs}/10. "
        f"Confidence: {nato_code}."
    )


# ── DB persistence ────────────────────────────────────────────────────────────

def _persist_assessment(
    score: float,
    confidence: float,
    nato_code: str,
    narrative: str,
    evidence: dict[str, Any],
) -> None:
    conn = _get_conn()
    try:
        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            "INSERT INTO sg_sio_assessments "
            "(id, confidence, nato_reliability, recommendation, lens_source, "
            " timestamp, score, narrative, evidence_json, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                f"cv-{uuid.uuid4().hex[:10]}",
                confidence,
                nato_code,
                "Escalate I&W briefing to command if TRIGGERED status confirmed.",
                "convergence",
                now,
                score,
                narrative,
                json.dumps(evidence),
                now,
            ),
        )
        conn.commit()
        try:
            from tools.db.storage import is_pg
            if is_pg():
                conn.execute(
                    "DELETE FROM sg_sio_assessments "
                    "WHERE lens_source='convergence' "
                    "  AND created_at::timestamptz < NOW() - INTERVAL '24 hours'"
                )
            else:
                conn.execute(
                    "DELETE FROM sg_sio_assessments "
                    "WHERE lens_source='convergence' "
                    "  AND created_at < datetime('now', '-24 hours')"
                )
        except Exception:
            pass
        conn.commit()
    except Exception as exc:
        logger.warning("Failed to persist convergence assessment: %s", exc)
    finally:
        conn.close()


# ── Public API ────────────────────────────────────────────────────────────────

def run() -> dict[str, Any]:
    """Execute the Cross-Domain Convergence lens.

    Returns
    -------
    dict with keys:
        score            — float 0-10  (convergence_score)
        confidence       — float 0-1
        nato_reliability — str
        narrative        — str
        iw_triggered     — bool
        lens_scores      — dict of peer lens → score
    """
    peer_scores = _read_peer_scores()
    metrics = _compute_convergence(peer_scores)

    score = metrics["convergence_score"]
    confidence = _compute_confidence(metrics["std_dev"], metrics["lens_count"])
    nato_code = _nato_reliability(confidence)
    narrative = _build_narrative(metrics, nato_code)

    evidence = {
        "convergence_score": score,
        "avg_score": metrics["avg_score"],
        "std_dev": metrics["std_dev"],
        "iw_triggered": metrics["iw_triggered"],
        "lens_scores": metrics["lens_scores"],
        "lens_count": metrics["lens_count"],
    }

    try:
        _persist_assessment(score, confidence, nato_code, narrative, evidence)
    except Exception as exc:
        logger.warning("Persist skipped: %s", exc)

    return {
        "score": score,
        "confidence": confidence,
        "nato_reliability": nato_code,
        "narrative": narrative,
        "iw_triggered": metrics["iw_triggered"],
        "lens_scores": metrics["lens_scores"],
    }


# ── CLI entrypoint ────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import logging as _logging
    _logging.basicConfig(level=_logging.INFO, format="%(levelname)s %(message)s")
    result = run()
    print("\n── Cross-Domain Convergence ───────────────────────────────────")
    print(f"Score: {result['score']}/10   NATO Reliability: {result['nato_reliability']}")
    print(f"Confidence: {result['confidence']}")
    print(f"I&W Status: {'TRIGGERED' if result['iw_triggered'] else 'NOMINAL'}")
    print()
    print("Peer lens scores:", result["lens_scores"])
    print()
    print("Narrative:")
    print(result["narrative"])

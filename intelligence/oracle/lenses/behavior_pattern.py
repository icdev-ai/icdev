# CUI // SP-CTI
"""Behavior Pattern Lens — escalation velocity, repetition, and deception indicators.

Reads sg_conflict_events (last 30 days) to detect behavioural signatures:
  - escalation_velocity: rate of escalation_level increases
  - pattern_repetition: repeated event types
  - deception_indicators: information-operations events

behavior_pattern_score = (escalation_velocity*0.4 + pattern_repetition*0.35
                          + deception_indicators*0.25) × 10

Feeds into SIOEngine via sg_sio_assessments with lens_source='behavior_pattern'.
"""

from __future__ import annotations

import json
import logging
import uuid
from collections import Counter
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger("icdev.intelligence.oracle.behavior_pattern")

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


# ── Signal reading ────────────────────────────────────────────────────────────

def _read_events() -> list[Any]:
    """Return conflict events from the last 30 days."""
    conn = _get_conn()
    try:
        try:
            rows = conn.execute(
                "SELECT event_type, escalation_level, event_ts "
                "FROM sg_conflict_events "
                "WHERE event_ts >= date('now', '-30 days') "
                "ORDER BY event_ts ASC "
                "LIMIT 500"
            ).fetchall()
            return rows
        except Exception:
            return []
    finally:
        conn.close()


# ── Sub-score computation ─────────────────────────────────────────────────────

# Info-ops / deception event type keywords
_DECEPTION_KWS = {
    "info_op", "psyop", "disinformation", "propaganda", "influence",
    "cyber_info", "media_manip", "false_flag", "deception",
}


def _compute_sub_scores(rows: list[Any]) -> dict[str, float]:
    """Compute escalation_velocity, pattern_repetition, deception_indicators (all 0-1)."""
    if not rows:
        return {
            "escalation_velocity": 0.0,
            "pattern_repetition": 0.0,
            "deception_indicators": 0.0,
            "event_count": 0,
            "escalation_increases": 0,
            "deception_count": 0,
            "top_event_type": "unknown",
            "unique_event_types": 0,
        }

    event_count = len(rows)
    event_types = [(r[0] or "").lower() for r in rows]
    escalation_levels = [r[1] or 1 for r in rows]

    # ── Escalation velocity ──────────────────────────────────────────────────
    # Count transitions where escalation_level increases
    escalation_increases = sum(
        1
        for prev, curr in zip(escalation_levels[:-1], escalation_levels[1:])
        if curr > prev
    )
    # Normalize: max theoretical increases = event_count - 1
    max_increases = max(event_count - 1, 1)
    escalation_velocity = min(1.0, escalation_increases / max_increases * 3)

    # ── Pattern repetition ───────────────────────────────────────────────────
    # Measure how concentrated event types are (high repetition = low entropy)
    counter = Counter(event_types)
    total_types = len(counter)
    most_common_count = counter.most_common(1)[0][1] if counter else 0
    # If most common type > 30% of events, that's meaningful repetition
    repetition_ratio = most_common_count / max(event_count, 1)
    # Scale so 100% repetition → 1.0, 10% → ~0.0
    pattern_repetition = min(1.0, max(0.0, (repetition_ratio - 0.1) / 0.9))

    # ── Deception indicators ─────────────────────────────────────────────────
    deception_count = sum(
        1 for et in event_types if any(kw in et for kw in _DECEPTION_KWS)
    )
    deception_indicators = min(1.0, deception_count / max(event_count, 1) * 5)

    return {
        "escalation_velocity": round(escalation_velocity, 4),
        "pattern_repetition": round(pattern_repetition, 4),
        "deception_indicators": round(deception_indicators, 4),
        "event_count": event_count,
        "escalation_increases": escalation_increases,
        "deception_count": deception_count,
        "top_event_type": counter.most_common(1)[0][0] if counter else "unknown",
        "unique_event_types": total_types,
    }


def _behavior_score(sub: dict[str, float]) -> float:
    """Compute behavior_pattern_score = weighted average × 10."""
    raw = (
        sub["escalation_velocity"] * 0.40
        + sub["pattern_repetition"] * 0.35
        + sub["deception_indicators"] * 0.25
    )
    return round(min(10.0, max(0.0, raw * 10.0)), 2)


# ── Narrative ────────────────────────────────────────────────────────────────

def _build_narrative(
    sub: dict[str, float],
    score: float,
    nato_code: str,
) -> str:
    n = sub["event_count"]
    vel = round(sub["escalation_velocity"] * 10, 1)
    dec = sub["deception_count"]
    top_et = sub.get("top_event_type", "unknown")

    trend_label = "rising" if sub["escalation_velocity"] > 0.4 else (
        "stable" if sub["escalation_velocity"] > 0.1 else "low"
    )

    return (
        f"Pattern analysis of {n} event(s): {trend_label} escalation trend "
        f"(velocity {vel}/10), dominant event type '{top_et}' "
        f"({sub['unique_event_types']} unique types detected), "
        f"{dec} deception indicator(s). "
        f"Behaviour Pattern Score: {score}/10. "
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
                f"bp-{uuid.uuid4().hex[:10]}",
                confidence,
                nato_code,
                "Review escalation pattern and deception indicators for COA assessment.",
                "behavior_pattern",
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
                    "WHERE lens_source='behavior_pattern' "
                    "  AND created_at::timestamptz < NOW() - INTERVAL '24 hours'"
                )
            else:
                conn.execute(
                    "DELETE FROM sg_sio_assessments "
                    "WHERE lens_source='behavior_pattern' "
                    "  AND created_at < datetime('now', '-24 hours')"
                )
        except Exception:
            pass
        conn.commit()
    except Exception as exc:
        logger.warning("Failed to persist behavior pattern assessment: %s", exc)
    finally:
        conn.close()


# ── Public API ────────────────────────────────────────────────────────────────

def run() -> dict[str, Any]:
    """Execute the Behavior Pattern lens.

    Returns
    -------
    dict with keys:
        score            — float 0-10
        confidence       — float 0-1
        nato_reliability — str (e.g. "B2")
        narrative        — str
        sub_scores       — dict of intermediate metrics
    """
    rows = _read_events()
    sub = _compute_sub_scores(rows)
    score = _behavior_score(sub)

    event_count = sub["event_count"]
    confidence = round(min(1.0, event_count / 20.0), 3)
    nato_code = _nato_reliability(confidence)
    narrative = _build_narrative(sub, score, nato_code)

    evidence = {
        "escalation_velocity": sub["escalation_velocity"],
        "pattern_repetition": sub["pattern_repetition"],
        "deception_indicators": sub["deception_indicators"],
        "event_count": event_count,
        "escalation_increases": sub.get("escalation_increases", 0),
        "deception_count": sub.get("deception_count", 0),
        "top_event_type": sub.get("top_event_type", "unknown"),
        "unique_event_types": sub.get("unique_event_types", 0),
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
        "sub_scores": evidence,
    }


# ── CLI entrypoint ────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import logging as _logging
    _logging.basicConfig(level=_logging.INFO, format="%(levelname)s %(message)s")
    result = run()
    print("\n── Behavior Pattern ───────────────────────────────────────────")
    print(f"Score: {result['score']}/10   NATO Reliability: {result['nato_reliability']}")
    print(f"Confidence: {result['confidence']}")
    print()
    print("Narrative:")
    print(result["narrative"])

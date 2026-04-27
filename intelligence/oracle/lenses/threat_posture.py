# CUI // SP-CTI
"""Threat Posture Lens — adversary mobility, force concentration, and offensive indicators.

Reads from sg_conflict_events and sg_raw_signals to score the current threat posture
on a 0-10 scale. Feeds into SIOEngine via sg_sio_assessments with
lens_source='threat_posture'.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger("icdev.intelligence.oracle.threat_posture")

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

def _read_signals() -> tuple[list[Any], list[Any]]:
    """Return (conflict_event_rows, raw_signal_rows)."""
    conn = _get_conn()
    try:
        ce_rows = []
        try:
            ce_rows = conn.execute(
                "SELECT event_type, escalation_level, event_ts, domain "
                "FROM sg_conflict_events "
                "WHERE event_ts >= date('now', '-30 days') "
                "LIMIT 500"
            ).fetchall()
        except Exception:
            pass

        sig_rows = []
        try:
            sig_rows = conn.execute(
                "SELECT title, body, created_at "
                "FROM sg_raw_signals "
                "WHERE created_at >= date('now', '-7 days') "
                "LIMIT 200"
            ).fetchall()
        except Exception:
            pass

        return ce_rows, sig_rows
    finally:
        conn.close()


# ── Sub-score computation ─────────────────────────────────────────────────────

def _compute_scores(
    ce_rows: list[Any],
    sig_rows: list[Any],
) -> dict[str, float]:
    """Compute adversary_mobility, force_concentration, offensive_indicator sub-scores."""
    if not ce_rows and not sig_rows:
        return {
            "adversary_mobility_score": 0.5,
            "force_concentration_score": 0.5,
            "offensive_indicator_count": 0,
        }

    total_events = len(ce_rows)

    # Mobility keywords in raw signals
    mobility_kws = {"movement", "advance", "withdrawal", "reposition", "flanking",
                    "maneuver", "convoy", "logistics", "mobiliz"}
    # Force concentration keywords
    concentration_kws = {"buildup", "massing", "concentration", "staging", "assembly",
                         "reinforcement", "surge", "troop"}
    # Offensive indicator CAMEO-style event types
    offensive_types = {"attack", "assault", "bombing", "airstrike", "missile",
                       "shelling", "cyber_op", "offensive"}

    mobility_hits = 0
    concentration_hits = 0
    for title, body, _ in sig_rows:
        text = ((title or "") + " " + (body or "")).lower()
        if any(k in text for k in mobility_kws):
            mobility_hits += 1
        if any(k in text for k in concentration_kws):
            concentration_hits += 1

    sig_total = max(len(sig_rows), 1)
    adversary_mobility_score = min(1.0, mobility_hits / sig_total * 4)
    force_concentration_score = min(1.0, concentration_hits / sig_total * 4)

    # Offensive indicator count: events with offensive event types or high escalation
    offensive_count = 0
    for event_type, escalation_level, *_ in ce_rows:
        et = (event_type or "").lower()
        el = escalation_level or 0
        if any(k in et for k in offensive_types) or el >= 4:
            offensive_count += 1

    return {
        "adversary_mobility_score": adversary_mobility_score,
        "force_concentration_score": force_concentration_score,
        "offensive_indicator_count": offensive_count,
        "total_events": total_events,
        "sig_total": sig_total,
    }


def _threat_posture_score(sub_scores: dict[str, float]) -> float:
    """Weighted average × 10, clamped 0-10."""
    mob = sub_scores["adversary_mobility_score"]
    conc = sub_scores["force_concentration_score"]
    total_events = sub_scores.get("total_events", 0)
    offensive_count = sub_scores["offensive_indicator_count"]

    # Normalize offensive indicator to 0-1 (cap at 20 events = 1.0)
    off_norm = min(1.0, offensive_count / max(total_events, 1) * 2)

    raw = (mob * 0.35) + (conc * 0.35) + (off_norm * 0.30)
    return round(min(10.0, max(0.0, raw * 10.0)), 2)


def _compute_confidence(
    ce_rows: list[Any],
    sig_rows: list[Any],
) -> float:
    """Derive confidence from signal recency and count."""
    total = len(ce_rows) + len(sig_rows)
    if total == 0:
        return 0.0
    # Recency bonus: penalise if few signals
    count_factor = min(1.0, total / 30.0)
    return round(min(1.0, count_factor * 0.95), 3)


# ── Narrative ────────────────────────────────────────────────────────────────

def _build_narrative(
    sub_scores: dict[str, float],
    score: float,
    nato_code: str,
    ce_rows: list[Any],
) -> str:
    domains: set[str] = set()
    for row in ce_rows:
        domain = row[3] if len(row) > 3 else None
        if domain:
            domains.add(str(domain))
    domain_count = len(domains) if domains else len(set(
        (r[0] or "").split("_")[0] for r in ce_rows
    ))
    offensive_count = sub_scores["offensive_indicator_count"]
    total_events = sub_scores.get("total_events", 0)
    mob = round(sub_scores["adversary_mobility_score"] * 10, 1)
    conc = round(sub_scores["force_concentration_score"] * 10, 1)

    return (
        f"{offensive_count} active threat indicator(s) detected across "
        f"{max(domain_count, 1)} domain(s) ({total_events} events analysed). "
        f"Adversary mobility index: {mob}/10. "
        f"Force concentration index: {conc}/10. "
        f"Threat Posture Score: {score}/10. "
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
                f"tp-{uuid.uuid4().hex[:10]}",
                confidence,
                nato_code,
                "Assess adversary mobility corridors and force concentration areas.",
                "threat_posture",
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
                    "WHERE lens_source='threat_posture' "
                    "  AND created_at::timestamptz < NOW() - INTERVAL '24 hours'"
                )
            else:
                conn.execute(
                    "DELETE FROM sg_sio_assessments "
                    "WHERE lens_source='threat_posture' "
                    "  AND created_at < datetime('now', '-24 hours')"
                )
        except Exception:
            pass
        conn.commit()
    except Exception as exc:
        logger.warning("Failed to persist threat posture assessment: %s", exc)
    finally:
        conn.close()


# ── Public API ────────────────────────────────────────────────────────────────

def run() -> dict[str, Any]:
    """Execute the Threat Posture lens.

    Returns
    -------
    dict with keys:
        score            — float 0-10
        confidence       — float 0-1
        nato_reliability — str (e.g. "B2")
        narrative        — str
        sub_scores       — dict of intermediate metrics
    """
    ce_rows, sig_rows = _read_signals()
    sub_scores = _compute_scores(ce_rows, sig_rows)
    score = _threat_posture_score(sub_scores)
    confidence = _compute_confidence(ce_rows, sig_rows)
    nato_code = _nato_reliability(confidence)
    narrative = _build_narrative(sub_scores, score, nato_code, ce_rows)

    evidence = {
        "adversary_mobility_score": sub_scores["adversary_mobility_score"],
        "force_concentration_score": sub_scores["force_concentration_score"],
        "offensive_indicator_count": sub_scores["offensive_indicator_count"],
        "event_count": sub_scores.get("total_events", 0),
        "signal_count": sub_scores.get("sig_total", 0),
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
    print("\n── Threat Posture ─────────────────────────────────────────────")
    print(f"Score: {result['score']}/10   NATO Reliability: {result['nato_reliability']}")
    print(f"Confidence: {result['confidence']}")
    print()
    print("Narrative:")
    print(result["narrative"])

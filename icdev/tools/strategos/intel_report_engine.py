# CUI // SP-CTI
"""Intelligence report engine — predictive analysis + generative leadership briefings.

Reads sg_mesh_signals and sg_mesh_patterns from the federated data mesh,
runs deterministic escalation prediction scoring, and generates structured
intelligence report artifacts (INTSUM, leadership brief) for decision-makers.

NIST 800-53: SI-12, AU-9, RA-3
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent.parent
import sys
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.db.storage import get_connection  # noqa: E402
from tools.logging.icdev_logger import get_logger  # noqa: E402

logger = get_logger("icdev.strategos.intel_report_engine")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sg_intel_reports (
    id              TEXT PRIMARY KEY,
    report_type     TEXT NOT NULL,
    theater_id      TEXT,
    title           TEXT NOT NULL,
    classification  TEXT DEFAULT 'CUI // SP-CTI',
    escalation_score REAL DEFAULT 0.0,
    body_json       TEXT NOT NULL,
    generated_at    TEXT NOT NULL
);
"""

# Escalation score weights by pattern type
_PATTERN_WEIGHTS: dict[str, float] = {
    "wmd_concern": 1.0,
    "offensive_action": 0.8,
    "force_buildup": 0.7,
    "diplomatic_breakdown": 0.6,
    "civilian_impact": 0.4,
}

# Risk level thresholds
_RISK_LEVELS = [
    (0.8, "CRITICAL"),
    (0.6, "HIGH"),
    (0.4, "ELEVATED"),
    (0.2, "MODERATE"),
    (0.0, "LOW"),
]


def _risk_level(score: float) -> str:
    for threshold, label in _RISK_LEVELS:
        if score >= threshold:
            return label
    return "LOW"


def _compute_escalation_score(patterns: list[dict[str, Any]]) -> float:
    """Weighted sum of pattern confidence values, capped at 1.0."""
    total = 0.0
    for p in patterns:
        weight = _PATTERN_WEIGHTS.get(p.get("pattern_type", ""), 0.3)
        total += float(p.get("confidence", 0.0)) * weight
    return round(min(1.0, total), 3)


def _fetch_patterns(conn, theater_id: str | None = None) -> list[dict[str, Any]]:
    try:
        rows = conn.execute(
            "SELECT pattern_type, keywords, event_count, confidence, detected_at "
            "FROM sg_mesh_patterns ORDER BY confidence DESC LIMIT 20"
        ).fetchall()
    except Exception:
        return []
    return [
        {
            "pattern_type": r[0],
            "keywords": json.loads(r[1] or "[]"),
            "event_count": r[2],
            "confidence": r[3],
            "detected_at": r[4],
        }
        for r in rows
    ]


def _fetch_recent_events(conn, theater_id: str | None, limit: int = 10) -> list[dict[str, Any]]:
    where = "WHERE theater_id = ?" if theater_id else ""
    params = [theater_id, limit] if theater_id else [limit]
    try:
        rows = conn.execute(
            f"SELECT provider, event_type, event_date, description, fatalities "
            f"FROM sg_mesh_signals {where} ORDER BY event_date DESC LIMIT ?",
            params,
        ).fetchall()
    except Exception:
        return []
    return [
        {"provider": r[0], "event_type": r[1], "event_date": r[2],
         "description": (r[3] or "")[:200], "fatalities": r[4] or 0}
        for r in rows
    ]


def generate_intsum(theater_id: str | None = None) -> dict[str, Any]:
    """Generate an Intelligence Summary (INTSUM) for leadership decision-making."""
    now = datetime.now(timezone.utc)
    report_id = str(uuid.uuid4())
    date_str = now.strftime("%Y%m%d")
    time_str = now.strftime("%H%MZ")

    with get_connection() as conn:
        for stmt in _SCHEMA.strip().split(";"):
            if stmt.strip():
                conn.execute(stmt.strip())
        conn.commit()

        patterns = _fetch_patterns(conn, theater_id)
        events = _fetch_recent_events(conn, theater_id)

    escalation_score = _compute_escalation_score(patterns)
    risk = _risk_level(escalation_score)

    theater_label = theater_id or "GLOBAL"

    # Key findings narrative (deterministic)
    findings: list[str] = []
    for p in patterns[:5]:
        kws = ", ".join(p["keywords"][:3])
        findings.append(
            f"{p['pattern_type'].replace('_',' ').upper()} indicators detected "
            f"(confidence {p['confidence']:.0%}): [{kws}]"
        )

    # Executive summary
    exec_summary = (
        f"As of {date_str}/{time_str}, the {theater_label} theater exhibits an escalation "
        f"risk score of {escalation_score:.2f} ({risk}). "
        f"{len(patterns)} pattern clusters identified across {sum(p['event_count'] for p in patterns)} events. "
        + (f"Top concern: {patterns[0]['pattern_type'].replace('_',' ')} activity." if patterns else "No significant patterns detected.")
    )

    body = {
        "executive_summary": exec_summary,
        "escalation_score": escalation_score,
        "risk_level": risk,
        "theater": theater_label,
        "pattern_findings": findings,
        "recent_events": events[:10],
        "generated_at": now.isoformat(),
        "classification": "CUI // SP-CTI",
    }

    with get_connection() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO sg_intel_reports "
            "(id, report_type, theater_id, title, classification, escalation_score, body_json, generated_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (
                report_id, "INTSUM", theater_id,
                f"INTSUM {date_str}/{time_str} — {theater_label} [{risk}]",
                "CUI // SP-CTI", escalation_score,
                json.dumps(body), now.isoformat(),
            ),
        )
        conn.commit()

    logger.info("Generated INTSUM %s (score=%.2f risk=%s)", report_id, escalation_score, risk)
    return {"report_id": report_id, **body}


def generate_leadership_brief(theater_id: str | None = None) -> dict[str, Any]:
    """Generate a concise leadership briefing from the latest INTSUM."""
    intsum = generate_intsum(theater_id)

    brief = {
        "classification": "CUI // SP-CTI",
        "for": "Leadership / Decision-Maker",
        "subject": f"Conflict Monitoring Brief — {intsum.get('theater','GLOBAL')}",
        "date": datetime.now(timezone.utc).strftime("%d %b %Y"),
        "bottom_line_up_front": (
            f"Escalation risk: {intsum['risk_level']} ({intsum['escalation_score']:.0%}). "
            + (intsum.get("pattern_findings", ["No significant activity."])[0] if intsum.get("pattern_findings") else "No significant activity.")
        ),
        "key_findings": intsum.get("pattern_findings", []),
        "recent_events_summary": [
            f"[{e['event_date']}] {e['event_type'].upper()}: {e['description'][:100]}"
            for e in intsum.get("recent_events", [])[:5]
        ],
        "recommended_actions": _recommended_actions(intsum["risk_level"]),
        "intsum_ref": intsum["report_id"],
    }
    return brief


def _recommended_actions(risk: str) -> list[str]:
    base = ["Continue monitoring sg_mesh_signals for pattern changes"]
    if risk == "CRITICAL":
        return ["IMMEDIATE: Escalate to senior leadership", "Activate crisis response protocol"] + base
    if risk == "HIGH":
        return ["Alert operational commanders", "Increase ISR collection tempo"] + base
    if risk == "ELEVATED":
        return ["Brief duty officer", "Review contingency plans"] + base
    return base


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--theater", default=None)
    ap.add_argument("--brief", action="store_true")
    args = ap.parse_args()
    result = generate_leadership_brief(args.theater) if args.brief else generate_intsum(args.theater)
    print(json.dumps(result, indent=2))

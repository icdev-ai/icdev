# CUI // SP-CTI
"""Source Registry — STANAG 2022 Intelligence Source Grading.

Reliability grades (A-F): assess the source's track record.
Credibility grades (1-6): assess how much to trust this specific report.
Confidence decay: intelligence confidence decreases with age.

Doctrinal basis: STANAG 2022 / AJP-2.1 — Intelligence Reporting.

Combined grade examples:
  A1 = Completely reliable source, confirmed information (highest)
  B2 = Usually reliable source, probably true
  F6 = Cannot judge reliability OR information validity (lowest)
"""
from __future__ import annotations
from tools.logging.icdev_logger import get_logger

import math
import uuid
from datetime import datetime, timezone
from typing import Any

logger = get_logger("icdev.strategos.source_registry")

RELIABILITY_GRADES = {
    "A": "Completely reliable — no doubt about authenticity, trustworthiness, competency",
    "B": "Usually reliable — minor doubt about authenticity",
    "C": "Fairly reliable — some doubt about authenticity",
    "D": "Not usually reliable — significant doubt",
    "E": "Unreliable — lacking in authenticity, trustworthiness, competency",
    "F": "Cannot be judged — insufficient basis to evaluate",
}

CREDIBILITY_GRADES = {
    "1": "Confirmed — confirmed by other independent sources",
    "2": "Probably true — not confirmed but probably true",
    "3": "Possibly true — not confirmed, possibly true",
    "4": "Doubtful — not confirmed and doubtful",
    "5": "Improbable — contradicted by other information",
    "6": "Cannot be judged — no basis to evaluate",
}

RELIABILITY_SCORE = {"A": 1.0, "B": 0.85, "C": 0.65, "D": 0.4, "E": 0.15, "F": 0.5}
CREDIBILITY_SCORE = {"1": 1.0, "2": 0.85, "3": 0.65, "4": 0.4, "5": 0.15, "6": 0.5}

# Half-life in hours for each collection type (confidence decays to 50% at this age)
DECAY_HALF_LIFE = {
    "HUMINT": 72,
    "SIGINT": 6,
    "IMINT": 24,
    "OSINT": 12,
    "MASINT": 48,
    "SOCMINT": 8,
    "technical": 96,
    "unknown": 24,
}


def _now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def confidence_at_age(
    base_confidence: float,
    source_type: str,
    collected_at: str,
    reference_time: str | None = None,
) -> float:
    """Apply exponential decay to base confidence based on intelligence age."""
    try:
        from datetime import datetime, timezone
        collected = datetime.fromisoformat(collected_at.replace("Z", "+00:00"))
        ref = (datetime.fromisoformat(reference_time.replace("Z", "+00:00"))
               if reference_time else datetime.now(timezone.utc))
        age_hours = (ref - collected).total_seconds() / 3600
        half_life = DECAY_HALF_LIFE.get(source_type, 24)
        decay = math.exp(-math.log(2) * age_hours / half_life)
        return round(max(0.0, min(1.0, base_confidence * decay)), 3)
    except Exception:
        return base_confidence


def combined_confidence(reliability: str, credibility: str) -> float:
    """Compute initial confidence from STANAG grades."""
    r = RELIABILITY_SCORE.get(reliability, 0.5)
    c = CREDIBILITY_SCORE.get(credibility, 0.5)
    return round((r + c) / 2, 3)


# ── Source CRUD ────────────────────────────────────────────────────────────────

def create_source(
    source_name: str,
    source_type: str = "HUMINT",
    reliability: str = "F",
    theater: str = "global",
    notes: str = "",
    created_by: str = "analyst",
) -> dict[str, Any]:
    from tools.db.storage import get_connection
    conn = get_connection()
    try:
        source_id = str(uuid.uuid4())
        now = _now_utc()
        conn.execute(
            "INSERT INTO sg_sources "
            "(id, source_name, source_type, reliability, theater, notes, "
            " created_by, created_at, updated_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (source_id, source_name, source_type, reliability,
             theater, notes, created_by, now, now),
        )
        conn.commit()
        return {"source_id": source_id, "source_name": source_name, "reliability": reliability}
    finally:
        conn.close()


def update_reliability(source_id: str, reliability: str) -> bool:
    from tools.db.storage import get_connection, is_pg
    ph = "%s" if is_pg() else "?"
    conn = get_connection()
    try:
        conn.execute(
            f"UPDATE sg_sources SET reliability = {ph}, updated_at = {ph} WHERE id = {ph}",  # nosec B608
            (reliability, _now_utc(), source_id),
        )
        conn.commit()
        return True
    except Exception as exc:
        logger.error("update_reliability failed: %s", exc)
        return False
    finally:
        conn.close()


def list_sources(theater: str = "", source_type: str = "") -> list[dict]:
    from tools.db.storage import get_connection, is_pg
    ph = "%s" if is_pg() else "?"
    conn = get_connection()
    try:
        clauses, params = [], []
        if theater and theater != "global":
            clauses.append(f"theater = {ph}")
            params.append(theater)
        if source_type:
            clauses.append(f"source_type = {ph}")
            params.append(source_type)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        rows = conn.execute(
            f"SELECT id, source_name, source_type, reliability, theater, "  # nosec B608
            f"notes, last_reporting, created_by, created_at "
            f"FROM sg_sources{where} ORDER BY reliability, source_name",
            params,
        ).fetchall()
        cols = ("id", "source_name", "source_type", "reliability", "theater",
                "notes", "last_reporting", "created_by", "created_at")
        result = []
        for r in rows:
            d = dict(zip(cols, r))
            d["reliability_desc"] = RELIABILITY_GRADES.get(d["reliability"], "")
            result.append(d)
        return result
    finally:
        conn.close()


def delete_source(source_id: str) -> bool:
    from tools.db.storage import get_connection, is_pg
    ph = "%s" if is_pg() else "?"
    conn = get_connection()
    try:
        conn.execute(f"DELETE FROM sg_source_reports WHERE source_id = {ph}", (source_id,))  # nosec B608
        conn.execute(f"DELETE FROM sg_sources WHERE id = {ph}", (source_id,))  # nosec B608
        conn.commit()
        return True
    except Exception as exc:
        logger.error("delete_source failed: %s", exc)
        return False
    finally:
        conn.close()


# ── Source Reports ─────────────────────────────────────────────────────────────

def add_report(
    source_id: str,
    content: str,
    credibility: str = "6",
    subject: str = "",
    collected_at: str = "",
) -> dict[str, Any]:
    from tools.db.storage import get_connection, is_pg
    ph = "%s" if is_pg() else "?"
    conn = get_connection()
    try:
        report_id = str(uuid.uuid4())
        now = _now_utc()
        cat = collected_at or now

        src_row = conn.execute(
            f"SELECT source_type, reliability FROM sg_sources WHERE id = {ph}", (source_id,)  # nosec B608
        ).fetchone()
        source_type = src_row[0] if src_row else "unknown"
        reliability = src_row[1] if src_row else "F"

        base_conf = combined_confidence(reliability, credibility)

        conn.execute(
            "INSERT INTO sg_source_reports "
            "(id, source_id, content, credibility, collected_at, confidence, subject, created_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
            (report_id, source_id, content, credibility, cat, base_conf, subject, now),
        )
        conn.execute(
            f"UPDATE sg_sources SET last_reporting = {ph}, updated_at = {ph} WHERE id = {ph}",  # nosec B608
            (cat, now, source_id),
        )
        conn.commit()
        current_conf = confidence_at_age(base_conf, source_type, cat)
        return {
            "report_id": report_id,
            "base_confidence": base_conf,
            "current_confidence": current_conf,
            "grade": f"{reliability}{credibility}",
        }
    finally:
        conn.close()


def list_reports(source_id: str = "", limit: int = 50) -> list[dict]:
    from tools.db.storage import get_connection, is_pg
    ph = "%s" if is_pg() else "?"
    conn = get_connection()
    try:
        now = _now_utc()
        if source_id:
            rows = conn.execute(
                f"SELECT r.id, r.source_id, s.source_name, s.source_type, s.reliability, "  # nosec B608
                f"r.content, r.credibility, r.collected_at, r.confidence, r.subject, r.created_at "
                f"FROM sg_source_reports r "
                f"JOIN sg_sources s ON s.id = r.source_id "
                f"WHERE r.source_id = {ph} ORDER BY r.collected_at DESC LIMIT {ph}",
                (source_id, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                f"SELECT r.id, r.source_id, s.source_name, s.source_type, s.reliability, "  # nosec B608
                f"r.content, r.credibility, r.collected_at, r.confidence, r.subject, r.created_at "
                f"FROM sg_source_reports r "
                f"JOIN sg_sources s ON s.id = r.source_id "
                f"ORDER BY r.collected_at DESC LIMIT {ph}",
                (limit,),
            ).fetchall()
        cols = ("id", "source_id", "source_name", "source_type", "reliability",
                "content", "credibility", "collected_at", "confidence", "subject", "created_at")
        result = []
        for r in rows:
            d = dict(zip(cols, r))
            d["grade"] = f"{d['reliability']}{d['credibility']}"
            d["current_confidence"] = confidence_at_age(
                float(d["confidence"] or 0.5), d["source_type"], d["collected_at"], now
            )
            d["credibility_desc"] = CREDIBILITY_GRADES.get(d["credibility"], "")
            result.append(d)
        return result
    finally:
        conn.close()

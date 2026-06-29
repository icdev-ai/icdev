# CUI // SP-CTI
"""BDA Module — Battle Damage Assessment (JP 3-60 / ATP 3-60).

Records post-strike effects assessments with physical damage rating,
functional defeat determination, confidence level, and collection method.
Append-only (NIST AU) — no updates or deletes.

Usage:
    from tools.strategos.bda import create_assessment, list_assessments
    bda = create_assessment(target_name="SAM Site Alpha", ...)
"""
from __future__ import annotations
from tools.logging.icdev_logger import get_logger

import uuid
from datetime import datetime, timezone
from typing import Any

logger = get_logger("icdev.strategos.bda")

DAMAGE_LEVELS = [
    "destroyed",
    "severely_damaged",
    "moderately_damaged",
    "negligible",
    "unknown",
]
DAMAGE_LABELS = {
    "destroyed":          ("D",  "#e94560"),
    "severely_damaged":   ("SD", "#f97316"),
    "moderately_damaged": ("MD", "#f59e0b"),
    "negligible":         ("N",  "#10b981"),
    "unknown":            ("U",  "#6b7280"),
}
COLLECTION_METHODS = ["IMINT", "SIGINT", "HUMINT", "OSINT", "SOCMINT", "combined", "unknown"]


def _now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def create_assessment(
    target_name: str,
    target_id: str = "",
    strike_time: str = "",
    method: str = "kinetic",
    physical_damage: str = "unknown",
    functional_defeat: bool = False,
    confidence: float = 0.5,
    collection_method: str = "IMINT",
    analyst_notes: str = "",
    theater: str = "unspecified",
    created_by: str = "analyst",
) -> dict[str, Any]:
    from tools.db.storage import get_connection
    conn = get_connection()
    try:
        bda_id = str(uuid.uuid4())
        now = _now_utc()
        conn.execute(
            "INSERT INTO sg_bda_assessments "
            "(id, target_id, target_name, strike_time, method, physical_damage, "
            " functional_defeat, confidence, collection_method, analyst_notes, "
            " theater, classification, created_by, created_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (
                bda_id,
                target_id or None,
                target_name,
                strike_time or now,
                method,
                physical_damage,
                1 if functional_defeat else 0,
                confidence,
                collection_method,
                analyst_notes,
                theater,
                "CUI // SP-CTI",
                created_by,
                now,
            ),
        )
        conn.commit()
        return {
            "bda_id": bda_id,
            "target_name": target_name,
            "physical_damage": physical_damage,
            "functional_defeat": functional_defeat,
        }
    finally:
        conn.close()


def list_assessments(theater: str = "", limit: int = 50) -> list[dict]:
    from tools.db.storage import get_connection, is_pg
    ph = "%s" if is_pg() else "?"
    conn = get_connection()
    try:
        if theater:
            rows = conn.execute(
                f"SELECT id, target_id, target_name, strike_time, method, "  # nosec B608
                f"physical_damage, functional_defeat, confidence, collection_method, "
                f"analyst_notes, theater, created_by, created_at "
                f"FROM sg_bda_assessments WHERE theater = {ph} "
                f"ORDER BY created_at DESC LIMIT {ph}",
                (theater, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id, target_id, target_name, strike_time, method, "  # nosec B608
                "physical_damage, functional_defeat, confidence, collection_method, "
                "analyst_notes, theater, created_by, created_at "
                "FROM sg_bda_assessments ORDER BY created_at DESC LIMIT %s",
                (limit,),
            ).fetchall()
        cols = ("id", "target_id", "target_name", "strike_time", "method",
                "physical_damage", "functional_defeat", "confidence",
                "collection_method", "analyst_notes", "theater",
                "created_by", "created_at")
        results = []
        for r in rows:
            d = dict(zip(cols, r))
            d["functional_defeat"] = bool(d["functional_defeat"])
            label, color = DAMAGE_LABELS.get(d["physical_damage"], ("U", "#6b7280"))
            d["damage_label"] = label
            d["damage_color"] = color
            results.append(d)
        return results
    finally:
        conn.close()


def get_summary(theater: str = "") -> dict[str, Any]:
    """Return aggregated BDA stats for dashboard display."""
    from tools.db.storage import get_connection, is_pg
    ph = "%s" if is_pg() else "?"
    conn = get_connection()
    try:
        base = "FROM sg_bda_assessments"
        params: tuple = ()
        if theater:
            base += f" WHERE theater = {ph}"
            params = (theater,)

        total_row = conn.execute(f"SELECT COUNT(*) {base}", params).fetchone()  # nosec B608
        total = total_row[0] if total_row else 0

        defeat_row = conn.execute(
            f"SELECT COUNT(*) {base}"  # nosec B608
            + (" AND " if theater else " WHERE ") + "functional_defeat = 1",
            params,
        ).fetchone()
        defeated = defeat_row[0] if defeat_row else 0

        return {
            "total_assessments": total,
            "functional_defeats": defeated,
            "defeat_rate": round(defeated / total, 2) if total > 0 else 0.0,
        }
    except Exception as exc:
        logger.debug("BDA summary failed: %s", exc)
        return {"total_assessments": 0, "functional_defeats": 0, "defeat_rate": 0.0}
    finally:
        conn.close()

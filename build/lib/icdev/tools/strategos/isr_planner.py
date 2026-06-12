# CUI // SP-CTI
"""ISR Collection Planner — Map collection requirements to assets with gap analysis.

Collection Requirements: Named Area of Interest (NAI) + collection type + time window.
Assets: UAV / satellite / SIGINT aircraft / HUMINT — pulled from airspace COP or manually registered.
Gap Analysis: requirements with no assigned asset, overdue windows, coverage overlaps.

Doctrinal basis: FM 3-55 / JP 2-01 — Intelligence Collection Planning.
"""
from __future__ import annotations
from tools.logging.icdev_logger import get_logger

import json
import uuid
from datetime import datetime, timezone
from typing import Any

logger = get_logger("icdev.strategos.isr_planner")

COLLECTION_TYPES = ["IMINT", "SIGINT", "HUMINT", "OSINT", "MASINT", "mixed"]
ASSET_TYPES = ["UAV", "satellite", "SIGINT_aircraft", "HUMINT_asset", "surface", "cyber"]
ASSET_CAPS_MAP = {
    "UAV": ["IMINT", "SIGINT"],
    "satellite": ["IMINT", "SIGINT", "MASINT"],
    "SIGINT_aircraft": ["SIGINT"],
    "HUMINT_asset": ["HUMINT"],
    "surface": ["HUMINT", "SIGINT", "MASINT"],
    "cyber": ["SIGINT", "OSINT"],
}


def _now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── Requirements ──────────────────────────────────────────────────────────────

def create_requirement(
    nai: str,
    collection_type: str = "IMINT",
    priority: int = 3,
    theater: str = "unspecified",
    earliest: str = "",
    latest: str = "",
    purpose: str = "",
    created_by: str = "analyst",
) -> dict[str, Any]:
    from tools.db.storage import get_connection
    conn = get_connection()
    try:
        req_id = str(uuid.uuid4())
        now = _now_utc()
        conn.execute(
            "INSERT INTO sg_isr_requirements "
            "(id, nai, collection_type, priority, theater, earliest, latest, "
            " purpose, status, created_by, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'open', ?, ?, ?)",
            (req_id, nai, collection_type, priority, theater,
             earliest or None, latest or None, purpose, created_by, now, now),
        )
        conn.commit()
        return {"requirement_id": req_id, "nai": nai, "status": "open"}
    finally:
        conn.close()


def list_requirements(theater: str = "", status: str = "") -> list[dict]:
    from tools.db.storage import get_connection, is_pg
    ph = "%s" if is_pg() else "?"
    conn = get_connection()
    try:
        clauses, params = [], []
        if theater:
            clauses.append(f"theater = {ph}")
            params.append(theater)
        if status:
            clauses.append(f"status = {ph}")
            params.append(status)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        rows = conn.execute(
            f"SELECT id, nai, collection_type, priority, theater, earliest, latest, "  # nosec B608
            f"purpose, status, assigned_asset_id, created_by, created_at "
            f"FROM sg_isr_requirements{where} ORDER BY priority, created_at",
            params,
        ).fetchall()
        cols = ("id", "nai", "collection_type", "priority", "theater",
                "earliest", "latest", "purpose", "status",
                "assigned_asset_id", "created_by", "created_at")
        return [dict(zip(cols, r)) for r in rows]
    finally:
        conn.close()


def assign_asset(requirement_id: str, asset_id: str) -> dict[str, Any]:
    from tools.db.storage import get_connection, is_pg
    ph = "%s" if is_pg() else "?"
    conn = get_connection()
    try:
        now = _now_utc()
        conn.execute(
            f"UPDATE sg_isr_requirements "  # nosec B608
            f"SET assigned_asset_id = {ph}, status = 'assigned', updated_at = {ph} "
            f"WHERE id = {ph}",
            (asset_id, now, requirement_id),
        )
        conn.commit()
        return {"requirement_id": requirement_id, "asset_id": asset_id, "status": "assigned"}
    except Exception as exc:
        logger.error("assign_asset failed: %s", exc)
        return {"error": str(exc)}
    finally:
        conn.close()


def update_requirement_status(req_id: str, status: str) -> bool:
    from tools.db.storage import get_connection, is_pg
    ph = "%s" if is_pg() else "?"
    conn = get_connection()
    try:
        conn.execute(
            f"UPDATE sg_isr_requirements SET status = {ph}, updated_at = {ph} WHERE id = {ph}",  # nosec B608
            (status, _now_utc(), req_id),
        )
        conn.commit()
        return True
    except Exception as exc:
        logger.error("update_requirement_status failed: %s", exc)
        return False
    finally:
        conn.close()


def delete_requirement(req_id: str) -> bool:
    from tools.db.storage import get_connection, is_pg
    ph = "%s" if is_pg() else "?"
    conn = get_connection()
    try:
        conn.execute(f"DELETE FROM sg_isr_requirements WHERE id = {ph}", (req_id,))  # nosec B608
        conn.commit()
        return True
    except Exception as exc:
        logger.error("delete_requirement failed: %s", exc)
        return False
    finally:
        conn.close()


# ── Assets ─────────────────────────────────────────────────────────────────────

def register_asset(
    asset_name: str,
    asset_type: str = "UAV",
    theater: str = "unspecified",
    notes: str = "",
    created_by: str = "analyst",
) -> dict[str, Any]:
    from tools.db.storage import get_connection
    conn = get_connection()
    try:
        asset_id = str(uuid.uuid4())
        now = _now_utc()
        caps = json.dumps(ASSET_CAPS_MAP.get(asset_type, []))
        conn.execute(
            "INSERT INTO sg_isr_assets "
            "(id, asset_name, asset_type, theater, collection_caps, status, notes, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, 'available', ?, ?, ?)",
            (asset_id, asset_name, asset_type, theater, caps, notes, now, now),
        )
        conn.commit()
        return {"asset_id": asset_id, "asset_name": asset_name, "asset_type": asset_type}
    finally:
        conn.close()


def list_assets(theater: str = "") -> list[dict]:
    from tools.db.storage import get_connection, is_pg
    ph = "%s" if is_pg() else "?"
    conn = get_connection()
    try:
        if theater:
            rows = conn.execute(
                f"SELECT id, asset_name, asset_type, theater, collection_caps, status, notes, created_at "  # nosec B608
                f"FROM sg_isr_assets WHERE theater = {ph} ORDER BY asset_type, asset_name",
                (theater,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id, asset_name, asset_type, theater, collection_caps, status, notes, created_at "  # nosec B608
                "FROM sg_isr_assets ORDER BY asset_type, asset_name"
            ).fetchall()
        cols = ("id", "asset_name", "asset_type", "theater",
                "collection_caps", "status", "notes", "created_at")
        result = []
        for r in rows:
            d = dict(zip(cols, r))
            try:
                d["collection_caps"] = json.loads(d["collection_caps"] or "[]")
            except Exception:
                d["collection_caps"] = []
            result.append(d)
        return result
    finally:
        conn.close()


def update_asset_status(asset_id: str, status: str) -> bool:
    from tools.db.storage import get_connection, is_pg
    ph = "%s" if is_pg() else "?"
    conn = get_connection()
    try:
        conn.execute(
            f"UPDATE sg_isr_assets SET status = {ph}, updated_at = {ph} WHERE id = {ph}",  # nosec B608
            (status, _now_utc(), asset_id),
        )
        conn.commit()
        return True
    except Exception as exc:
        logger.error("update_asset_status failed: %s", exc)
        return False
    finally:
        conn.close()


def delete_asset(asset_id: str) -> bool:
    from tools.db.storage import get_connection, is_pg
    ph = "%s" if is_pg() else "?"
    conn = get_connection()
    try:
        conn.execute(f"DELETE FROM sg_isr_assets WHERE id = {ph}", (asset_id,))  # nosec B608
        conn.commit()
        return True
    except Exception as exc:
        logger.error("delete_asset failed: %s", exc)
        return False
    finally:
        conn.close()


# ── Gap Analysis ───────────────────────────────────────────────────────────────

def gap_analysis(theater: str = "") -> dict[str, Any]:
    """Return requirements with no capable asset, and uncovered collection types."""
    reqs = list_requirements(theater=theater)
    assets = list_assets(theater=theater)

    all_caps: set[str] = set()
    for a in assets:
        if a["status"] in ("available", "tasked"):
            all_caps.update(a.get("collection_caps") or [])

    open_reqs = [r for r in reqs if r["status"] == "open"]
    covered_reqs = [r for r in reqs if r["status"] == "assigned"]
    uncovered = [r for r in open_reqs if r["collection_type"] not in all_caps]

    coverage_pct = 0.0
    if reqs:
        satisfied = len([r for r in reqs if r["status"] in ("assigned", "satisfied")])
        coverage_pct = round(satisfied / len(reqs) * 100, 1)

    return {
        "total_requirements": len(reqs),
        "open": len(open_reqs),
        "assigned": len(covered_reqs),
        "coverage_pct": coverage_pct,
        "uncovered_requirements": uncovered,
        "available_collection_caps": sorted(all_caps),
        "gaps": [r["collection_type"] for r in uncovered],
    }

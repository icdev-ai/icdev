# CUI // SP-CTI
"""METT-TC Worksheet — Mission Analysis Framework (FM 6-0 / MDMP Step 2).

Six factors: Mission, Enemy, Terrain & Weather, Troops Available,
Time Available, Civil Considerations.

Auto-population pulls from existing Strategos data tables when available.
"""
from __future__ import annotations
from tools.logging.icdev_logger import get_logger

import logging
import uuid
from datetime import datetime, timezone
from typing import Any

logger = get_logger("icdev.strategos.mett_tc")


def _now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_fetch(query: str, params: tuple = ()) -> list[dict]:
    from tools.db.storage import get_connection
    conn = get_connection()
    try:
        rows = conn.execute(query, params).fetchall()  # nosec B608
        if not rows:
            return []
        desc = [d[0] for d in conn.execute(query, params).description] if hasattr(conn, "description") else []
        return [dict(zip(desc, r)) if desc else {"value": r[0]} for r in rows]
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        return []
    finally:
        conn.close()


def auto_populate(theater: str = "unspecified") -> dict[str, str]:
    """Pull available data from Strategos tables to pre-fill METT-TC fields."""
    from tools.db.storage import get_connection, is_pg
    ph = "%s" if is_pg() else "?"
    conn = get_connection()
    result: dict[str, str] = {}

    # Enemy — recent SIGINT signals
    try:
        rows = conn.execute(
            f"SELECT signal_type, signal_summary FROM strategos_signals "  # nosec B608
            f"WHERE theater = {ph} ORDER BY detected_at DESC LIMIT 5",
            (theater,),
        ).fetchall()
        if rows:
            result["enemy_situation"] = "; ".join(
                f"{r[0]}: {r[1]}" for r in rows if r[1]
            )
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass

    # Troops available — ORBAT summary
    try:
        rows = conn.execute(
            "SELECT unit_name, strength, equipment FROM sg_orbat_strengths "  # nosec B608
            "ORDER BY updated_at DESC LIMIT 8"
        ).fetchall()
        if rows:
            result["troops_available"] = "; ".join(
                f"{r[0]} ({r[1] or '?'} pers{', ' + r[2] if r[2] else ''})" for r in rows
            )
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass

    # Red Cell COAs for enemy summary supplement
    try:
        rows = conn.execute(
            f"SELECT scenario, mlcoa_title, mdcoa_title FROM sg_red_cell_analyses "  # nosec B608
            f"WHERE theater = {ph} ORDER BY created_at DESC LIMIT 1",
            (theater,),
        ).fetchone()
        if rows and rows[1]:
            supplement = f"MLCOA: {rows[1]}"
            if rows[2]:
                supplement += f" | MDCOA: {rows[2]}"
            existing = result.get("enemy_situation", "")
            result["enemy_situation"] = (supplement + ". " + existing).strip()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass

    # I&W COA probabilities for enemy estimate
    try:
        rows = conn.execute(
            "SELECT coa_name, status FROM sg_iw_indicators "  # nosec B608
            "WHERE status = 'observed' ORDER BY weight DESC LIMIT 5"
        ).fetchall()
        if rows:
            iw_text = "Observed I&W: " + ", ".join(r[0] for r in rows if r[0])
            existing = result.get("enemy_situation", "")
            result["enemy_situation"] = (existing + " " + iw_text).strip() if existing else iw_text
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass

    conn.close()
    return result


def create_worksheet(
    theater: str = "unspecified",
    operation_name: str = "",
    mission: str = "",
    enemy_situation: str = "",
    terrain_weather: str = "",
    troops_available: str = "",
    time_available: str = "",
    civil_considerations: str = "",
    auto_populated: bool = False,
    created_by: str = "analyst",
) -> dict[str, Any]:
    from tools.db.storage import get_connection
    conn = get_connection()
    try:
        ws_id = str(uuid.uuid4())
        now = _now_utc()
        conn.execute(
            "INSERT INTO sg_mett_tc "
            "(id, theater, operation_name, mission, enemy_situation, terrain_weather, "
            " troops_available, time_available, civil_considerations, auto_populated, "
            " created_by, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (ws_id, theater, operation_name, mission, enemy_situation, terrain_weather,
             troops_available, time_available, civil_considerations,
             1 if auto_populated else 0, created_by, now, now),
        )
        conn.commit()
        return {"worksheet_id": ws_id, "theater": theater, "operation_name": operation_name}
    finally:
        conn.close()


def get_worksheet(ws_id: str) -> dict | None:
    from tools.db.storage import get_connection, is_pg
    ph = "%s" if is_pg() else "?"
    conn = get_connection()
    try:
        row = conn.execute(
            f"SELECT id, theater, operation_name, mission, enemy_situation, terrain_weather, "  # nosec B608
            f"troops_available, time_available, civil_considerations, auto_populated, "
            f"created_by, created_at, updated_at FROM sg_mett_tc WHERE id = {ph}",
            (ws_id,),
        ).fetchone()
        if not row:
            return None
        cols = ("id", "theater", "operation_name", "mission", "enemy_situation",
                "terrain_weather", "troops_available", "time_available",
                "civil_considerations", "auto_populated", "created_by",
                "created_at", "updated_at")
        return dict(zip(cols, row))
    finally:
        conn.close()


def list_worksheets(theater: str = "", limit: int = 20) -> list[dict]:
    from tools.db.storage import get_connection, is_pg
    ph = "%s" if is_pg() else "?"
    conn = get_connection()
    try:
        if theater:
            rows = conn.execute(
                f"SELECT id, theater, operation_name, created_by, created_at "  # nosec B608
                f"FROM sg_mett_tc WHERE theater = {ph} ORDER BY created_at DESC LIMIT {ph}",
                (theater, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id, theater, operation_name, created_by, created_at "  # nosec B608
                "FROM sg_mett_tc ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        cols = ("id", "theater", "operation_name", "created_by", "created_at")
        return [dict(zip(cols, r)) for r in rows]
    finally:
        conn.close()


def update_worksheet(ws_id: str, fields: dict) -> bool:
    allowed = {
        "mission", "enemy_situation", "terrain_weather",
        "troops_available", "time_available", "civil_considerations", "operation_name",
    }
    updates = {k: v for k, v in fields.items() if k in allowed}
    if not updates:
        return False
    from tools.db.storage import get_connection, is_pg
    ph = "%s" if is_pg() else "?"
    conn = get_connection()
    try:
        set_clause = ", ".join(f"{k} = {ph}" for k in updates)
        values = list(updates.values()) + [_now_utc(), ws_id]
        conn.execute(
            f"UPDATE sg_mett_tc SET {set_clause}, updated_at = {ph} WHERE id = {ph}",  # nosec B608
            values,
        )
        conn.commit()
        return True
    except Exception as exc:
        logger.error("update_worksheet failed: %s", exc)
        return False
    finally:
        conn.close()


def delete_worksheet(ws_id: str) -> bool:
    from tools.db.storage import get_connection, is_pg
    ph = "%s" if is_pg() else "?"
    conn = get_connection()
    try:
        conn.execute(f"DELETE FROM sg_mett_tc WHERE id = {ph}", (ws_id,))  # nosec B608
        conn.commit()
        return True
    except Exception as exc:
        logger.error("delete_worksheet failed: %s", exc)
        return False
    finally:
        conn.close()

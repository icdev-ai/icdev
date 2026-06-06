# CUI // SP-CTI
"""RTBH (Remotely Triggered Black Hole) routing manager — RFC 5635."""
from __future__ import annotations

from datetime import datetime, timezone


# ── Internal helpers ──────────────────────────────────────────────────────────

def _safe_fetch(conn, sql_pg: str, sql_sq: str, params: tuple = ()) -> list[dict]:
    """Execute a SELECT with PG-style params; fall back to SQLite-style on failure."""
    def _rows(cur) -> list[dict]:
        rows = cur.fetchall()
        if not rows:
            return []
        if hasattr(rows[0], "keys"):
            return [dict(r) for r in rows]
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in rows]

    try:
        cur = conn.cursor()
        cur.execute(sql_pg, params)
        return _rows(cur)
    except Exception:
        try:
            cur = conn.cursor()
            cur.execute(sql_sq, params)
            return _rows(cur)
        except Exception:
            return []


def _next_mitigation_number(conn) -> str:
    """Generate a sequential RTBH mitigation number: RTBH-YYYYMM-NNNN."""
    month_prefix = datetime.now(timezone.utc).strftime("RTBH-%Y%m-")
    # mitigation_number column does not exist on dsoc_rtbh_entries; use id-based fallback
    count_rows = _safe_fetch(
        conn,
        "SELECT COALESCE(MAX(id), 0) AS max_id FROM dsoc_rtbh_entries",
        "SELECT COALESCE(MAX(id), 0) AS max_id FROM dsoc_rtbh_entries",
        (),
    )
    max_id = count_rows[0].get("max_id", 0) if count_rows else 0
    seq = int(max_id) + 1
    return f"{month_prefix}{seq:04d}"


# ── Public API ────────────────────────────────────────────────────────────────

def trigger_rtbh(
    conn,
    prefix: str,
    reason: str,
    triggered_by: str = "system",
    auto_withdraw_minutes: int = 60,
) -> dict:
    """Insert a new RTBH entry and return the created record."""
    from tools.dsoc_canvas.constants import RTBH_NEXTHOP_DEFAULT, RTBH_COMMUNITY_DEFAULT

    now = datetime.now(timezone.utc).isoformat()
    sql_pg = """
        INSERT INTO dsoc_rtbh_entries
            (prefix, trigger_reason, triggered_by, status, community, nexthop,
             applied_routers, withdrawn_at, auto_withdraw_minutes, classification, created_at)
        VALUES (%s, %s, %s, 'active', %s, %s, '[]', '', %s, 'CUI', %s)
        RETURNING id
    """
    sql_sq = """
        INSERT INTO dsoc_rtbh_entries
            (prefix, trigger_reason, triggered_by, status, community, nexthop,
             applied_routers, withdrawn_at, auto_withdraw_minutes, classification, created_at)
        VALUES (?, ?, ?, 'active', ?, ?, '[]', '', ?, 'CUI', ?)
    """
    params = (prefix, reason, triggered_by, RTBH_COMMUNITY_DEFAULT, RTBH_NEXTHOP_DEFAULT,
              auto_withdraw_minutes, now)

    new_id: int | None = None
    try:
        cur = conn.cursor()
        cur.execute(sql_pg, params)
        row = cur.fetchone()
        if row:
            new_id = row[0] if not hasattr(row, "keys") else row["id"]
        conn.commit()
    except Exception:
        cur = conn.cursor()
        cur.execute(sql_sq, params)
        conn.commit()
        new_id = cur.lastrowid

    if new_id is None:
        return {}

    rows = _safe_fetch(
        conn,
        "SELECT * FROM dsoc_rtbh_entries WHERE id=%s",
        "SELECT * FROM dsoc_rtbh_entries WHERE id=?",
        (new_id,),
    )
    return rows[0] if rows else {}


def withdraw_rtbh(conn, rtbh_id: int) -> dict:
    """Set an RTBH entry to status='withdrawn' and record withdrawn_at."""
    now = datetime.now(timezone.utc).isoformat()
    sql_pg = "UPDATE dsoc_rtbh_entries SET status='withdrawn', withdrawn_at=%s WHERE id=%s"
    sql_sq = "UPDATE dsoc_rtbh_entries SET status='withdrawn', withdrawn_at=? WHERE id=?"
    try:
        conn.execute(sql_pg, (now, rtbh_id))
        conn.commit()
    except Exception:
        conn.execute(sql_sq, (now, rtbh_id))
        conn.commit()

    rows = _safe_fetch(
        conn,
        "SELECT * FROM dsoc_rtbh_entries WHERE id=%s",
        "SELECT * FROM dsoc_rtbh_entries WHERE id=?",
        (rtbh_id,),
    )
    return rows[0] if rows else {}


def get_active_blackholes(conn) -> list:
    """Return all RTBH entries with status='active'."""
    return _safe_fetch(
        conn,
        "SELECT * FROM dsoc_rtbh_entries WHERE status='active' ORDER BY created_at DESC",
        "SELECT * FROM dsoc_rtbh_entries WHERE status='active' ORDER BY created_at DESC",
    )


def auto_expire_rtbh(conn) -> int:
    """Withdraw entries whose auto_withdraw_minutes has elapsed. Returns count expired."""
    active = get_active_blackholes(conn)
    expired_count = 0
    now = datetime.now(timezone.utc)

    for entry in active:
        created_raw = entry.get("created_at", "")
        auto_mins = int(entry.get("auto_withdraw_minutes", 60))
        if not created_raw:
            continue
        try:
            # Parse ISO or SQLite TEXT timestamp
            created_raw_str = str(created_raw).replace(" ", "T")
            if created_raw_str.endswith("Z"):
                created_raw_str = created_raw_str[:-1] + "+00:00"
            if "+" not in created_raw_str and not created_raw_str.endswith("UTC"):
                created_raw_str += "+00:00"
            created_dt = datetime.fromisoformat(created_raw_str)
        except Exception:
            continue

        elapsed_minutes = (now - created_dt).total_seconds() / 60.0
        if elapsed_minutes >= auto_mins:
            withdraw_rtbh(conn, entry["id"])
            expired_count += 1

    return expired_count

# CUI // SP-CTI
"""CPMP Personnel Registry — CRUD and query functions.

Manages two tables:
  pma_personnel         — Program staff with clearance/credential/lcat/key-person flags.
  pma_credential_alerts — Dedup alert log with status lifecycle (open/acknowledged/resolved).

Extended columns beyond credential_monitor.py:
  pma_personnel:        key_person (0/1), lcat (TEXT), status ('active'/'inactive'),
                        clearance_level (TEXT)
  pma_credential_alerts: status ('open'/'acknowledged'/'resolved'), acknowledged_at (TEXT ISO)
"""

import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional


def _ensure_schema(conn) -> None:
    """Create or extend pma tables to include registry columns."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS pma_personnel (
            id               TEXT PRIMARY KEY,
            name             TEXT NOT NULL,
            role             TEXT,
            contract_id      TEXT,
            poly_expiry      TEXT,
            secret_expiry    TEXT,
            ts_expiry        TEXT,
            backup_person_id TEXT,
            key_person       INTEGER DEFAULT 0,
            lcat             TEXT,
            status           TEXT DEFAULT 'active',
            clearance_level  TEXT,
            created_at       TEXT,
            updated_at       TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS pma_credential_alerts (
            id               TEXT PRIMARY KEY,
            person_id        TEXT NOT NULL,
            alert_type       TEXT NOT NULL,
            expiry_date      TEXT NOT NULL,
            days_remaining   INTEGER NOT NULL,
            severity         TEXT NOT NULL,
            kanban_task_id   TEXT,
            status           TEXT DEFAULT 'open',
            acknowledged_at  TEXT,
            created_at       TEXT NOT NULL,
            UNIQUE (person_id, alert_type, expiry_date)
        )
    """)
    # Idempotent column additions for existing installs
    for col, defn in [
        ("key_person", "INTEGER DEFAULT 0"),
        ("lcat", "TEXT"),
        ("status", "TEXT DEFAULT 'active'"),
        ("clearance_level", "TEXT"),
    ]:
        try:
            conn.execute(f"ALTER TABLE pma_personnel ADD COLUMN {col} {defn}")
        except Exception:
            pass
    for col, defn in [
        ("status", "TEXT DEFAULT 'open'"),
        ("acknowledged_at", "TEXT"),
    ]:
        try:
            conn.execute(f"ALTER TABLE pma_credential_alerts ADD COLUMN {col} {defn}")
        except Exception:
            pass
    conn.commit()


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def upsert_person(contract_id: str, data: Dict[str, Any]) -> Dict[str, Any]:
    """Create or update a personnel record for a contract.

    If 'id' is provided and matches an existing row for the contract, performs
    UPDATE; otherwise inserts a new record with a generated UUID.

    Returns {"status": "ok", "person_id": <id>, "action": "created"|"updated"}.
    """
    from tools.db.storage import get_connection

    conn = get_connection()
    conn.set_security_context(None)  # rls-bypass: pma tables have no tenant_id/classification columns
    _ensure_schema(conn)
    try:
        person_id = data.get("id")
        now = _now_iso()

        if person_id:
            existing = conn.execute(
                "SELECT id FROM pma_personnel WHERE id = ? AND contract_id = ?",
                (person_id, contract_id),
            ).fetchone()
        else:
            existing = None
            person_id = str(uuid.uuid4())

        if existing:
            conn.execute(
                """UPDATE pma_personnel SET
                    name             = COALESCE(?, name),
                    role             = COALESCE(?, role),
                    poly_expiry      = COALESCE(?, poly_expiry),
                    secret_expiry    = COALESCE(?, secret_expiry),
                    ts_expiry        = COALESCE(?, ts_expiry),
                    backup_person_id = COALESCE(?, backup_person_id),
                    key_person       = COALESCE(?, key_person),
                    lcat             = COALESCE(?, lcat),
                    status           = COALESCE(?, status),
                    clearance_level  = COALESCE(?, clearance_level),
                    updated_at       = ?
                WHERE id = ?""",
                (
                    data.get("name"), data.get("role"),
                    data.get("poly_expiry"), data.get("secret_expiry"), data.get("ts_expiry"),
                    data.get("backup_person_id"),
                    1 if data.get("key_person") else None,
                    data.get("lcat"), data.get("status"), data.get("clearance_level"),
                    now, person_id,
                ),
            )
            action = "updated"
        else:
            name = data.get("name")
            if not name:
                return {"status": "error", "message": "name is required"}
            conn.execute(
                """INSERT INTO pma_personnel
                    (id, name, role, contract_id, poly_expiry, secret_expiry, ts_expiry,
                     backup_person_id, key_person, lcat, status, clearance_level,
                     created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    person_id, name, data.get("role"), contract_id,
                    data.get("poly_expiry"), data.get("secret_expiry"), data.get("ts_expiry"),
                    data.get("backup_person_id"),
                    1 if data.get("key_person") else 0,
                    data.get("lcat"), data.get("status", "active"), data.get("clearance_level"),
                    now, now,
                ),
            )
            action = "created"

        conn.commit()
        return {"status": "ok", "person_id": person_id, "action": action}
    finally:
        conn.close()


def list_personnel(
    contract_id: str,
    clearance_level: Optional[str] = None,
    lcat: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """List personnel for a contract with optional clearance_level/lcat filters."""
    from tools.db.storage import get_connection

    conn = get_connection()
    conn.set_security_context(None)  # rls-bypass: pma tables have no tenant_id/classification columns
    _ensure_schema(conn)
    try:
        sql = "SELECT * FROM pma_personnel WHERE contract_id = ?"
        params: List[Any] = [contract_id]
        if clearance_level:
            sql += " AND clearance_level = ?"
            params.append(clearance_level)
        if lcat:
            sql += " AND lcat = ?"
            params.append(lcat)
        sql += " ORDER BY name"
        rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def update_person(person_id: str, data: Dict[str, Any]) -> Dict[str, Any]:
    """Update a personnel record's status, backup assignment, or other fields."""
    from tools.db.storage import get_connection

    conn = get_connection()
    conn.set_security_context(None)  # rls-bypass: pma tables have no tenant_id/classification columns
    _ensure_schema(conn)
    try:
        existing = conn.execute(
            "SELECT id FROM pma_personnel WHERE id = ?", (person_id,)
        ).fetchone()
        if not existing:
            return {"status": "error", "message": f"Person {person_id} not found"}

        now = _now_iso()
        conn.execute(
            """UPDATE pma_personnel SET
                status           = COALESCE(?, status),
                backup_person_id = COALESCE(?, backup_person_id),
                role             = COALESCE(?, role),
                lcat             = COALESCE(?, lcat),
                clearance_level  = COALESCE(?, clearance_level),
                poly_expiry      = COALESCE(?, poly_expiry),
                secret_expiry    = COALESCE(?, secret_expiry),
                ts_expiry        = COALESCE(?, ts_expiry),
                key_person       = COALESCE(?, key_person),
                updated_at       = ?
            WHERE id = ?""",
            (
                data.get("status"), data.get("backup_person_id"),
                data.get("role"), data.get("lcat"), data.get("clearance_level"),
                data.get("poly_expiry"), data.get("secret_expiry"), data.get("ts_expiry"),
                1 if data.get("key_person") else None,
                now, person_id,
            ),
        )
        conn.commit()
        return {"status": "ok", "person_id": person_id}
    finally:
        conn.close()


def get_expiring_personnel(contract_id: str, days: int = 90) -> List[Dict[str, Any]]:
    """Return personnel for a contract with credentials expiring within *days* days.

    Each result includes the base personnel row plus an 'expiring_credentials' list
    with one entry per credential type nearing expiry.
    """
    from tools.db.storage import get_connection

    conn = get_connection()
    conn.set_security_context(None)  # rls-bypass: pma tables have no tenant_id/classification columns
    _ensure_schema(conn)
    try:
        today = date.today()
        cutoff = (today + timedelta(days=days)).isoformat()
        today_iso = today.isoformat()

        rows = conn.execute(
            "SELECT * FROM pma_personnel WHERE contract_id = ?", (contract_id,)
        ).fetchall()

        results: List[Dict[str, Any]] = []
        for row in rows:
            p = dict(row)
            expiring: List[Dict[str, Any]] = []
            for field in ("poly_expiry", "secret_expiry", "ts_expiry"):
                expiry_iso = p.get(field)
                if not expiry_iso:
                    continue
                if expiry_iso < today_iso or expiry_iso > cutoff:
                    continue
                try:
                    days_remaining = (date.fromisoformat(expiry_iso) - today).days
                except ValueError:
                    continue
                expiring.append(
                    {
                        "credential": field,
                        "expiry_date": expiry_iso,
                        "days_remaining": days_remaining,
                    }
                )
            if expiring:
                p["expiring_credentials"] = expiring
                results.append(p)
        return results
    finally:
        conn.close()


def get_key_persons(contract_id: str) -> List[Dict[str, Any]]:
    """Return personnel flagged as key_person=True for a contract."""
    from tools.db.storage import get_connection

    conn = get_connection()
    conn.set_security_context(None)  # rls-bypass: pma tables have no tenant_id/classification columns
    _ensure_schema(conn)
    try:
        rows = conn.execute(
            "SELECT * FROM pma_personnel WHERE contract_id = ? AND key_person = 1 ORDER BY name",
            (contract_id,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_personnel_alerts(contract_id: str) -> List[Dict[str, Any]]:
    """Return open credential alerts for all personnel on a contract."""
    from tools.db.storage import get_connection

    conn = get_connection()
    conn.set_security_context(None)  # rls-bypass: pma tables have no tenant_id/classification columns
    _ensure_schema(conn)
    try:
        rows = conn.execute(
            """SELECT a.*, p.name AS person_name, p.role AS person_role
               FROM pma_credential_alerts a
               JOIN pma_personnel p ON p.id = a.person_id
               WHERE p.contract_id = ?
                 AND (a.status IS NULL OR a.status = 'open')
               ORDER BY a.days_remaining ASC""",
            (contract_id,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def update_alert(alert_id: str, data: Dict[str, Any]) -> Dict[str, Any]:
    """Acknowledge or resolve a credential alert.

    Setting status='acknowledged' or 'resolved' stamps acknowledged_at with the
    current UTC timestamp.
    """
    from tools.db.storage import get_connection

    conn = get_connection()
    conn.set_security_context(None)  # rls-bypass: pma tables have no tenant_id/classification columns
    _ensure_schema(conn)
    try:
        existing = conn.execute(
            "SELECT id FROM pma_credential_alerts WHERE id = ?", (alert_id,)
        ).fetchone()
        if not existing:
            return {"status": "error", "message": f"Alert {alert_id} not found"}

        new_status = data.get("status")
        now = _now_iso()
        ack_at = now if new_status in ("acknowledged", "resolved") else None

        conn.execute(
            """UPDATE pma_credential_alerts SET
                status          = COALESCE(?, status),
                acknowledged_at = COALESCE(?, acknowledged_at)
            WHERE id = ?""",
            (new_status, ack_at, alert_id),
        )
        conn.commit()
        row = conn.execute(
            "SELECT status, acknowledged_at FROM pma_credential_alerts WHERE id = ?",
            (alert_id,),
        ).fetchone()
        result: Dict[str, Any] = {
            "status": "ok",
            "alert_id": alert_id,
            "new_status": row["status"] if row else new_status,
        }
        if row and row["acknowledged_at"]:
            result["acknowledged_at"] = row["acknowledged_at"]
        return result
    finally:
        conn.close()

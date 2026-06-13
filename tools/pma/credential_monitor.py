# CUI // SP-CTI
"""PMA Personnel Credential Monitor.

Provides data-access functions consumed by the Genesis pma_credential_monitor
reflex. Manages two tables:

  pma_personnel         — Program staff with clearance/poly credential dates.
  pma_credential_alerts — Dedup alert log (person_id + alert_type + expiry_date).

Alert types map to credential fields:
  poly_expiry     → polygraph expiration
  secret_expiry   → SECRET clearance expiration
  ts_expiry       → TS/SCI clearance expiration

Severity tiers:
  critical  — ≤ 30 days remaining
  warning   — ≤ 60 days remaining
  watch     — ≤ 90 days remaining

Key-person dependency: a pma_personnel row with backup_person_id IS NULL is
classified as a single-point-of-failure (SPOF) for staffing-risk purposes.
"""

import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.db.storage import get_connection  # noqa: E402 — after sys.path setup

_CREDENTIAL_FIELDS = [
    ("poly_expiry", "poly_expiry"),
    ("secret_expiry", "secret_expiry"),
    ("ts_expiry", "ts_expiry"),
]

_SEVERITY_THRESHOLDS = [
    (30, "critical"),
    (60, "warning"),
    (90, "watch"),
]


def _ensure_tables(conn) -> None:
    """Create PMA tables if they do not yet exist."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS pma_personnel (
            id               TEXT PRIMARY KEY,
            name             TEXT NOT NULL,
            role             TEXT,
            contract_id      TEXT,
            poly_expiry      TEXT,
            secret_expiry    TEXT,
            ts_expiry        TEXT,
            backup_person_id TEXT,
            created_at       TEXT,
            updated_at       TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS pma_credential_alerts (
            id             TEXT PRIMARY KEY,
            person_id      TEXT NOT NULL,
            alert_type     TEXT NOT NULL,
            expiry_date    TEXT NOT NULL,
            days_remaining INTEGER NOT NULL,
            severity       TEXT NOT NULL,
            kanban_task_id TEXT,
            created_at     TEXT NOT NULL,
            UNIQUE (person_id, alert_type, expiry_date)
        )
        """
    )
    conn.commit()


def _severity_for_days(days: int) -> Optional[str]:
    for threshold, label in _SEVERITY_THRESHOLDS:
        if days <= threshold:
            return label
    return None


def get_expiring_credentials(days: int = 90, conn=None) -> List[Dict[str, Any]]:
    """Return credential records expiring within *days* days.

    Each entry contains:
        person_id, person_name, alert_type, expiry_date,
        days_remaining, severity, contract_id
    """
    owns_conn = conn is None
    if owns_conn:
        conn = get_connection()
        conn.set_security_context(None)

    _ensure_tables(conn)

    today = date.today()
    cutoff = (today + timedelta(days=days)).isoformat()
    today_iso = today.isoformat()

    results: List[Dict[str, Any]] = []
    try:
        rows = conn.execute(
            "SELECT id, name, role, contract_id, poly_expiry, secret_expiry, ts_expiry "
            "FROM pma_personnel"
        ).fetchall()

        for row in rows:
            person = dict(row)
            for field, alert_type in _CREDENTIAL_FIELDS:
                expiry_iso = person.get(field)
                if not expiry_iso:
                    continue
                if expiry_iso < today_iso or expiry_iso > cutoff:
                    continue
                try:
                    expiry_date = date.fromisoformat(expiry_iso)
                except ValueError:
                    continue
                days_remaining = (expiry_date - today).days
                severity = _severity_for_days(days_remaining)
                if severity is None:
                    continue
                results.append(
                    {
                        "person_id": person["id"],
                        "person_name": person["name"],
                        "alert_type": alert_type,
                        "expiry_date": expiry_iso,
                        "days_remaining": days_remaining,
                        "severity": severity,
                        "contract_id": person.get("contract_id"),
                    }
                )
    finally:
        if owns_conn:
            conn.close()

    return results


def get_key_person_dependencies(conn=None) -> List[Dict[str, Any]]:
    """Return key personnel with no backup (single-point-of-failure).

    Each entry contains:
        person_id, person_name, role, contract_id, backup_person_id (None)
    """
    owns_conn = conn is None
    if owns_conn:
        conn = get_connection()
        conn.set_security_context(None)

    _ensure_tables(conn)

    results: List[Dict[str, Any]] = []
    try:
        rows = conn.execute(
            "SELECT id, name, role, contract_id, backup_person_id "
            "FROM pma_personnel "
            "WHERE backup_person_id IS NULL"
        ).fetchall()
        for row in rows:
            r = dict(row)
            results.append(
                {
                    "person_id": r["id"],
                    "person_name": r["name"],
                    "role": r.get("role"),
                    "contract_id": r.get("contract_id"),
                    "backup_person_id": None,
                }
            )
    finally:
        if owns_conn:
            conn.close()

    return results

#!/usr/bin/env python3
# CUI // SP-CTI
"""Genesis Reflex: PMA Credential Monitor.

Runs nightly. Two detection passes:

  1. Credential Expiry — calls get_expiring_credentials(days=90).
     Inserts pma_credential_alerts rows (dedup: person_id + alert_type + expiry_date).
     For CRITICAL expirations (≤ 30 days) seeds a kanban task:
       'URGENT: renew <type> for <name> by <date>'

  2. Key-Person Dependency — calls get_key_person_dependencies().
     For each person with no backup, seeds a cpmp_risks entry
     (category='staffing') if no open risk already exists for that person.
"""
IMPLEMENTATION_STATUS = "full"

import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.db.storage import get_connection  # noqa: E402 — after sys.path setup

_DISPATCH_SOURCE = "pma_credential_monitor"
_ALERT_TITLE_PREFIX = "URGENT: renew"


def run(config: Dict[str, Any], trust: Any) -> Dict[str, Any]:
    """Execute PMA credential monitor reflex."""
    results: Dict[str, Any] = {
        "credentials_checked": 0,
        "alerts_inserted": 0,
        "kanban_tasks_seeded": 0,
        "spof_persons_checked": 0,
        "risk_entries_created": 0,
        "errors": [],
    }

    try:
        from tools.pma.credential_monitor import (
            _ensure_tables,
            get_expiring_credentials,
            get_key_person_dependencies,
        )

        conn = get_connection()
        conn.set_security_context(None)  # rls-bypass: background reflex; pma tables have no tenant_id/classification

        _ensure_tables(conn)

        # ── Pass 1: Credential Expiry ──────────────────────────────────
        expiring = get_expiring_credentials(days=90, conn=conn)
        results["credentials_checked"] = len(expiring)

        now = datetime.now(timezone.utc).isoformat()

        for cred in expiring:
            person_id = cred["person_id"]
            person_name = cred["person_name"]
            alert_type = cred["alert_type"]
            expiry_date = cred["expiry_date"]
            days_remaining = cred["days_remaining"]
            severity = cred["severity"]

            # Dedup: skip if alert already exists for this person+type+expiry
            existing_alert = conn.execute(
                "SELECT id FROM pma_credential_alerts "
                "WHERE person_id = %s AND alert_type = %s AND expiry_date = %s",
                (person_id, alert_type, expiry_date),
            ).fetchone()

            if not existing_alert:
                alert_id = f"pma-alert-{uuid.uuid4().hex[:10]}"
                conn.execute(
                    """
                    INSERT INTO pma_credential_alerts
                        (id, person_id, alert_type, expiry_date, days_remaining,
                         severity, created_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    """,
                    (alert_id, person_id, alert_type, expiry_date, days_remaining, severity, now),
                )
                results["alerts_inserted"] += 1

                # Seed kanban task for CRITICAL expirations only
                if severity == "critical":
                    friendly_type = alert_type.replace("_expiry", "").upper()
                    title = (
                        f"{_ALERT_TITLE_PREFIX} {friendly_type} for {person_name} by {expiry_date}"
                    )
                    _seed_kanban_task(conn, title, cred, now, results)

        conn.commit()

        # ── Pass 2: Key-Person Dependency (SPOF) ──────────────────────
        spofs = get_key_person_dependencies(conn=conn)
        results["spof_persons_checked"] = len(spofs)

        for person in spofs:
            person_id = person["person_id"]
            person_name = person["person_name"]
            role = person.get("role") or "key personnel"
            contract_id = person.get("contract_id") or "PMA-PORTFOLIO"

            risk_title = f"SPOF: {person_name} ({role}) has no backup"

            # Check if an open staffing risk already exists for this person
            existing_risk = conn.execute(
                "SELECT id FROM cpmp_risks "
                "WHERE contract_id = %s AND title = %s AND status = 'open'",
                (contract_id, risk_title),
            ).fetchone()

            if not existing_risk:
                _seed_risk_entry(contract_id, risk_title, person_name, role, results)

        conn.commit()
        conn.close()

        _write_memory_log(results)
        return {"success": True, "metric_value": results["alerts_inserted"], "details": results}

    except Exception as exc:
        results["errors"].append(str(exc))
        return {"success": False, "metric_value": 0, "details": results, "error": str(exc)}


def _seed_kanban_task(conn, title: str, cred: Dict, now: str, results: Dict) -> None:
    """Insert a kanban task for a CRITICAL credential expiry. Skips duplicates."""
    existing = conn.execute(
        "SELECT id FROM kanban_tasks "
        "WHERE title = %s AND dispatch_source = %s AND status NOT IN ('done', 'dismissed')",
        (title[:120], _DISPATCH_SOURCE),
    ).fetchone()
    if existing:
        return

    task_id = f"pma-cred-{uuid.uuid4().hex[:10]}"
    description = (
        f"Credential: {cred['alert_type'].replace('_', ' ').title()}\n"
        f"Person: {cred['person_name']}\n"
        f"Expiry Date: {cred['expiry_date']}\n"
        f"Days Remaining: {cred['days_remaining']}\n"
        f"Severity: {cred['severity'].upper()}\n"
        f"Action: Initiate renewal process immediately to avoid access disruption."
    )
    tags = json.dumps(
        {
            "person_id": cred["person_id"],
            "alert_type": cred["alert_type"],
            "expiry_date": cred["expiry_date"],
            "days_remaining": cred["days_remaining"],
            "severity": cred["severity"],
            "contract_id": cred.get("contract_id"),
        }
    )
    conn.execute(
        """
        INSERT INTO kanban_tasks
            (id, task_type, title, description, status, priority,
             target_date, tags, dispatch_source, created_at, updated_at)
        VALUES (%s, %s, %s, %s, 'suggested', 'high', %s, %s, %s, %s, %s)
        """,
        (
            task_id,
            "chore",
            title[:120],
            description[:500],
            cred["expiry_date"],
            tags,
            _DISPATCH_SOURCE,
            now,
            now,
        ),
    )
    results["kanban_tasks_seeded"] += 1


def _seed_risk_entry(
    contract_id: str, title: str, person_name: str, role: str, results: Dict
) -> None:
    """Create a staffing risk entry in cpmp_risks. Best-effort; errors logged."""
    try:
        from tools.govcon.risk_manager import create_risk
        resp = create_risk(
            {
                "contract_id": contract_id,
                "title": title,
                "category": "staffing",
                "probability": 4,
                "impact": 4,
                "status": "open",
                "mitigation": None,
                "owner": None,
                "metadata": {
                    "person_name": person_name,
                    "role": role,
                    "source": _DISPATCH_SOURCE,
                },
            }
        )
        if resp.get("status") == "ok":
            results["risk_entries_created"] += 1
    except Exception as exc:
        results["errors"].append(f"Risk entry failed for {person_name}: {exc}")


def _write_memory_log(results: Dict) -> None:
    try:
        from tools.memory.memory_write import write_memory
        write_memory(
            content=(
                f"PMA Credential Monitor: {results['credentials_checked']} credentials checked, "
                f"{results['alerts_inserted']} alerts inserted, "
                f"{results['kanban_tasks_seeded']} kanban tasks seeded, "
                f"{results['spof_persons_checked']} SPOF persons checked, "
                f"{results['risk_entries_created']} risk entries created."
            ),
            memory_type="event",
        )
    except Exception:
        pass


if __name__ == "__main__":
    # Load THIS repo's .env so a direct CLI run uses the same board/PG config as the
    # GenesisDaemon. override=True: a pip-installed ICDEV in site-packages may have
    # already loaded a different checkout's .env at import. Repo root via __file__, not cwd.
    try:
        from pathlib import Path as _EnvPath
        from dotenv import load_dotenv as _load_dotenv
        _load_dotenv(_EnvPath(__file__).resolve().parents[3] / ".env", override=True)
    except ImportError:
        pass
    result = run({}, None)
    print(json.dumps(result, indent=2, default=str))

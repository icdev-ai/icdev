# CUI // SP-CTI
"""BDC ISA expiry alerting — 90-day warning events, 30-day Telegram notifications.

check_isa_expiry() is the main entry point. It:
  1. Ensures isa_expiry_date column exists on bd_isa_tracker (add if missing).
  2. Queries ISAs expiring within 90 days.
  3. Publishes a 'bdc' / 'isa_expiring_soon' canvas event for each.
  4. Sends a Telegram notification for ISAs expiring within 30 days.

Returns a summary dict: isas_checked, events_published, notifications_sent, dry_run.
"""
from __future__ import annotations
import sys
from pathlib import Path

# kax-conflict-05: run by path, sys.path[0] is this file's own directory — never
# the import root. Bootstrap it before the first first-party import below.
# parents[N] is whatever holds this file's `tools` package: the repo root in
# tools/, and <repo>/icdev in the icdev/ mirror (which is what a wheel ships).
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tools.logging.icdev_logger import get_logger

from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict

logger = get_logger(__name__)

_WARN_DAYS = 90
_NOTIFY_DAYS = 30


def ensure_isa_expiry_column() -> None:
    """Add isa_expiry_date TEXT column to bd_isa_tracker if absent (idempotent)."""
    from tools.boundary_canvas.db.init_db import get_connection as bdc_conn

    conn = bdc_conn()
    try:
        try:
            conn.execute(
                "ALTER TABLE bd_isa_tracker ADD COLUMN isa_expiry_date TEXT"
            )
            conn.commit()
            logger.info("isa_expiry_date column added to bd_isa_tracker")
        except Exception:
            pass  # Column already exists — safe to ignore
    finally:
        conn.close()


def _parse_expiry(expiry_str: str) -> date | None:
    """Return a date from ISO/date string, or None on failure."""
    if not expiry_str:
        return None
    try:
        return date.fromisoformat(expiry_str[:10])
    except ValueError:
        return None


def check_isa_expiry(*, dry_run: bool = False) -> Dict[str, Any]:
    """Check ISA expiry dates and emit alerts.

    - ISAs expiring within 90 days → canvas event publish('bdc', 'isa_expiring_soon', ...)
    - ISAs expiring within 30 days → Telegram notification (in addition to canvas event)

    Args:
        dry_run: If True, query and count but do not publish events or send notifications.

    Returns:
        {"isas_checked": int, "events_published": int, "notifications_sent": int, "dry_run": bool}
    """
    ensure_isa_expiry_column()

    from tools.boundary_canvas.db.init_db import get_connection as bdc_conn

    today = datetime.now(timezone.utc).date()
    cutoff = (today + timedelta(days=_WARN_DAYS)).isoformat()

    conn = bdc_conn()
    try:
        rows = conn.execute(
            """
            SELECT id, design_id, interconnection_id, isa_doc_id, status, owner,
                   COALESCE(isa_expiry_date, expiry_date) AS expiry
            FROM bd_isa_tracker
            WHERE COALESCE(isa_expiry_date, expiry_date) IS NOT NULL
              AND COALESCE(isa_expiry_date, expiry_date) <= %s
              AND status NOT IN ('terminated', 'expired')
            ORDER BY expiry ASC
            """,
            (cutoff,),
        ).fetchall()
    finally:
        conn.close()

    events_published = 0
    notifications_sent = 0

    for row in rows:
        if isinstance(row, (list, tuple)):
            isa_id, design_id, interconnection_id, isa_doc_id, status, owner, expiry_str = row
        else:
            isa_id = row["id"]
            design_id = row["design_id"]
            interconnection_id = row["interconnection_id"]
            isa_doc_id = row["isa_doc_id"]
            status = row["status"]
            owner = row["owner"]
            expiry_str = row["expiry"]

        expiry_date = _parse_expiry(expiry_str)
        if expiry_date is None:
            continue

        days_left = (expiry_date - today).days

        payload: Dict[str, Any] = {
            "isa_id": isa_id,
            "design_id": design_id,
            "interconnection_id": interconnection_id,
            "isa_doc_id": isa_doc_id,
            "status": status,
            "owner": owner or "",
            "expiry_date": expiry_str,
            "days_until_expiry": days_left,
        }

        if dry_run:
            continue

        # Publish canvas event for all ISAs within 90-day window
        try:
            from tools.canvas.event_bus import publish
            publish("bdc", "isa_expiring_soon", payload)
            events_published += 1
        except Exception as exc:
            logger.warning("canvas event publish failed for ISA %s: %s", isa_id, exc)

        # Telegram notification for ISAs within 30-day window
        if days_left <= _NOTIFY_DAYS:
            try:
                from tools.notifications.adapters.telegram import send as tg_send
                severity = "critical" if days_left <= 7 else "warning"
                label = isa_doc_id or isa_id
                tg_send(
                    title="BDC ISA Expiry Warning",
                    body=(
                        f"ISA {label} expires in {days_left} day(s) ({expiry_str}). "
                        f"Interconnection: {interconnection_id}. "
                        f"Owner: {owner or 'unassigned'}."
                    ),
                    severity=severity,
                )
                notifications_sent += 1
            except Exception as exc:
                logger.warning("Telegram notification failed for ISA %s: %s", isa_id, exc)

    return {
        "isas_checked": len(rows),
        "events_published": events_published,
        "notifications_sent": notifications_sent,
        "dry_run": dry_run,
    }


if __name__ == "__main__":
    import json as _json
    result = check_isa_expiry()
    print(_json.dumps(result, indent=2))

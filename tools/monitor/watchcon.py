#!/usr/bin/env python3

from tools.logging.icdev_logger import get_logger
# CUI // SP-CTI
"""WATCHCON alert tier support.

Three tiers:
  WATCHCON 4 — Routine   (severity: info)
  WATCHCON 3 — Elevated  (severity: warning)
  WATCHCON 2 — High      (severity: critical)
"""

import argparse
import json
import os
from pathlib import Path

logger = get_logger(__name__)

from tools.db.storage import column_exists, get_connection
from tools.monitor.constants import (
    WATCHCON_LABELS,
    WATCHCON_TIERS,
    WATCHCON_TO_SEVERITY,
    SEVERITY_TO_WATCHCON,
)

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = BASE_DIR / "data" / "icdev.db"


# ---------------------------------------------------------------------------
# Classification helpers
# ---------------------------------------------------------------------------

def classify_tier(severity: str) -> int:
    """Return the WATCHCON tier integer for a given severity string.

    Unknown severities default to WATCHCON 4 (routine).
    """
    return SEVERITY_TO_WATCHCON.get(severity.lower(), 4)


def tier_label(tier: int) -> str:
    """Human-readable label for a WATCHCON tier."""
    return WATCHCON_LABELS.get(tier, "unknown")


def tier_severity(tier: int) -> str:
    """Canonical severity string for a WATCHCON tier."""
    return WATCHCON_TO_SEVERITY.get(tier, "info")


# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------

def _ensure_column(conn) -> None:
    """Add watchcon_tier column to alerts table if absent (idempotent)."""
    # Backend-aware column probe (pgrt-sweep-06) — no PRAGMA/translation reliance.
    if not column_exists(conn, "alerts", "watchcon_tier"):
        conn.execute(
            "ALTER TABLE alerts ADD COLUMN watchcon_tier INTEGER DEFAULT 4 "
            "CHECK(watchcon_tier IN (2, 3, 4))"
        )
        conn.commit()


def backfill_tiers(db_path: Path = None) -> int:
    """Populate watchcon_tier for existing alerts rows that have NULL or default.

    Returns the number of rows updated.
    """
    conn = get_connection(db_path=str(db_path or DB_PATH))
    _ensure_column(conn)
    rows = conn.execute(
        "SELECT id, severity FROM alerts WHERE watchcon_tier IS NULL OR watchcon_tier = 4"
    ).fetchall()
    updated = 0
    for row_id, severity in rows:
        tier = classify_tier(severity or "info")
        conn.execute(
            "UPDATE alerts SET watchcon_tier = %s WHERE id = %s", (tier, row_id)
        )
        updated += 1
    conn.commit()
    return updated


def get_alerts_by_tier(tier: int, db_path: Path = None, limit: int = 100) -> list:
    """Fetch alerts for a specific WATCHCON tier."""
    if tier not in WATCHCON_TIERS:
        raise ValueError(f"Invalid WATCHCON tier: {tier}. Valid values: {WATCHCON_TIERS}")
    conn = get_connection(db_path=str(db_path or DB_PATH))
    _ensure_column(conn)
    rows = conn.execute(
        "SELECT id, project_id, severity, source, title, description, status, "
        "watchcon_tier, created_at "
        "FROM alerts WHERE watchcon_tier = %s ORDER BY created_at DESC LIMIT %s",
        (tier, limit),
    ).fetchall()
    cols = ["id", "project_id", "severity", "source", "title", "description",
            "status", "watchcon_tier", "created_at"]
    return [dict(zip(cols, r)) for r in rows]


def insert_alert(
    severity: str,
    source: str,
    title: str,
    description: str = "",
    project_id: str = None,
    db_path: Path = None,
) -> dict:
    """Insert an alert and auto-assign its WATCHCON tier.

    Returns the inserted row as a dict with watchcon_tier populated.
    """
    tier = classify_tier(severity)
    conn = get_connection(db_path=str(db_path or DB_PATH))
    _ensure_column(conn)
    cur = conn.execute(
        "INSERT INTO alerts (project_id, severity, source, title, description, watchcon_tier) "
        "VALUES (%s, %s, %s, %s, %s, %s)",
        (project_id, severity.lower(), source, title, description, tier),
    )
    conn.commit()

    alert = {
        "id": cur.lastrowid,
        "project_id": project_id,
        "severity": severity.lower(),
        "source": source,
        "title": title,
        "description": description,
        "watchcon_tier": tier,
        "watchcon_label": tier_label(tier),
    }

    siem_endpoint = os.getenv("SIEM_ENDPOINT", "")
    if siem_endpoint:
        try:
            from tools.siem_alert_forwarder import forward_alert
            forward_alert(
                alert_payload={
                    "title": title,
                    "severity": severity.lower(),
                    "source": source,
                    "description": description,
                    "project_id": project_id,
                    "watchcon_tier": tier,
                },
                siem_endpoint=siem_endpoint,
                siem_token=os.getenv("SIEM_TOKEN", ""),
                db_path=str(db_path or DB_PATH),
            )
        except Exception as exc:
            logger.warning("SIEM forwarding failed for alert '%s': %s", title, exc)

    return alert


def tier_summary(db_path: Path = None) -> dict:
    """Return counts of alerts grouped by WATCHCON tier."""
    conn = get_connection(db_path=str(db_path or DB_PATH))
    _ensure_column(conn)
    rows = conn.execute(
        "SELECT watchcon_tier, COUNT(*) FROM alerts GROUP BY watchcon_tier"
    ).fetchall()
    summary = {t: 0 for t in WATCHCON_TIERS}
    for tier, count in rows:
        if tier in summary:
            summary[tier] = count
    return {
        "tiers": {
            str(t): {
                "count": summary[t],
                "label": tier_label(t),
                "severity": tier_severity(t),
            }
            for t in WATCHCON_TIERS
        }
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="WATCHCON alert tier management")
    sub = p.add_subparsers(dest="command")

    summary_p = sub.add_parser("summary", help="Show alert counts per tier")
    summary_p.add_argument("--json", action="store_true")

    get_p = sub.add_parser("get", help="Get alerts for a tier")
    get_p.add_argument("tier", type=int, choices=list(WATCHCON_TIERS))
    get_p.add_argument("--limit", type=int, default=20)
    get_p.add_argument("--json", action="store_true")

    backfill_p = sub.add_parser("backfill", help="Backfill watchcon_tier for existing rows")
    backfill_p.add_argument("--json", action="store_true")

    return p


def main() -> None:
    args = _build_parser().parse_args()
    if args.command == "summary":
        result = tier_summary()
        print(json.dumps(result, indent=2) if args.json else result)
    elif args.command == "get":
        alerts = get_alerts_by_tier(args.tier, limit=args.limit)
        print(json.dumps(alerts, indent=2) if args.json else alerts)
    elif args.command == "backfill":
        updated = backfill_tiers()
        result = {"updated": updated}
        print(json.dumps(result, indent=2) if args.json else result)
    else:
        _build_parser().print_help()


if __name__ == "__main__":
    main()

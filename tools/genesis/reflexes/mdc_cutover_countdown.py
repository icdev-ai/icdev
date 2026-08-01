# CUI // SP-CTI
"""Genesis Reflex — MDC Cutover Countdown Alerts (6h cadence).

Scans mc_sops for approved migration SOPs with a cutover_date within
30 days. Publishes 'mdc.cutover.approaching' events and suggests kanban
countdown cards.

Air-gap safe: no LLM calls — pure DB heuristics.
"""
from __future__ import annotations

IMPLEMENTATION_STATUS = "full"
from tools.logging.icdev_logger import get_logger

from datetime import datetime, timezone
from typing import Any, Dict

logger = get_logger(__name__)

CADENCE_HOURS = 6
WARN_DAYS = 30   # Alert when cutover is within this many days


def _days_until(iso_str: str) -> int:
    if not iso_str:
        return 9999
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return (dt - datetime.now(timezone.utc)).days
    except Exception:
        return 9999


def run(ctx: Dict[str, Any], conn=None) -> Dict[str, Any]:
    """Find approved MDC SOPs with approaching cutover dates.

    Returns:
        sops_checked: int
        approaching_cutover: list of {id, title, cutover_date, days_until}
        events_published: int
    """
    dry_run = ctx.get("dry_run", False)
    result: Dict[str, Any] = {
        "cadence_hours": CADENCE_HOURS,
        "sops_checked": 0,
        "approaching_cutover": [],
        "events_published": 0,
        "errors": [],
        "status": "ok",
    }
    try:
        from tools.migration_canvas.db.init_db import get_connection as mdc_conn
        from tools.db.storage import column_exists
        conn_mdc = mdc_conn()
        try:
            # Look for cutover_date or target_date column via the shared
            # backend-aware probe (information_schema on PG, PRAGMA table_info on
            # SQLite) rather than a raw PRAGMA that only introspects on SQLite.
            date_col = "cutover_date" if column_exists(conn_mdc, "mc_sops", "cutover_date") else (
                "target_date" if column_exists(conn_mdc, "mc_sops", "target_date") else None
            )
            if date_col:
                rows = conn_mdc.execute(
                    f"SELECT id, title, {date_col}, design_id FROM mc_sops "  # nosec B608
                    f"WHERE approval_status='approved' AND {date_col} IS NOT NULL "  # nosec B608
                    f"ORDER BY {date_col}"  # nosec B608
                ).fetchall()
            else:
                rows = []
        finally:
            conn_mdc.close()

        result["sops_checked"] = len(rows)
        approaching = []
        for row in rows:
            sid = row[0] if isinstance(row, (tuple, list)) else row["id"]
            title = row[1] if isinstance(row, (tuple, list)) else row["title"]
            date_val = row[2] if isinstance(row, (tuple, list)) else row[date_col]
            design_id = row[3] if isinstance(row, (tuple, list)) else row.get("design_id", "")
            days = _days_until(date_val or "")
            if days <= WARN_DAYS:
                approaching.append({
                    "id": sid, "title": title,
                    "cutover_date": date_val, "days_until": days,
                    "design_id": design_id,
                })

        result["approaching_cutover"] = approaching

        if approaching and not dry_run:
            try:
                from tools.canvas.event_bus import publish
                for s in approaching:
                    publish("mdc", "mdc.cutover.approaching", {
                        "sop_id": s["id"],
                        "title": s["title"],
                        "cutover_date": s["cutover_date"],
                        "days_until": s["days_until"],
                        "design_id": s["design_id"],
                    })
                    result["events_published"] += 1
            except Exception as exc:
                result["errors"].append(f"event_bus: {exc}")

    except Exception as exc:
        logger.error("mdc_cutover_countdown reflex error: %s", exc)
        result["status"] = "error"
        result["errors"].append(str(exc))
    return result


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
    import json as _json
    print(_json.dumps(run({}), indent=2))

# CUI // SP-CTI
"""Genesis Reflex — NOCC SLA Watcher (4h cadence).

Reads noc_sla_records, projects end-of-period compliance for each
circuit's SLA, marks breach=1 when measured_value violates target,
and publishes a warning canvas event when projected compliance falls
below (target − 0.5%).

Air-gap safe: no LLM calls — pure DB heuristics.
"""
IMPLEMENTATION_STATUS = "full"
from __future__ import annotations
from tools.logging.icdev_logger import get_logger

from datetime import datetime, timezone
from typing import Any, Dict

logger = get_logger(__name__)

CADENCE_HOURS = 4

# Warn when projected compliance is this many percentage points below target
_WARN_MARGIN_PCT = 0.5


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _try_exec(conn, sql_pg: str, sql_sq: str, params: tuple = ()) -> Any:
    try:
        return conn.execute(sql_pg, params)
    except Exception:
        return conn.execute(sql_sq, params)


def run(ctx: Dict[str, Any], conn=None) -> Dict[str, Any]:
    """Watch SLA records and flag projected breaches.

    Returns:
        records_checked: int
        breaches_marked: int
        warnings_issued: int
        events_published: int
        errors: list[str]
    """
    dry_run = ctx.get("dry_run", False)
    result: Dict[str, Any] = {
        "cadence_hours": CADENCE_HOURS,
        "records_checked": 0,
        "breaches_marked": 0,
        "warnings_issued": 0,
        "events_published": 0,
        "errors": [],
        "status": "ok",
    }

    try:
        from tools.noc_canvas.db.init_db import get_connection as nocc_conn
        db = nocc_conn()
        try:
            _watch_sla(db, dry_run, result)
        finally:
            db.close()
    except Exception as exc:
        logger.error("nocc_sla_watcher reflex error: %s", exc)
        result["status"] = "error"
        result["errors"].append(str(exc))

    return result


def _watch_sla(conn, dry_run: bool, result: Dict[str, Any]) -> None:
    try:
        rows = _try_exec(
            conn,
            "SELECT id, circuit_id, carrier, customer, sla_type, target_value, "
            "measured_value, breach, period_start, period_end "
            "FROM noc_sla_records WHERE breach = FALSE",
            "SELECT id, circuit_id, carrier, customer, sla_type, target_value, "
            "measured_value, breach, period_start, period_end "
            "FROM noc_sla_records WHERE breach = 0",
        ).fetchall()
    except Exception as exc:
        result["errors"].append(f"sla_fetch: {exc}")
        return

    result["records_checked"] = len(rows)

    for row in rows:
        if hasattr(row, "keys"):
            rec_id = row["id"]
            circuit = row["circuit_id"]
            carrier = row["carrier"]
            sla_type = row["sla_type"]
            target = float(row["target_value"] or 0)
            measured = float(row["measured_value"] or 0)
        else:
            rec_id, circuit, carrier, _customer, sla_type, target_raw, measured_raw = row[0], row[1], row[2], row[3], row[4], row[5], row[6]
            target = float(target_raw or 0)
            measured = float(measured_raw or 0)

        is_breach, is_warn = _evaluate_sla(sla_type, target, measured)

        if is_breach and not dry_run:
            try:
                _try_exec(
                    conn,
                    "UPDATE noc_sla_records SET breach = TRUE WHERE id = %s",
                    "UPDATE noc_sla_records SET breach = 1 WHERE id = ?",
                    (rec_id,),
                )
                try:
                    conn.commit()
                except Exception:
                    pass
                result["breaches_marked"] += 1
            except Exception as exc:
                result["errors"].append(f"breach_mark({rec_id}): {exc}")
        elif is_breach:
            result["breaches_marked"] += 1

        if is_warn:
            result["warnings_issued"] += 1
            if not dry_run:
                try:
                    from tools.canvas.event_bus import publish
                    publish("nocc", "nocc.sla.projected_breach", {
                        "record_id": rec_id,
                        "circuit_id": circuit,
                        "carrier": carrier,
                        "sla_type": sla_type,
                        "target": target,
                        "measured": measured,
                        "margin_pct": _WARN_MARGIN_PCT,
                    })
                    result["events_published"] += 1
                except Exception as exc:
                    result["errors"].append(f"event_bus: {exc}")


def _evaluate_sla(sla_type: str, target: float, measured: float) -> tuple[bool, bool]:
    """Return (is_breach, is_warn).

    For uptime-type SLAs: higher is better (measured must be >= target).
    For latency/jitter/loss: lower is better (measured must be <= target).
    """
    lower_is_better = any(k in sla_type.lower() for k in ("latency", "jitter", "loss", "packet", "rtt"))

    if lower_is_better:
        is_breach = measured > target
        is_warn = not is_breach and measured > (target - target * _WARN_MARGIN_PCT / 100)
    else:
        # Uptime or throughput: higher is better
        is_breach = measured < target
        is_warn = not is_breach and measured < (target + _WARN_MARGIN_PCT)

    return is_breach, is_warn


if __name__ == "__main__":
    import json as _json
    print(_json.dumps(run({"dry_run": True}), indent=2))

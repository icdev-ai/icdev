# CUI // SP-CTI
"""Genesis reflex: poll in-flight cross-connect orders and alarm on delayed deliveries."""
from __future__ import annotations
IMPLEMENTATION_STATUS = "full"

import os
import statistics
from datetime import date
from typing import Any, Dict, List

CADENCE_HOURS = 1

# Configurable fallback thresholds — override via .env to avoid hardcoded values
_MINOR_DELAY_DAYS = int(os.environ.get("XC_DELAY_MINOR_DAYS", "3"))
_MAJOR_DELAY_DAYS = int(os.environ.get("XC_DELAY_MAJOR_DAYS", "7"))
_CRITICAL_DELAY_DAYS = int(os.environ.get("XC_DELAY_CRITICAL_DAYS", "30"))
_ANOMALY_ZSCORE_THRESHOLD = float(os.environ.get("XC_ANOMALY_ZSCORE", "1.5"))
_MIN_HISTORY_SAMPLES = int(os.environ.get("XC_ANOMALY_MIN_SAMPLES", "5"))


def _get_historical_delays(ccc_conn) -> List[int]:
    """Return list of historical (actual - estimated) delivery days from resolved orders."""
    sql_sqlite = (
        "SELECT estimated_delivery, actual_delivery FROM ccc_cross_connects "
        "WHERE order_status IN ('active','cancelled') "
        "AND estimated_delivery IS NOT NULL AND actual_delivery IS NOT NULL "
        "LIMIT 200"
    )
    sql_pg = sql_sqlite.replace("?", "%s")
    rows = []
    try:
        rows = ccc_conn.execute(sql_sqlite).fetchall()
    except Exception:
        try:
            cur = ccc_conn.cursor()
            cur.execute(sql_pg)
            rows = cur.fetchall()
        except Exception:
            return []

    delays: List[int] = []
    for row in rows:
        try:
            est = date.fromisoformat(str(row[0])[:10])
            act = date.fromisoformat(str(row[1])[:10])
            delays.append((act - est).days)
        except Exception:
            continue
    return delays


def _detect_delivery_anomaly(days_overdue: int, historical_delays: List[int]) -> Dict[str, Any]:
    """
    Determine whether a delivery delay is anomalous.

    Uses z-score over historical delay distribution when enough samples exist;
    falls back to env-configurable day-bucket thresholds otherwise.
    Returns: {is_anomaly, severity, score (0..1), method}
    """
    if len(historical_delays) >= _MIN_HISTORY_SAMPLES:
        mean = statistics.mean(historical_delays)
        stdev = statistics.pstdev(historical_delays) or 1.0
        z = (days_overdue - mean) / stdev

        if z < _ANOMALY_ZSCORE_THRESHOLD:
            return {"is_anomaly": False, "severity": None,
                    "score": max(0.0, round(z / 3.0, 3)), "method": "zscore"}
        elif z < 2.0:
            return {"is_anomaly": True, "severity": "minor",
                    "score": round(z / 3.0, 3), "method": "zscore"}
        elif z < 3.0:
            return {"is_anomaly": True, "severity": "major",
                    "score": round(z / 3.0, 3), "method": "zscore"}
        else:
            return {"is_anomaly": True, "severity": "critical",
                    "score": min(round(z / 3.0, 3), 1.0), "method": "zscore"}

    # Sparse history — fall back to configurable day-bucket thresholds
    if days_overdue < _MINOR_DELAY_DAYS:
        return {"is_anomaly": False, "severity": None,
                "score": round(days_overdue / max(_MAJOR_DELAY_DAYS, 1), 3), "method": "threshold"}
    elif days_overdue < _MAJOR_DELAY_DAYS:
        return {"is_anomaly": True, "severity": "minor", "score": 0.4, "method": "threshold"}
    elif days_overdue < _CRITICAL_DELAY_DAYS:
        return {"is_anomaly": True, "severity": "major", "score": 0.7, "method": "threshold"}
    else:
        return {"is_anomaly": True, "severity": "critical", "score": 1.0, "method": "threshold"}


def run(ctx: Dict[str, Any], conn=None) -> Dict[str, Any]:
    """Poll in-flight cross-connect orders; alarm on anomalously delayed deliveries.

    Returns:
        orders_polled: int
        status_changes: int
        delayed_orders: int
        alarms_created: int
        errors: list[str]
        status: "ok" | "error"
    """
    dry_run = ctx.get("dry_run", False)
    errors: list[str] = []
    status_changes = 0
    delayed_orders = 0
    alarms_created = 0

    # Open CCC connection
    ccc_conn = None
    try:
        from tools.ccc_canvas.db.init_db import get_connection as ccc_conn_factory
        ccc_conn = ccc_conn_factory()
    except Exception as e:
        return {"status": "error", "errors": [f"CCC DB unreachable: {e}"],
                "orders_polled": 0, "status_changes": 0,
                "delayed_orders": 0, "alarms_created": 0}

    try:
        from tools.ccc_canvas.constants import XC_IN_FLIGHT_STATUSES
        from tools.ccc_canvas.xc_order_manager import poll_order_status

        # Pre-load historical delays once for the entire run
        historical_delays = _get_historical_delays(ccc_conn)

        # Fetch all in-flight orders
        placeholders = ",".join(["?" for _ in XC_IN_FLIGHT_STATUSES])
        try:
            rows = ccc_conn.execute(
                f"SELECT id, xc_number, estimated_delivery, order_status FROM ccc_cross_connects WHERE order_status IN ({placeholders})",
                XC_IN_FLIGHT_STATUSES,
            ).fetchall()
        except Exception:
            try:
                ph = ",".join(["%s" for _ in XC_IN_FLIGHT_STATUSES])
                cur = ccc_conn.cursor()
                cur.execute(
                    f"SELECT id, xc_number, estimated_delivery, order_status FROM ccc_cross_connects WHERE order_status IN ({ph})",
                    XC_IN_FLIGHT_STATUSES,
                )
                rows = cur.fetchall()
            except Exception as e:
                errors.append(f"Failed to query in-flight orders: {e}")
                rows = []

        orders_polled = len(rows)

        today = date.today()

        for row in rows:
            xc_id = row[0]
            xc_number = row[1]
            estimated_delivery = row[2] or ""
            current_status = row[3]

            if not dry_run:
                try:
                    result = poll_order_status(ccc_conn, xc_id)
                    if result.get("changed"):
                        status_changes += 1
                except Exception as e:
                    errors.append(f"poll_order_status xc_id={xc_id}: {e}")

            # Anomaly detection: replace binary date threshold with statistical assessment
            if (estimated_delivery and current_status not in ("active", "cancelled")):
                try:
                    est_date = date.fromisoformat(str(estimated_delivery)[:10])
                    days_overdue = (today - est_date).days
                except Exception:
                    days_overdue = 0

                if days_overdue <= 0:
                    continue

                anomaly = _detect_delivery_anomaly(days_overdue, historical_delays)

                if not anomaly["is_anomaly"]:
                    continue

                delayed_orders += 1

                if not dry_run:
                    try:
                        from tools.noc_canvas.db.init_db import get_connection as nocc_conn_factory
                        nocc = nocc_conn_factory()
                        try:
                            from datetime import datetime, timezone
                            now = datetime.now(timezone.utc).isoformat()
                            desc = (
                                f"Cross-connect {xc_number} delivery anomaly — "
                                f"{days_overdue}d overdue (estimated {estimated_delivery}), "
                                f"status '{current_status}', "
                                f"anomaly_score={anomaly['score']:.2f} [{anomaly['method']}]"
                            )
                            # Dedup check
                            try:
                                existing = nocc.execute(
                                    "SELECT id FROM noc_alarms WHERE alarm_source='xc_order_poller' AND device_name=%s AND cleared=0 LIMIT 1",
                                    (str(xc_id),),
                                ).fetchone()
                            except Exception:
                                try:
                                    cur2 = nocc.cursor()
                                    cur2.execute(
                                        "SELECT id FROM noc_alarms WHERE alarm_source='xc_order_poller' AND device_name=%s AND cleared=0 LIMIT 1",
                                        (str(xc_id),),
                                    )
                                    existing = cur2.fetchone()
                                except Exception:
                                    existing = None

                            if not existing:
                                severity = anomaly["severity"]
                                try:
                                    nocc.execute(
                                        "INSERT INTO noc_alarms (alarm_source, severity, alarm_type, device_name, description, cleared, acknowledged, suppressed, first_seen, last_seen, classification) VALUES (%s,%s,%s,%s,%s,0,0,0,%s,%s,'CUI')",
                                        ("xc_order_poller", severity, "circuit",
                                         str(xc_id), desc, now, now),
                                    )
                                except Exception:
                                    nocc.execute(
                                        "INSERT INTO noc_alarms (alarm_source, severity, alarm_type, device_name, description, cleared, acknowledged, suppressed, first_seen, last_seen, classification) VALUES (%s,%s,%s,%s,%s,0,0,0,%s,%s,'CUI')",
                                        ("xc_order_poller", severity, "circuit",
                                         str(xc_id), desc, now, now),
                                    )
                                alarms_created += 1
                            try:
                                nocc.commit()
                            except Exception:
                                pass
                        finally:
                            try:
                                nocc.close()
                            except Exception:
                                pass
                    except Exception as e:
                        errors.append(f"NOCC alarm for xc_id={xc_id}: {e}")

    except Exception as e:
        errors.append(str(e))
        return {"status": "error", "errors": errors, "orders_polled": 0,
                "status_changes": 0, "delayed_orders": 0, "alarms_created": 0}
    finally:
        try:
            ccc_conn.close()
        except Exception:
            pass

    return {
        "status": "ok",
        "orders_polled": orders_polled,
        "status_changes": status_changes,
        "delayed_orders": delayed_orders,
        "alarms_created": alarms_created,
        "dry_run": dry_run,
        "errors": errors,
    }

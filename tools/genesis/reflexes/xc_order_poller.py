# CUI // SP-CTI
"""Genesis reflex: poll in-flight cross-connect orders and alarm on delayed deliveries."""
from __future__ import annotations

from typing import Any, Dict

CADENCE_HOURS = 1


def run(ctx: Dict[str, Any], conn=None) -> Dict[str, Any]:
    """Poll in-flight cross-connect orders; alarm on delayed deliveries.

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

        from datetime import date
        today = date.today().isoformat()

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

            # Check for overdue delivery
            if (estimated_delivery and estimated_delivery < today
                    and current_status not in ("active", "cancelled")):
                delayed_orders += 1

                if not dry_run:
                    try:
                        from tools.noc_canvas.db.init_db import get_connection as nocc_conn_factory
                        nocc = nocc_conn_factory()
                        try:
                            from datetime import datetime, timezone
                            now = datetime.now(timezone.utc).isoformat()
                            desc = (
                                f"Cross-connect {xc_number} delivery overdue — "
                                f"estimated {estimated_delivery}, still in status '{current_status}'"
                            )
                            # Dedup check
                            try:
                                existing = nocc.execute(
                                    "SELECT id FROM noc_alarms WHERE alarm_source='xc_order_poller' AND device_name=? AND cleared=0 LIMIT 1",
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
                                try:
                                    nocc.execute(
                                        "INSERT INTO noc_alarms (alarm_source, severity, alarm_type, device_name, description, cleared, acknowledged, suppressed, first_seen, last_seen, classification) VALUES (?,?,?,?,?,0,0,0,?,?,'CUI')",
                                        ("xc_order_poller", "major", "circuit",
                                         str(xc_id), desc, now, now),
                                    )
                                except Exception:
                                    nocc.execute(
                                        "INSERT INTO noc_alarms (alarm_source, severity, alarm_type, device_name, description, cleared, acknowledged, suppressed, first_seen, last_seen, classification) VALUES (%s,%s,%s,%s,%s,0,0,0,%s,%s,'CUI')",
                                        ("xc_order_poller", "major", "circuit",
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

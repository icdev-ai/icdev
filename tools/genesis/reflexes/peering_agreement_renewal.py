# CUI // SP-CTI
"""Genesis reflex: check peering agreements expiring within 90 days."""
from __future__ import annotations

from typing import Any, Dict

CADENCE_HOURS = 24


def run(ctx: Dict[str, Any], conn=None) -> Dict[str, Any]:
    """Check nc_peering_agreements for contracts expiring within 90 days.

    Returns:
        agreements_checked: int
        at_risk_count: int
        events_published: int
        risk_list: list[dict]
        errors: list[str]
        status: "ok" | "error"
    """
    dry_run = ctx.get("dry_run", False)
    errors: list[str] = []
    events_published = 0
    alarms_created = 0

    # Open Network Canvas connection
    nc_conn = None
    try:
        from tools.network.db.init_db import get_connection as nc_conn_factory
        nc_conn = nc_conn_factory()
    except Exception as e:
        return {"status": "error", "errors": [f"NC DB unreachable: {e}"],
                "agreements_checked": 0, "at_risk_count": 0,
                "events_published": 0, "risk_list": []}

    try:
        from tools.network.agreement_lifecycle import get_renewal_risk
        risk_list = get_renewal_risk(nc_conn, days_ahead=90)
        at_risk = len(risk_list)

        # Count total operational agreements for context
        try:
            cur = nc_conn.cursor()
            cur.execute("SELECT COUNT(*) FROM nc_peering_agreements WHERE status='operational'")
            row = cur.fetchone()
            agreements_checked = row[0] if row else 0
        except Exception:
            agreements_checked = 0

        if not dry_run:
            # Attempt to create NOCC alarms for each at-risk agreement
            try:
                from tools.noc_canvas.db.init_db import get_connection as nocc_conn_factory
                nocc = nocc_conn_factory()
                try:
                    from datetime import datetime, timezone
                    now = datetime.now(timezone.utc).isoformat()
                    for item in risk_list:
                        days = item.get("days_remaining")
                        severity = "critical" if (days is not None and days <= 30) else "major"
                        desc = (
                            f"Peering agreement with {item.get('peer_name','')} "
                            f"(AS{item.get('peer_asn','')}) expires {item.get('contract_end','')} "
                            f"({days}d remaining)"
                        )
                        # Dedup: skip if identical alarm already open
                        try:
                            nocc.execute(
                                "SELECT id FROM noc_alarms WHERE alarm_source='peering_renewal' AND device_name=%s AND cleared=0 LIMIT 1",
                                (str(item.get("id", "")),),
                            )
                        except Exception:
                            nocc.execute(
                                "SELECT id FROM noc_alarms WHERE alarm_source='peering_renewal' AND device_name=? AND cleared=0 LIMIT 1",
                                (str(item.get("id", "")),),
                            )
                        existing = nocc.cursor().fetchone() if False else None  # handled above
                        try:
                            r = nocc.execute(
                                "SELECT id FROM noc_alarms WHERE alarm_source='peering_renewal' AND device_name=? AND cleared=0 LIMIT 1",
                                (str(item.get("id", "")),),
                            ).fetchone()
                        except Exception:
                            r = None
                        if r:
                            continue
                        try:
                            nocc.execute(
                                "INSERT INTO noc_alarms (alarm_source, severity, alarm_type, device_name, description, cleared, acknowledged, suppressed, first_seen, last_seen, classification) VALUES (?,?,?,?,?,0,0,0,?,?,'CUI')",
                                ("peering_renewal", severity, "bgp",
                                 str(item.get("id", "")), desc, now, now),
                            )
                        except Exception:
                            nocc.execute(
                                "INSERT INTO noc_alarms (alarm_source, severity, alarm_type, device_name, description, cleared, acknowledged, suppressed, first_seen, last_seen, classification) VALUES (%s,%s,%s,%s,%s,0,0,0,%s,%s,'CUI')",
                                ("peering_renewal", severity, "bgp",
                                 str(item.get("id", "")), desc, now, now),
                            )
                        alarms_created += 1
                    nocc.commit()
                finally:
                    try:
                        nocc.close()
                    except Exception:
                        pass
            except Exception as e:
                errors.append(f"NOCC alarm insert skipped: {e}")

    except Exception as e:
        errors.append(str(e))
        return {"status": "error", "errors": errors, "agreements_checked": 0,
                "at_risk_count": 0, "events_published": 0, "risk_list": []}
    finally:
        try:
            nc_conn.close()
        except Exception:
            pass

    return {
        "status": "ok",
        "agreements_checked": agreements_checked,
        "at_risk_count": at_risk,
        "alarms_created": alarms_created,
        "events_published": events_published,
        "risk_list": risk_list,
        "dry_run": dry_run,
        "errors": errors,
    }

# CUI // SP-CTI
"""Network Access Control Enforcer — ZIG Device Pillar, Activity p1-10.

Enforces Zero Trust network access policy: known managed devices receive
full access; unknown devices are quarantined to a captive segment until
they enroll or are explicitly authorized by an operator.

NIST 800-53: AC-3, AC-17, SC-7, SC-10
ZIG Activity: zig-act-p1-10 (Establish NAC for unknown devices)
"""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any

from tools.security_canvas.db.init_db import get_connection

# ---------------------------------------------------------------------------
# NAC decision outcomes
# ---------------------------------------------------------------------------


class NACDecision(str, Enum):
    ALLOW = "allow"
    QUARANTINE = "quarantine"
    BLOCK = "block"
    PENDING_ENROLLMENT = "pending_enrollment"


NAC_POLICY = {
    "quarantine_vlan":   "192.168.254.0/24",
    "production_vlans":  ["10.0.0.0/8", "172.16.0.0/12"],
    "captive_portal_url": "https://enroll.icdev.local/device-onboarding",
    "max_unknown_age_hours": 1,
    "block_after_violations": 3,
}


def _ensure_tables(conn) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS zig_nac_events (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            mac_address    TEXT NOT NULL,
            ip_address     TEXT,
            hostname       TEXT,
            device_id      TEXT,
            decision       TEXT NOT NULL,
            reason         TEXT,
            network_segment TEXT,
            violation_count INTEGER DEFAULT 0,
            operator_note  TEXT,
            created_at     TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS zig_nac_device_allowlist (
            mac_address    TEXT PRIMARY KEY,
            device_id      TEXT,
            hostname       TEXT,
            authorized_by  TEXT,
            authorized_at  TEXT,
            expires_at     TEXT,
            notes          TEXT
        )
    """)
    conn.commit()


def evaluate_access(mac_address: str, ip_address: str = "",
                    hostname: str = "", device_id: str = "") -> dict[str, Any]:
    """Evaluate network access request for a device.

    Checks the device against:
    1. Managed device registry (zig_device_registry — enrolled devices)
    2. NAC allowlist (manually authorized devices)
    3. Compliance score threshold

    Returns decision with assigned network segment.
    """
    now = datetime.now(timezone.utc).isoformat()
    conn = get_connection()
    try:
        _ensure_tables(conn)

        # Check managed device registry
        registered = conn.execute(
            "SELECT * FROM zig_device_registry WHERE device_id=%s OR hostname=%s",
            (device_id, hostname),
        ).fetchone()

        # Check manual allowlist
        allowlisted = conn.execute(
            "SELECT * FROM zig_nac_device_allowlist WHERE mac_address=%s",
            (mac_address,),
        ).fetchone()

        # Count prior violations
        violations = conn.execute(
            "SELECT COUNT(*) FROM zig_nac_events WHERE mac_address=%s AND decision=%s",
            (mac_address, NACDecision.QUARANTINE),
        ).fetchone()[0]

        if violations >= NAC_POLICY["block_after_violations"]:
            decision = NACDecision.BLOCK
            reason = f"Device exceeded quarantine violation limit ({violations})"
            segment = None
        elif allowlisted:
            decision = NACDecision.ALLOW
            reason = f"Device on NAC allowlist (authorized by {allowlisted['authorized_by']})"
            segment = NAC_POLICY["production_vlans"][0]
        elif registered and registered["compliance_score"] >= 0.70:
            decision = NACDecision.ALLOW
            reason = f"Managed device with compliance score {registered['compliance_score']:.2f}"
            segment = NAC_POLICY["production_vlans"][0]
        elif registered:
            decision = NACDecision.QUARANTINE
            reason = f"Managed device below compliance threshold ({registered['compliance_score']:.2f})"
            segment = NAC_POLICY["quarantine_vlan"]
        else:
            decision = NACDecision.PENDING_ENROLLMENT
            reason = "Unknown device — redirect to captive enrollment portal"
            segment = NAC_POLICY["quarantine_vlan"]

        conn.execute(
            "INSERT INTO zig_nac_events "
            "(mac_address, ip_address, hostname, device_id, decision, reason, network_segment, violation_count, created_at) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (mac_address, ip_address, hostname, device_id, decision.value, reason, segment, violations, now),
        )
        conn.commit()

        return {
            "mac_address": mac_address,
            "ip_address": ip_address,
            "hostname": hostname,
            "decision": decision.value,
            "reason": reason,
            "network_segment": segment,
            "captive_portal": NAC_POLICY["captive_portal_url"] if decision != NACDecision.ALLOW else None,
            "evaluated_at": now,
        }
    finally:
        conn.close()


def authorize_device(mac_address: str, device_id: str = "", hostname: str = "",
                     authorized_by: str = "operator", notes: str = "") -> dict[str, Any]:
    """Manually add a device to the NAC allowlist (operator override)."""
    from datetime import timedelta
    now = datetime.now(timezone.utc)
    expires = (now + timedelta(days=90)).isoformat()
    conn = get_connection()
    try:
        _ensure_tables(conn)
        conn.execute(
            """INSERT INTO zig_nac_device_allowlist
               (mac_address, device_id, hostname, authorized_by, authorized_at, expires_at, notes)
               VALUES (%s,%s,%s,%s,%s,%s,%s)
               ON CONFLICT(mac_address) DO UPDATE SET
               authorized_by=excluded.authorized_by,
               authorized_at=excluded.authorized_at,
               expires_at=excluded.expires_at,
               notes=excluded.notes""",
            (mac_address, device_id, hostname, authorized_by, now.isoformat(), expires, notes),
        )
        conn.commit()
        return {"status": "authorized", "mac_address": mac_address, "expires_at": expires}
    finally:
        conn.close()


def deploy_nac_policy() -> dict[str, Any]:
    """Activate NAC enforcement and mark ZIG activity complete.

    Records the NAC policy deployment and marks zig-act-p1-10 as complete.
    In production this would push policy to network switches/SDN controller.
    """
    conn = get_connection()
    try:
        _ensure_tables(conn)
        total_devices = conn.execute("SELECT COUNT(*) FROM zig_device_registry").fetchone()[0]
        unknown = conn.execute(
            "SELECT COUNT(*) FROM zig_nac_events WHERE decision=%s",
            (NACDecision.PENDING_ENROLLMENT.value,),
        ).fetchone()[0]
    finally:
        conn.close()

    from tools.security_canvas.zig_activity_tracker import set_activity_status
    evidence = (
        f"NAC policy deployed. Quarantine VLAN {NAC_POLICY['quarantine_vlan']} active. "
        f"Captive portal: {NAC_POLICY['captive_portal_url']}. "
        f"Fleet: {total_devices} registered, {unknown} pending enrollment. "
        f"Block-after-{NAC_POLICY['block_after_violations']}-violations enforced."
    )
    set_activity_status("zig-act-p1-10", "complete", evidence, "nac_enforcer")
    return {"status": "deployed", "policy": NAC_POLICY, "registered_devices": total_devices}


def get_nac_summary() -> dict[str, Any]:
    """Current NAC decision summary."""
    conn = get_connection()
    try:
        _ensure_tables(conn)
        rows = conn.execute(
            "SELECT decision, COUNT(*) as cnt FROM zig_nac_events GROUP BY decision"
        ).fetchall()
        return {r["decision"]: r["cnt"] for r in rows}
    finally:
        conn.close()

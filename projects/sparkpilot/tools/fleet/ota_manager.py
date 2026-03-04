#!/usr/bin/env python3
# CUI // SP-CTI
"""OTA (Over-The-Air) update manager for SparkPilot.

Manages firmware and model OTA updates with canary deployment support.

Usage:
    python tools/fleet/ota_manager.py --deploy --firmware-id fw-001 --device-id dev-001 --json
    python tools/fleet/ota_manager.py --canary --firmware-id fw-001 --group-id grp-001 --canary-pct 10 --json
    python tools/fleet/ota_manager.py --rollback --device-id dev-001 --json
    python tools/fleet/ota_manager.py --status --json
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "sparkpilot.db"
STABILITY_WINDOW_HOURS = 72


def _get_db():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def _now():
    return datetime.now(timezone.utc).isoformat()


def deploy_firmware(firmware_id: str, device_id: str, method: str = "ota_mqtt") -> dict:
    """Deploy firmware to a single device."""
    deploy_id = f"ota-{uuid.uuid4().hex[:8]}"
    conn = _get_db()
    try:
        conn.execute(
            "INSERT INTO ota_update_log (id, device_id, firmware_id, update_type, "
            "status, stability_window_start) VALUES (?, ?, ?, 'firmware', 'pending', ?)",
            (deploy_id, device_id, firmware_id, _now()),
        )
        conn.execute(
            "INSERT INTO firmware_deploy_log (id, firmware_id, device_id, deploy_method, "
            "status) VALUES (?, ?, ?, ?, 'pending')",
            (f"dep-{uuid.uuid4().hex[:8]}", firmware_id, device_id, method),
        )
        # Log audit
        conn.execute(
            "INSERT INTO audit_trail (id, event_type, actor, action) "
            "VALUES (?, 'ota.deploy', 'fleet-manager', ?)",
            (f"aud-{uuid.uuid4().hex[:8]}",
             f"OTA deploy firmware {firmware_id} to device {device_id}"),
        )
        conn.commit()
        return {
            "status": "success",
            "deployment_id": deploy_id,
            "firmware_id": firmware_id,
            "device_id": device_id,
            "method": method,
            "stability_window_hours": STABILITY_WINDOW_HOURS,
            "mqtt_topic": f"sparkpilot/devices/{device_id}/ota/firmware",
        }
    finally:
        conn.close()


def canary_deploy(firmware_id: str, group_id: str, canary_pct: int = 10) -> dict:
    """Initiate canary deployment to a device group."""
    canary_id = f"canary-{uuid.uuid4().hex[:8]}"
    conn = _get_db()
    try:
        conn.execute(
            "INSERT INTO fleet_canary_log (id, deployment_id, group_id, canary_pct, "
            "phase, stability_window_hours) VALUES (?, ?, ?, ?, 'canary', ?)",
            (canary_id, firmware_id, group_id, canary_pct, STABILITY_WINDOW_HOURS),
        )
        conn.execute(
            "INSERT INTO audit_trail (id, event_type, actor, action) "
            "VALUES (?, 'ota.canary', 'fleet-manager', ?)",
            (f"aud-{uuid.uuid4().hex[:8]}",
             f"Canary deploy {firmware_id} to group {group_id} at {canary_pct}%"),
        )
        conn.commit()
        return {
            "status": "success",
            "canary_id": canary_id,
            "firmware_id": firmware_id,
            "group_id": group_id,
            "canary_pct": canary_pct,
            "phase": "canary",
            "next_phase": "staged_rollout",
            "stability_window_hours": STABILITY_WINDOW_HOURS,
        }
    finally:
        conn.close()


def get_ota_status() -> dict:
    """Get OTA deployment status summary."""
    conn = _get_db()
    try:
        pending = conn.execute(
            "SELECT COUNT(*) FROM ota_update_log WHERE status = 'pending'"
        ).fetchone()[0]
        in_progress = conn.execute(
            "SELECT COUNT(*) FROM ota_update_log WHERE status IN ('downloading', 'flashing', 'verifying')"
        ).fetchone()[0]
        completed = conn.execute(
            "SELECT COUNT(*) FROM ota_update_log WHERE status = 'success'"
        ).fetchone()[0]
        failed = conn.execute(
            "SELECT COUNT(*) FROM ota_update_log WHERE status = 'failed'"
        ).fetchone()[0]
        return {
            "status": "success",
            "pending": pending,
            "in_progress": in_progress,
            "completed": completed,
            "failed": failed,
        }
    finally:
        conn.close()


def main():
    parser = argparse.ArgumentParser(description="OTA Update Manager")
    parser.add_argument("--deploy", action="store_true")
    parser.add_argument("--canary", action="store_true")
    parser.add_argument("--rollback", action="store_true")
    parser.add_argument("--status", action="store_true")
    parser.add_argument("--firmware-id", help="Firmware build ID")
    parser.add_argument("--device-id", help="Target device ID")
    parser.add_argument("--group-id", help="Device group ID")
    parser.add_argument("--canary-pct", type=int, default=10)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    if args.deploy:
        result = deploy_firmware(args.firmware_id, args.device_id)
    elif args.canary:
        result = canary_deploy(args.firmware_id, args.group_id, args.canary_pct)
    elif args.status:
        result = get_ota_status()
    else:
        parser.print_help()
        return

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()

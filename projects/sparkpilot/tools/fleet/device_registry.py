#!/usr/bin/env python3
# CUI // SP-CTI
"""Device registry for SparkPilot fleet management.

Manages device registration, heartbeats, and health monitoring.

Usage:
    python tools/fleet/device_registry.py --register --name "my-esp32" --board esp32-s3 --json
    python tools/fleet/device_registry.py --list --json
    python tools/fleet/device_registry.py --heartbeat --device-id dev-001 --json
    python tools/fleet/device_registry.py --health --json
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "sparkpilot.db"


def _get_db():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def _now():
    return datetime.now(timezone.utc).isoformat()


def register_device(name: str, device_type: str = "mcu", board: str = "simulator",
                    arch: str = "", project_id: str = "") -> dict:
    """Register a new device."""
    device_id = f"dev-{uuid.uuid4().hex[:8]}"
    conn = _get_db()
    try:
        conn.execute(
            "INSERT INTO devices (id, name, device_type, board, arch, status, "
            "project_id, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, 'registered', ?, ?, ?)",
            (device_id, name, device_type, board, arch, project_id, _now(), _now()),
        )
        conn.commit()
        return {"status": "success", "device_id": device_id, "name": name,
                "board": board, "device_type": device_type}
    finally:
        conn.close()


def list_devices(status: str | None = None) -> dict:
    """List all registered devices."""
    conn = _get_db()
    try:
        if status:
            rows = conn.execute(
                "SELECT * FROM devices WHERE status = ? ORDER BY created_at DESC", (status,)
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM devices ORDER BY created_at DESC").fetchall()
        return {
            "status": "success",
            "count": len(rows),
            "devices": [dict(r) for r in rows],
        }
    finally:
        conn.close()


def update_heartbeat(device_id: str, heap_free: int = 0, stack_watermark: int = 0,
                     cpu_usage: float = 0.0, uptime: int = 0,
                     firmware_version: str = "", model_version: str = "") -> dict:
    """Update device heartbeat."""
    conn = _get_db()
    try:
        conn.execute(
            "UPDATE devices SET status = 'online', last_heartbeat = ?, "
            "heap_free_bytes = ?, stack_watermark_bytes = ?, cpu_usage_pct = ?, "
            "uptime_seconds = ?, firmware_version = ?, model_version = ?, "
            "updated_at = ? WHERE id = ?",
            (_now(), heap_free, stack_watermark, cpu_usage, uptime,
             firmware_version, model_version, _now(), device_id),
        )
        conn.commit()
        # Store telemetry
        for metric, value in [("cpu_usage", cpu_usage), ("heap_free", heap_free),
                               ("stack_watermark", stack_watermark)]:
            if value:
                conn.execute(
                    "INSERT INTO device_telemetry (id, device_id, metric_name, metric_value, unit) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (f"tel-{uuid.uuid4().hex[:8]}", device_id, metric, value,
                     "percent" if "usage" in metric else "bytes"),
                )
        conn.commit()
        return {"status": "success", "device_id": device_id, "heartbeat_at": _now()}
    finally:
        conn.close()


def get_fleet_health() -> dict:
    """Get overall fleet health summary."""
    conn = _get_db()
    try:
        total = conn.execute("SELECT COUNT(*) FROM devices").fetchone()[0]
        online = conn.execute("SELECT COUNT(*) FROM devices WHERE status = 'online'").fetchone()[0]
        offline = conn.execute("SELECT COUNT(*) FROM devices WHERE status = 'offline'").fetchone()[0]
        error = conn.execute("SELECT COUNT(*) FROM devices WHERE status = 'error'").fetchone()[0]
        return {
            "status": "success",
            "total_devices": total,
            "online": online,
            "offline": offline,
            "error": error,
            "health_pct": round((online / total * 100) if total > 0 else 0, 1),
        }
    finally:
        conn.close()


def main():
    parser = argparse.ArgumentParser(description="Device Registry")
    parser.add_argument("--register", action="store_true")
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--heartbeat", action="store_true")
    parser.add_argument("--health", action="store_true")
    parser.add_argument("--name", help="Device name")
    parser.add_argument("--board", default="simulator")
    parser.add_argument("--device-type", default="mcu")
    parser.add_argument("--device-id", help="Device ID for heartbeat")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    if args.register:
        result = register_device(args.name or "device", args.device_type, args.board)
    elif args.list:
        result = list_devices()
    elif args.heartbeat:
        result = update_heartbeat(args.device_id or "dev-sim")
    elif args.health:
        result = get_fleet_health()
    else:
        parser.print_help()
        return

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()

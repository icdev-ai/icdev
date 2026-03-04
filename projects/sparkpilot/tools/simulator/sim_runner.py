#!/usr/bin/env python3
# CUI // SP-CTI
"""SparkPilot simulator runner.

Manages FreeRTOS POSIX simulation sessions for host-based testing.
The browser WASM simulator uses this as a backend for session management.

Usage:
    python tools/simulator/sim_runner.py --create --user-id user1 --json
    python tools/simulator/sim_runner.py --list --json
    python tools/simulator/sim_runner.py --status --session-id sim-001 --json
    python tools/simulator/sim_runner.py --stop --session-id sim-001 --json
    python tools/simulator/sim_runner.py --peripherals --json
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "sparkpilot.db"

# Virtual peripherals available in the simulator
DEFAULT_PERIPHERALS = [
    {
        "peripheral_type": "led",
        "name": "Red LED",
        "default_config": {"pin": 2, "color": "#ff0000", "initial_state": "off"},
        "wasm_module": "VirtualLED",
    },
    {
        "peripheral_type": "led",
        "name": "Green LED",
        "default_config": {"pin": 4, "color": "#00ff00", "initial_state": "off"},
        "wasm_module": "VirtualLED",
    },
    {
        "peripheral_type": "button",
        "name": "Push Button",
        "default_config": {"pin": 0, "pull": "up", "active_low": True},
        "wasm_module": "VirtualButton",
    },
    {
        "peripheral_type": "temp_sensor",
        "name": "Temperature Sensor (I2C)",
        "default_config": {"i2c_addr": "0x48", "bus": 0, "range_min": -40, "range_max": 125},
        "wasm_module": "VirtualTempSensor",
    },
    {
        "peripheral_type": "accel",
        "name": "Accelerometer (I2C)",
        "default_config": {"i2c_addr": "0x1D", "bus": 0, "range_g": 4},
        "wasm_module": "VirtualAccelerometer",
    },
    {
        "peripheral_type": "oled_display",
        "name": "OLED Display 128x64 (SPI)",
        "default_config": {"spi_bus": 0, "cs_pin": 5, "dc_pin": 16, "width": 128, "height": 64},
        "wasm_module": "VirtualOLED",
    },
    {
        "peripheral_type": "potentiometer",
        "name": "Potentiometer (ADC)",
        "default_config": {"adc_channel": 0, "min_value": 0, "max_value": 4095},
        "wasm_module": "VirtualPotentiometer",
    },
]


def _get_db():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def _now():
    return datetime.now(timezone.utc).isoformat()


def seed_peripherals() -> dict:
    """Seed default virtual peripherals."""
    conn = _get_db()
    inserted = 0
    try:
        for p in DEFAULT_PERIPHERALS:
            existing = conn.execute(
                "SELECT id FROM virtual_peripherals WHERE name = ?", (p["name"],)
            ).fetchone()
            if existing:
                continue
            conn.execute(
                "INSERT INTO virtual_peripherals (id, peripheral_type, name, default_config, wasm_module) "
                "VALUES (?, ?, ?, ?, ?)",
                (f"vp-{uuid.uuid4().hex[:8]}", p["peripheral_type"], p["name"],
                 json.dumps(p["default_config"]), p["wasm_module"]),
            )
            inserted += 1
        conn.commit()
        return {"status": "success", "peripherals_seeded": inserted}
    finally:
        conn.close()


def create_session(user_id: str, board: str = "generic",
                   peripherals: list[str] | None = None) -> dict:
    """Create a new simulator session."""
    session_id = f"sim-{uuid.uuid4().hex[:8]}"
    active_peripherals = peripherals or ["Red LED", "Push Button", "Temperature Sensor (I2C)"]

    conn = _get_db()
    try:
        conn.execute(
            "INSERT INTO simulator_sessions (id, user_id, board_emulated, "
            "peripherals, status) VALUES (?, ?, ?, ?, 'running')",
            (session_id, user_id, board, json.dumps(active_peripherals)),
        )
        conn.execute(
            "INSERT INTO simulator_session_log (id, session_id, event_type, event_data, tick_at) "
            "VALUES (?, ?, 'session_created', ?, 0)",
            (f"slog-{uuid.uuid4().hex[:8]}", session_id,
             json.dumps({"user": user_id, "board": board, "peripherals": active_peripherals})),
        )
        conn.commit()
        return {
            "status": "success",
            "session_id": session_id,
            "board": board,
            "peripherals": active_peripherals,
            "state": "running",
            "message": "Simulator started! Your virtual device is ready.",
            "task_visualizer_url": f"/simulator/{session_id}/tasks",
        }
    finally:
        conn.close()


def list_sessions(user_id: str | None = None) -> dict:
    """List simulator sessions."""
    conn = _get_db()
    try:
        if user_id:
            rows = conn.execute(
                "SELECT * FROM simulator_sessions WHERE user_id = ? ORDER BY created_at DESC",
                (user_id,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM simulator_sessions ORDER BY created_at DESC"
            ).fetchall()

        sessions = []
        for r in rows:
            s = dict(r)
            if s.get("peripherals"):
                try:
                    s["peripherals"] = json.loads(s["peripherals"])
                except (json.JSONDecodeError, TypeError):
                    pass
            sessions.append(s)

        return {"status": "success", "count": len(sessions), "sessions": sessions}
    finally:
        conn.close()


def get_session_status(session_id: str) -> dict:
    """Get session status with recent events."""
    conn = _get_db()
    try:
        session = conn.execute(
            "SELECT * FROM simulator_sessions WHERE id = ?", (session_id,)
        ).fetchone()
        if not session:
            return {"status": "error", "message": "Session not found"}

        events = conn.execute(
            "SELECT * FROM simulator_session_log WHERE session_id = ? "
            "ORDER BY created_at DESC LIMIT 20",
            (session_id,),
        ).fetchall()

        s = dict(session)
        if s.get("peripherals"):
            try:
                s["peripherals"] = json.loads(s["peripherals"])
            except (json.JSONDecodeError, TypeError):
                pass

        return {
            "status": "success",
            "session": s,
            "recent_events": [dict(e) for e in events],
        }
    finally:
        conn.close()


def stop_session(session_id: str) -> dict:
    """Stop a simulator session."""
    conn = _get_db()
    try:
        conn.execute(
            "UPDATE simulator_sessions SET status = 'stopped', ended_at = ? WHERE id = ?",
            (_now(), session_id),
        )
        conn.execute(
            "INSERT INTO simulator_session_log (id, session_id, event_type, event_data) "
            "VALUES (?, ?, 'session_stopped', '{}')",
            (f"slog-{uuid.uuid4().hex[:8]}", session_id),
        )
        conn.commit()
        return {"status": "success", "session_id": session_id, "state": "stopped"}
    finally:
        conn.close()


def list_peripherals() -> dict:
    """List available virtual peripherals."""
    conn = _get_db()
    try:
        rows = conn.execute(
            "SELECT * FROM virtual_peripherals ORDER BY peripheral_type"
        ).fetchall()
        if not rows:
            # Auto-seed if empty
            seed_peripherals()
            rows = conn.execute(
                "SELECT * FROM virtual_peripherals ORDER BY peripheral_type"
            ).fetchall()
        return {
            "status": "success",
            "count": len(rows),
            "peripherals": [dict(r) for r in rows],
        }
    finally:
        conn.close()


def main():
    parser = argparse.ArgumentParser(description="SparkPilot Simulator Runner")
    parser.add_argument("--create", action="store_true", help="Create new session")
    parser.add_argument("--list", action="store_true", help="List sessions")
    parser.add_argument("--status", action="store_true", help="Get session status")
    parser.add_argument("--stop", action="store_true", help="Stop session")
    parser.add_argument("--peripherals", action="store_true", help="List peripherals")
    parser.add_argument("--seed", action="store_true", help="Seed peripherals")
    parser.add_argument("--session-id", help="Session ID")
    parser.add_argument("--user-id", default="player1", help="User ID")
    parser.add_argument("--board", default="generic", help="Board to emulate")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    if args.create:
        result = create_session(args.user_id, args.board)
    elif args.list:
        result = list_sessions(args.user_id)
    elif args.status:
        result = get_session_status(args.session_id)
    elif args.stop:
        result = stop_session(args.session_id)
    elif args.peripherals:
        result = list_peripherals()
    elif args.seed:
        result = seed_peripherals()
    else:
        parser.print_help()
        return

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()

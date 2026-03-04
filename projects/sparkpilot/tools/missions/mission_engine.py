#!/usr/bin/env python3
# CUI // SP-CTI
"""Gamified mission engine for SparkPilot.

Manages progressive learning missions from beginner (Blink LED) to advanced
(Deploy AI to real hardware). Tracks XP, badges, and user progress.

Usage:
    python tools/missions/mission_engine.py --seed --json            # Seed default missions
    python tools/missions/mission_engine.py --list --json            # List all missions
    python tools/missions/mission_engine.py --start --mission 1 --user-id user1 --json
    python tools/missions/mission_engine.py --complete --mission 1 --user-id user1 --json
    python tools/missions/mission_engine.py --progress --user-id user1 --json
    python tools/missions/mission_engine.py --leaderboard --json
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "sparkpilot.db"

# Default mission catalog — progressive difficulty
DEFAULT_MISSIONS = [
    {
        "mission_number": 1,
        "title": "Hello, LED!",
        "description": "Make an LED blink on and off. Your first embedded program!",
        "difficulty": "beginner",
        "category": "gpio",
        "objectives": ["Create a FreeRTOS task", "Toggle an LED pin", "Set a 1-second delay"],
        "hints": [
            "Use xTaskCreate() to make a new task",
            "hal_gpio_toggle(LED_PIN) switches the LED",
            "vTaskDelay(pdMS_TO_TICKS(1000)) waits 1 second",
        ],
        "starter_code": '''#include "FreeRTOS.h"
#include "task.h"
#include "hal_gpio.h"

// TODO: Create a task that blinks the LED on pin 2

void app_main(void)
{
    // TODO: Start the blink task
    vTaskStartScheduler();
}
''',
        "solution_code": '''#include "FreeRTOS.h"
#include "task.h"
#include "hal_gpio.h"

#define LED_PIN 2

static void vBlinkTask(void *pvParameters)
{
    hal_gpio_init(LED_PIN, GPIO_MODE_OUTPUT);
    for (;;)
    {
        hal_gpio_toggle(LED_PIN);
        vTaskDelay(pdMS_TO_TICKS(1000));
    }
}

void app_main(void)
{
    xTaskCreate(vBlinkTask, "blink", 256, NULL, 2, NULL);
    vTaskStartScheduler();
}
''',
        "xp_reward": 100,
        "badge_name": "First Blink",
        "estimated_minutes": 10,
        "requires_hardware": 0,
    },
    {
        "mission_number": 2,
        "title": "Sensor Explorer",
        "description": "Read a temperature sensor and display the value. Learn I2C communication!",
        "difficulty": "beginner",
        "category": "sensor",
        "objectives": ["Initialize I2C", "Read sensor data", "Print the temperature"],
        "hints": [
            "hal_i2c_init() sets up the I2C bus",
            "hal_sensor_read() returns a float value",
            "Temperature is in Celsius",
        ],
        "starter_code": '''#include "FreeRTOS.h"
#include "task.h"
#include "hal_i2c.h"
#include "hal_sensor.h"

// TODO: Read temperature sensor every 2 seconds

void app_main(void)
{
    // TODO: Start sensor reading task
    vTaskStartScheduler();
}
''',
        "xp_reward": 150,
        "badge_name": "Sensor Whisperer",
        "estimated_minutes": 15,
        "requires_hardware": 0,
    },
    {
        "mission_number": 3,
        "title": "WiFi Wrangler",
        "description": "Connect your device to WiFi. The gateway to the cloud!",
        "difficulty": "intermediate",
        "category": "network",
        "objectives": ["Configure WiFi credentials", "Connect to access point", "Verify connection"],
        "hints": [
            "Use hal_wifi_init() with a wifi_config_t struct",
            "hal_wifi_connect() starts the connection",
            "hal_wifi_is_connected() returns true when ready",
        ],
        "xp_reward": 200,
        "badge_name": "Connected",
        "estimated_minutes": 15,
        "requires_hardware": 0,
    },
    {
        "mission_number": 4,
        "title": "MQTT Messenger",
        "description": "Send your first message to the cloud using MQTT. Talk to the agents!",
        "difficulty": "intermediate",
        "category": "mqtt",
        "objectives": ["Connect to MQTT broker", "Publish a message", "Subscribe to a topic"],
        "hints": [
            "SparkPilot SDK has built-in MQTT support",
            "sparkpilot_mqtt_connect() connects to the broker",
            "sparkpilot_mqtt_publish() sends messages",
        ],
        "xp_reward": 250,
        "badge_name": "Cloud Talker",
        "estimated_minutes": 20,
        "requires_hardware": 0,
    },
    {
        "mission_number": 5,
        "title": "AI Detective",
        "description": "Add anomaly detection AI to your sensor. Your device gets smart!",
        "difficulty": "advanced",
        "category": "ai",
        "objectives": ["Load a TFLite Micro model", "Run inference on sensor data", "Report anomalies"],
        "hints": [
            "TFLite Micro needs a tensor arena (memory pool)",
            "The model is pre-trained — just load and invoke",
            "Confidence > 0.7 means anomaly detected",
        ],
        "xp_reward": 400,
        "badge_name": "AI Whisperer",
        "estimated_minutes": 30,
        "requires_hardware": 0,
    },
    {
        "mission_number": 6,
        "title": "Silicon Upgrade",
        "description": "Deploy your simulator project to real hardware. From virtual to physical!",
        "difficulty": "advanced",
        "category": "hardware",
        "objectives": ["Select a real board", "Adapt HAL drivers", "Flash firmware via USB"],
        "hints": [
            "SparkPilot recommends boards based on your project",
            "HAL stubs auto-adapt to real drivers",
            "esptool.py flashes ESP32, STM32CubeProgrammer for STM32",
        ],
        "xp_reward": 500,
        "badge_name": "Hardware Hero",
        "estimated_minutes": 45,
        "requires_hardware": 1,
    },
    {
        "mission_number": 7,
        "title": "Fleet Commander",
        "description": "Manage multiple devices at once. You're now a fleet captain!",
        "difficulty": "expert",
        "category": "fleet",
        "objectives": ["Register 3+ devices", "Create a device group", "Deploy firmware via canary rollout"],
        "hints": [
            "Device groups let you target multiple devices",
            "Canary deployment starts with 10% of devices",
            "72-hour stability window ensures safe rollout",
        ],
        "xp_reward": 600,
        "badge_name": "Fleet Commander",
        "estimated_minutes": 60,
        "requires_hardware": 0,
    },
]


def _get_db():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def _now():
    return datetime.now(timezone.utc).isoformat()


def seed_missions() -> dict:
    """Seed the default mission catalog."""
    conn = _get_db()
    inserted = 0
    try:
        for m in DEFAULT_MISSIONS:
            existing = conn.execute(
                "SELECT id FROM missions WHERE mission_number = ?",
                (m["mission_number"],),
            ).fetchone()
            if existing:
                continue
            mission_id = f"mission-{m['mission_number']:03d}"
            conn.execute(
                "INSERT INTO missions (id, mission_number, title, description, difficulty, "
                "category, prerequisites, objectives, hints, starter_code, solution_code, "
                "xp_reward, badge_name, estimated_minutes, requires_hardware) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (mission_id, m["mission_number"], m["title"], m["description"],
                 m["difficulty"], m["category"],
                 json.dumps(m.get("prerequisites", [])),
                 json.dumps(m.get("objectives", [])),
                 json.dumps(m.get("hints", [])),
                 m.get("starter_code", ""),
                 m.get("solution_code", ""),
                 m["xp_reward"],
                 m.get("badge_name", ""),
                 m.get("estimated_minutes", 30),
                 m.get("requires_hardware", 0)),
            )
            inserted += 1
        conn.commit()
        return {"status": "success", "missions_seeded": inserted,
                "total_missions": len(DEFAULT_MISSIONS)}
    finally:
        conn.close()


def list_missions(difficulty: str | None = None) -> dict:
    """List all available missions."""
    conn = _get_db()
    try:
        if difficulty:
            rows = conn.execute(
                "SELECT * FROM missions WHERE difficulty = ? ORDER BY mission_number",
                (difficulty,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM missions ORDER BY mission_number"
            ).fetchall()

        missions = []
        for r in rows:
            m = dict(r)
            for field in ("prerequisites", "objectives", "hints"):
                if m.get(field):
                    try:
                        m[field] = json.loads(m[field])
                    except (json.JSONDecodeError, TypeError):
                        pass
            missions.append(m)

        return {"status": "success", "count": len(missions), "missions": missions}
    finally:
        conn.close()


def start_mission(mission_number: int, user_id: str) -> dict:
    """Start a mission for a user."""
    conn = _get_db()
    try:
        mission = conn.execute(
            "SELECT * FROM missions WHERE mission_number = ?", (mission_number,)
        ).fetchone()
        if not mission:
            return {"status": "error", "message": f"Mission {mission_number} not found"}

        # Ensure user progress exists
        existing = conn.execute(
            "SELECT * FROM user_progress WHERE user_id = ?", (user_id,)
        ).fetchone()
        if not existing:
            conn.execute(
                "INSERT INTO user_progress (id, user_id, display_name, current_mission_id) "
                "VALUES (?, ?, ?, ?)",
                (f"prog-{uuid.uuid4().hex[:8]}", user_id, user_id, mission["id"]),
            )

        # Log mission start
        log_id = f"mlog-{uuid.uuid4().hex[:8]}"
        conn.execute(
            "INSERT INTO mission_completion_log (id, user_id, mission_id, status, attempts) "
            "VALUES (?, ?, ?, 'started', 1)",
            (log_id, user_id, mission["id"]),
        )
        conn.execute(
            "UPDATE user_progress SET current_mission_id = ?, updated_at = ? WHERE user_id = ?",
            (mission["id"], _now(), user_id),
        )
        conn.commit()

        m = dict(mission)
        for field in ("objectives", "hints"):
            if m.get(field):
                try:
                    m[field] = json.loads(m[field])
                except (json.JSONDecodeError, TypeError):
                    pass

        return {
            "status": "success",
            "mission": {
                "number": m["mission_number"],
                "title": m["title"],
                "description": m["description"],
                "difficulty": m["difficulty"],
                "objectives": m.get("objectives", []),
                "starter_code": m.get("starter_code", ""),
                "xp_reward": m["xp_reward"],
                "estimated_minutes": m.get("estimated_minutes", 30),
            },
            "hint": "Type 'hint' if you get stuck!",
        }
    finally:
        conn.close()


def complete_mission(mission_number: int, user_id: str, code: str = "") -> dict:
    """Mark a mission as completed and award XP."""
    conn = _get_db()
    try:
        mission = conn.execute(
            "SELECT * FROM missions WHERE mission_number = ?", (mission_number,)
        ).fetchone()
        if not mission:
            return {"status": "error", "message": f"Mission {mission_number} not found"}

        xp = mission["xp_reward"]
        badge = mission["badge_name"]

        # Update completion log
        conn.execute(
            "UPDATE mission_completion_log SET status = 'completed', "
            "code_submitted = ?, xp_earned = ?, completed_at = ? "
            "WHERE user_id = ? AND mission_id = ? AND status != 'completed'",
            (code, xp, _now(), user_id, mission["id"]),
        )

        # Update user progress
        progress = conn.execute(
            "SELECT * FROM user_progress WHERE user_id = ?", (user_id,)
        ).fetchone()
        if progress:
            total_xp = (progress["total_xp"] or 0) + xp
            completed = (progress["missions_completed"] or 0) + 1
            badges = json.loads(progress["badges"] or "[]")
            if badge and badge not in badges:
                badges.append(badge)
            level = 1 + total_xp // 500  # Level up every 500 XP

            conn.execute(
                "UPDATE user_progress SET total_xp = ?, missions_completed = ?, "
                "level = ?, badges = ?, updated_at = ? WHERE user_id = ?",
                (total_xp, completed, level, json.dumps(badges), _now(), user_id),
            )

        conn.commit()

        return {
            "status": "success",
            "mission_completed": mission["title"],
            "xp_earned": xp,
            "badge_earned": badge,
            "total_xp": total_xp if progress else xp,
            "level": level if progress else 1,
            "next_mission": mission_number + 1 if mission_number < len(DEFAULT_MISSIONS) else None,
            "message": f"Awesome! You earned {xp} XP and the '{badge}' badge!",
        }
    finally:
        conn.close()


def get_progress(user_id: str) -> dict:
    """Get user progress summary."""
    conn = _get_db()
    try:
        progress = conn.execute(
            "SELECT * FROM user_progress WHERE user_id = ?", (user_id,)
        ).fetchone()
        if not progress:
            return {"status": "success", "user_id": user_id, "started": False,
                    "message": "No progress yet. Start Mission 1!"}

        p = dict(progress)
        try:
            p["badges"] = json.loads(p.get("badges") or "[]")
        except (json.JSONDecodeError, TypeError):
            p["badges"] = []

        completed = conn.execute(
            "SELECT m.mission_number, m.title, mc.completed_at, mc.xp_earned "
            "FROM mission_completion_log mc JOIN missions m ON mc.mission_id = m.id "
            "WHERE mc.user_id = ? AND mc.status = 'completed' ORDER BY m.mission_number",
            (user_id,),
        ).fetchall()

        return {
            "status": "success",
            "user_id": user_id,
            "started": True,
            "level": p["level"],
            "total_xp": p["total_xp"],
            "missions_completed": p["missions_completed"],
            "badges": p["badges"],
            "mode": p.get("mode", "beginner"),
            "completed_missions": [dict(c) for c in completed],
            "next_mission": (p["missions_completed"] or 0) + 1,
        }
    finally:
        conn.close()


def main():
    parser = argparse.ArgumentParser(description="SparkPilot Mission Engine")
    parser.add_argument("--seed", action="store_true", help="Seed default missions")
    parser.add_argument("--list", action="store_true", help="List all missions")
    parser.add_argument("--start", action="store_true", help="Start a mission")
    parser.add_argument("--complete", action="store_true", help="Complete a mission")
    parser.add_argument("--progress", action="store_true", help="Get user progress")
    parser.add_argument("--leaderboard", action="store_true", help="Show leaderboard")
    parser.add_argument("--mission", type=int, help="Mission number")
    parser.add_argument("--user-id", default="player1", help="User ID")
    parser.add_argument("--difficulty", help="Filter by difficulty")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    if args.seed:
        result = seed_missions()
    elif args.list:
        result = list_missions(args.difficulty)
    elif args.start:
        result = start_mission(args.mission or 1, args.user_id)
    elif args.complete:
        result = complete_mission(args.mission or 1, args.user_id)
    elif args.progress:
        result = get_progress(args.user_id)
    else:
        parser.print_help()
        return

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
# CUI // SP-CTI
"""SparkPilot database initialization.

Creates all tables for the SparkPilot embedded AI co-pilot:
- Core ICDEV™ tables (projects, audit_trail, agents, compliance, etc.)
- Embedded-specific tables (devices, firmware, missions, simulator, fleet, edge AI)
"""

import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "sparkpilot.db"

# Tables that are append-only (no UPDATE/DELETE) — NIST AU compliance
APPEND_ONLY_TABLES = (
    "audit_trail",
    "firmware_deploy_log",
    "crash_dump_log",
    "mission_completion_log",
    "device_telemetry",
    "ota_update_log",
    "inference_telemetry",
    "fleet_canary_log",
    "simulator_session_log",
    "compliance_evidence",
)


def init_db(db_path=None):
    """Initialize SparkPilot database with all required tables."""
    db_path = db_path or str(DB_PATH)
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")

    # ── Core ICDEV™ Tables ──────────────────────────────────────────────
    conn.execute(
        "CREATE TABLE IF NOT EXISTS projects "
        "(id TEXT PRIMARY KEY, name TEXT NOT NULL, description TEXT, "
        "status TEXT DEFAULT 'active', impact_level TEXT DEFAULT 'IL4', "
        "classification TEXT DEFAULT 'CUI', "
        "created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, "
        "updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS audit_trail "
        "(id TEXT PRIMARY KEY, event_type TEXT NOT NULL, actor TEXT, "
        "action TEXT NOT NULL, project_id TEXT, session_id TEXT, "
        "details TEXT, "
        "created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS agents "
        "(id TEXT PRIMARY KEY, name TEXT NOT NULL, port INTEGER, "
        "agent_type TEXT, status TEXT DEFAULT 'idle', "
        "last_heartbeat TIMESTAMP, capabilities TEXT, "
        "created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS agent_tasks "
        "(id TEXT PRIMARY KEY, agent_id TEXT, task_type TEXT, "
        "status TEXT DEFAULT 'pending', input_data TEXT, "
        "output_data TEXT, error TEXT, "
        "created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, "
        "completed_at TIMESTAMP)"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS memory_entries "
        "(id INTEGER PRIMARY KEY AUTOINCREMENT, content TEXT NOT NULL, "
        "memory_type TEXT DEFAULT 'event', importance INTEGER DEFAULT 5, "
        "tags TEXT, embedding BLOB, "
        "created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
    )

    # ── Compliance Tables ──────────────────────────────────────────────
    conn.execute(
        "CREATE TABLE IF NOT EXISTS compliance_controls "
        "(id TEXT PRIMARY KEY, control_id TEXT NOT NULL, "
        "framework TEXT NOT NULL, status TEXT DEFAULT 'not_assessed', "
        "project_id TEXT, evidence TEXT, assessor TEXT, "
        "created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS compliance_evidence "
        "(id TEXT PRIMARY KEY, control_id TEXT, framework TEXT, "
        "evidence_type TEXT, evidence_data TEXT, collector TEXT, "
        "created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS sbom_entries "
        "(id TEXT PRIMARY KEY, project_id TEXT, component_name TEXT, "
        "component_version TEXT, license TEXT, purl TEXT, "
        "vulnerability_status TEXT, "
        "created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
    )

    # ── Device Registry ────────────────────────────────────────────────
    conn.execute(
        "CREATE TABLE IF NOT EXISTS devices "
        "(id TEXT PRIMARY KEY, name TEXT NOT NULL, "
        "device_type TEXT NOT NULL, "  # mcu, gateway, simulator
        "board TEXT, "  # e.g., 'esp32-s3', 'stm32f407', 'rpi4', 'wasm'
        "arch TEXT, "  # cortex-m4, cortex-m7, esp32, riscv, wasm
        "status TEXT DEFAULT 'registered', "  # registered, online, offline, error
        "firmware_version TEXT, model_version TEXT, "
        "ip_address TEXT, mqtt_topic_prefix TEXT, "
        "sdk_version TEXT, "
        "heap_free_bytes INTEGER, stack_watermark_bytes INTEGER, "
        "cpu_usage_pct REAL, uptime_seconds INTEGER, "
        "last_heartbeat TIMESTAMP, "
        "project_id TEXT, owner_id TEXT, "
        "metadata TEXT, "  # JSON: custom device properties
        "created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, "
        "updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
    )

    # ── Firmware Management ────────────────────────────────────────────
    conn.execute(
        "CREATE TABLE IF NOT EXISTS firmware_builds "
        "(id TEXT PRIMARY KEY, project_id TEXT, "
        "version TEXT NOT NULL, "
        "target_board TEXT NOT NULL, "  # esp32-s3, stm32f407, wasm, etc.
        "target_arch TEXT, "
        "source_hash TEXT, binary_hash TEXT, binary_size_bytes INTEGER, "
        "toolchain TEXT, "  # gcc-arm-none-eabi, xtensa-esp32-elf, emcc
        "cmake_config TEXT, "  # JSON: CMake variables
        "freertos_config TEXT, "  # JSON: FreeRTOSConfig.h settings
        "build_status TEXT DEFAULT 'pending', "  # pending, building, success, failed
        "build_log TEXT, "
        "signed INTEGER DEFAULT 0, signing_key_id TEXT, "
        "created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS firmware_deploy_log "
        "(id TEXT PRIMARY KEY, firmware_id TEXT NOT NULL, "
        "device_id TEXT NOT NULL, "
        "deploy_method TEXT, "  # ota_mqtt, usb_flash, simulator_load
        "status TEXT DEFAULT 'pending', "  # pending, downloading, flashing, verifying, success, failed, rolled_back
        "progress_pct INTEGER DEFAULT 0, "
        "error TEXT, rollback_firmware_id TEXT, "
        "created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, "
        "completed_at TIMESTAMP)"
    )

    # ── FreeRTOS Task Tracking ─────────────────────────────────────────
    conn.execute(
        "CREATE TABLE IF NOT EXISTS rtos_tasks "
        "(id TEXT PRIMARY KEY, device_id TEXT, "
        "task_name TEXT NOT NULL, priority INTEGER, "
        "stack_size_words INTEGER, stack_watermark_words INTEGER, "
        "state TEXT, "  # running, ready, blocked, suspended, deleted
        "cpu_usage_pct REAL, "
        "created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, "
        "updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
    )

    # ── Crash Dump / Self-Healing ──────────────────────────────────────
    conn.execute(
        "CREATE TABLE IF NOT EXISTS crash_dump_log "
        "(id TEXT PRIMARY KEY, device_id TEXT NOT NULL, "
        "crash_type TEXT, "  # hardfault, stack_overflow, watchdog, assertion, memory_corruption
        "fault_address TEXT, stack_trace TEXT, "
        "registers TEXT, "  # JSON: register dump
        "freertos_state TEXT, "  # JSON: task states at crash time
        "firmware_version TEXT, "
        "analysis TEXT, "  # LLM-generated analysis
        "root_cause TEXT, "
        "auto_healed INTEGER DEFAULT 0, "
        "patch_id TEXT, "  # reference to generated fix
        "created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
    )

    # ── OTA Updates ────────────────────────────────────────────────────
    conn.execute(
        "CREATE TABLE IF NOT EXISTS ota_update_log "
        "(id TEXT PRIMARY KEY, device_id TEXT NOT NULL, "
        "firmware_id TEXT, model_id TEXT, "
        "update_type TEXT, "  # firmware, model, config
        "status TEXT DEFAULT 'pending', "
        "progress_pct INTEGER DEFAULT 0, "
        "mcuboot_slot TEXT, "  # primary, secondary
        "stability_window_start TIMESTAMP, "
        "stability_window_end TIMESTAMP, "
        "accepted INTEGER DEFAULT 0, "
        "created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
    )

    # ── Gamified Missions ──────────────────────────────────────────────
    conn.execute(
        "CREATE TABLE IF NOT EXISTS missions "
        "(id TEXT PRIMARY KEY, "
        "mission_number INTEGER NOT NULL, "
        "title TEXT NOT NULL, "
        "description TEXT, "
        "difficulty TEXT DEFAULT 'beginner', "  # beginner, intermediate, advanced, expert
        "category TEXT, "  # gpio, sensor, network, mqtt, ai, fleet, hardware
        "prerequisites TEXT, "  # JSON: list of mission IDs
        "objectives TEXT, "  # JSON: list of objectives
        "hints TEXT, "  # JSON: progressive hints
        "starter_code TEXT, "
        "solution_code TEXT, "
        "validation_script TEXT, "  # Python script to validate completion
        "xp_reward INTEGER DEFAULT 100, "
        "badge_name TEXT, "
        "estimated_minutes INTEGER, "
        "requires_hardware INTEGER DEFAULT 0, "
        "created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS mission_completion_log "
        "(id TEXT PRIMARY KEY, user_id TEXT NOT NULL, "
        "mission_id TEXT NOT NULL, "
        "status TEXT DEFAULT 'started', "  # started, in_progress, completed, skipped
        "attempts INTEGER DEFAULT 0, "
        "code_submitted TEXT, "
        "xp_earned INTEGER DEFAULT 0, "
        "time_spent_seconds INTEGER, "
        "hints_used INTEGER DEFAULT 0, "
        "started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, "
        "completed_at TIMESTAMP)"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS user_progress "
        "(id TEXT PRIMARY KEY, user_id TEXT NOT NULL UNIQUE, "
        "display_name TEXT, "
        "total_xp INTEGER DEFAULT 0, "
        "level INTEGER DEFAULT 1, "
        "badges TEXT, "  # JSON: list of earned badges
        "current_mission_id TEXT, "
        "missions_completed INTEGER DEFAULT 0, "
        "devices_deployed INTEGER DEFAULT 0, "
        "mode TEXT DEFAULT 'beginner', "  # beginner, pro
        "created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, "
        "updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
    )

    # ── Simulator ──────────────────────────────────────────────────────
    conn.execute(
        "CREATE TABLE IF NOT EXISTS simulator_sessions "
        "(id TEXT PRIMARY KEY, user_id TEXT, "
        "session_type TEXT DEFAULT 'browser', "  # browser, posix, host_test
        "board_emulated TEXT DEFAULT 'generic', "
        "peripherals TEXT, "  # JSON: active virtual peripherals
        "firmware_id TEXT, "
        "status TEXT DEFAULT 'running', "  # running, paused, stopped, crashed
        "tick_count INTEGER DEFAULT 0, "
        "created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, "
        "ended_at TIMESTAMP)"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS simulator_session_log "
        "(id TEXT PRIMARY KEY, session_id TEXT NOT NULL, "
        "event_type TEXT NOT NULL, "  # task_created, task_switched, led_toggle, sensor_read, mqtt_publish, crash
        "event_data TEXT, "  # JSON
        "tick_at INTEGER, "
        "created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS virtual_peripherals "
        "(id TEXT PRIMARY KEY, "
        "peripheral_type TEXT NOT NULL, "  # led, button, temp_sensor, accel, oled_display, potentiometer
        "name TEXT NOT NULL, "
        "default_config TEXT, "  # JSON: pin, i2c_addr, spi_bus, etc.
        "wasm_module TEXT, "  # JS class name for browser rendering
        "created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
    )

    # ── Device Telemetry ───────────────────────────────────────────────
    conn.execute(
        "CREATE TABLE IF NOT EXISTS device_telemetry "
        "(id TEXT PRIMARY KEY, device_id TEXT NOT NULL, "
        "metric_name TEXT NOT NULL, "  # cpu_usage, heap_free, stack_watermark, temperature, rssi, inference_latency
        "metric_value REAL, "
        "unit TEXT, "
        "created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
    )

    # ── Edge AI / TinyML ───────────────────────────────────────────────
    conn.execute(
        "CREATE TABLE IF NOT EXISTS ml_models "
        "(id TEXT PRIMARY KEY, project_id TEXT, "
        "model_name TEXT NOT NULL, "
        "model_type TEXT, "  # tflite_micro, edge_impulse, onnx_micro
        "task_type TEXT, "  # anomaly_detection, keyword_spotting, image_classification, predictive_maintenance
        "model_size_bytes INTEGER, "
        "arena_size_bytes INTEGER, "  # TFLite Micro tensor arena
        "input_shape TEXT, output_shape TEXT, "  # JSON
        "quantization TEXT, "  # int8, float16, float32
        "accuracy REAL, latency_ms REAL, "
        "source TEXT, "  # edge_impulse, custom, tflite_model_maker
        "model_hash TEXT, "
        "created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS inference_telemetry "
        "(id TEXT PRIMARY KEY, device_id TEXT NOT NULL, "
        "model_id TEXT NOT NULL, "
        "latency_ms REAL, "
        "confidence REAL, "
        "prediction TEXT, "
        "arena_used_bytes INTEGER, "
        "created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
    )

    # ── Fleet Management ───────────────────────────────────────────────
    conn.execute(
        "CREATE TABLE IF NOT EXISTS device_groups "
        "(id TEXT PRIMARY KEY, name TEXT NOT NULL, "
        "description TEXT, "
        "selector TEXT, "  # JSON: device matching rules
        "device_count INTEGER DEFAULT 0, "
        "created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS fleet_canary_log "
        "(id TEXT PRIMARY KEY, "
        "deployment_id TEXT NOT NULL, "
        "group_id TEXT, "
        "canary_pct INTEGER DEFAULT 10, "
        "phase TEXT, "  # canary, staged_rollout, full_rollout, rollback
        "devices_targeted INTEGER, devices_updated INTEGER, "
        "devices_failed INTEGER, "
        "health_check_status TEXT, "
        "stability_window_hours INTEGER DEFAULT 72, "
        "created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, "
        "completed_at TIMESTAMP)"
    )

    # ── MQTT Agent Protocol ────────────────────────────────────────────
    conn.execute(
        "CREATE TABLE IF NOT EXISTS mqtt_messages "
        "(id TEXT PRIMARY KEY, "
        "topic TEXT NOT NULL, "
        "direction TEXT, "  # inbound, outbound
        "payload TEXT, "  # CBOR-decoded JSON
        "device_id TEXT, agent_id TEXT, "
        "qos INTEGER DEFAULT 1, "
        "created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS device_commands "
        "(id TEXT PRIMARY KEY, device_id TEXT NOT NULL, "
        "command_type TEXT NOT NULL, "  # OTA_UPDATE, CONFIG_SET, REBOOT, DIAG_DUMP, MODEL_UPDATE, TASK_CONTROL
        "payload TEXT, "
        "status TEXT DEFAULT 'pending', "  # pending, sent, acknowledged, executing, completed, failed, timeout
        "response TEXT, "
        "issued_by TEXT, "  # agent or user
        "created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, "
        "completed_at TIMESTAMP)"
    )

    # ── Natural Language Commands ──────────────────────────────────────
    conn.execute(
        "CREATE TABLE IF NOT EXISTS nl_commands "
        "(id TEXT PRIMARY KEY, user_id TEXT, "
        "raw_input TEXT NOT NULL, "  # e.g., 'Make the LED blink every 2 seconds'
        "parsed_intent TEXT, "  # JSON: extracted intent
        "generated_code TEXT, "  # C code output
        "target_board TEXT, "
        "firmware_id TEXT, "  # resulting firmware build
        "status TEXT DEFAULT 'parsing', "  # parsing, generating, compiling, deploying, completed, failed
        "created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
    )

    # ── HAL / Board Support ────────────────────────────────────────────
    conn.execute(
        "CREATE TABLE IF NOT EXISTS board_support_packages "
        "(id TEXT PRIMARY KEY, "
        "board_name TEXT NOT NULL, "  # esp32-s3-devkitc, stm32f407-discovery, etc.
        "manufacturer TEXT, "
        "arch TEXT NOT NULL, "
        "cpu TEXT, "  # Cortex-M4, Xtensa LX7, RISC-V
        "flash_kb INTEGER, ram_kb INTEGER, "
        "peripherals TEXT, "  # JSON: available peripherals
        "hal_driver TEXT, "  # esp-idf, stm32hal, nrf-hal
        "freertos_port TEXT, "  # GCC/ARM_CM4F, GCC/XTENSA_ESP32, etc.
        "toolchain TEXT, "  # gcc-arm-none-eabi, xtensa-esp32-elf
        "flash_tool TEXT, "  # esptool.py, STM32CubeProgrammer, nrfjprog
        "linker_script_template TEXT, "
        "supported INTEGER DEFAULT 1, "
        "created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
    )

    # ── Build / CMake ──────────────────────────────────────────────────
    conn.execute(
        "CREATE TABLE IF NOT EXISTS cmake_configs "
        "(id TEXT PRIMARY KEY, project_id TEXT, "
        "board_id TEXT, "
        "cmake_content TEXT, "
        "freertos_config_content TEXT, "  # FreeRTOSConfig.h
        "linker_script_content TEXT, "
        "optimization_level TEXT DEFAULT '-Os', "
        "debug_enabled INTEGER DEFAULT 0, "
        "created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
    )

    # ── Knowledge Base (embedded-specific patterns) ────────────────────
    conn.execute(
        "CREATE TABLE IF NOT EXISTS embedded_patterns "
        "(id TEXT PRIMARY KEY, "
        "pattern_type TEXT, "  # crash_signature, optimization_hint, best_practice, anti_pattern
        "title TEXT NOT NULL, "
        "description TEXT, "
        "match_criteria TEXT, "  # JSON: how to detect this pattern
        "resolution TEXT, "
        "severity TEXT, "
        "occurrences INTEGER DEFAULT 0, "
        "last_seen TIMESTAMP, "
        "created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
    )

    conn.commit()

    tables = [
        r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()
    ]
    print(f"SparkPilot database initialized at {db_path}")
    print(f"Tables created ({len(tables)}):")
    for t in tables:
        print(f"  - {t}")
    conn.close()
    return tables


if __name__ == "__main__":
    init_db()

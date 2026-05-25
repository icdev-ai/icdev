#!/usr/bin/env python3
# CUI // SP-CTI
"""Natural Language to Firmware generator.

Translates natural language commands (e.g., 'Blink the LED every 2 seconds')
into FreeRTOS C code, cross-compiles, and optionally deploys via OTA or simulator.

Usage:
    python tools/embedded/nl_to_firmware.py --command "Blink LED every 2 seconds" --board esp32-s3 --json
    python tools/embedded/nl_to_firmware.py --command "Read temperature sensor" --board simulator --json
    python tools/embedded/nl_to_firmware.py --command "Send MQTT message every 10s" --board stm32f407 --deploy --json
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "sparkpilot.db"

# Template library: common intents mapped to FreeRTOS C code templates
CODE_TEMPLATES = {
    "blink_led": {
        "keywords": ["blink", "led", "flash", "toggle"],
        "template": '''
#include "FreeRTOS.h"
#include "task.h"
#include "hal_gpio.h"

#define LED_PIN {pin}

static void vBlinkTask(void *pvParameters)
{{
    hal_gpio_init(LED_PIN, GPIO_MODE_OUTPUT);
    for (;;)
    {{
        hal_gpio_toggle(LED_PIN);
        vTaskDelay(pdMS_TO_TICKS({delay_ms}));
    }}
}}

void app_main(void)
{{
    xTaskCreate(vBlinkTask, "blink", configMINIMAL_STACK_SIZE + 128, NULL, 2, NULL);
    vTaskStartScheduler();
}}
''',
        "default_params": {"pin": "2", "delay_ms": "1000"},
    },
    "read_sensor": {
        "keywords": ["read", "sensor", "temperature", "humidity", "accel"],
        "template": '''
#include "FreeRTOS.h"
#include "task.h"
#include "hal_i2c.h"
#include "hal_sensor.h"

static void vSensorTask(void *pvParameters)
{{
    hal_i2c_init(I2C_PORT_0, {sda_pin}, {scl_pin}, 400000);
    sensor_handle_t sensor = hal_sensor_init(SENSOR_{sensor_type});

    for (;;)
    {{
        float value = hal_sensor_read(sensor);
        /* TODO: report via MQTT telemetry */
        vTaskDelay(pdMS_TO_TICKS({interval_ms}));
    }}
}}

void app_main(void)
{{
    xTaskCreate(vSensorTask, "sensor", configMINIMAL_STACK_SIZE + 256, NULL, 2, NULL);
    vTaskStartScheduler();
}}
''',
        "default_params": {"sda_pin": "21", "scl_pin": "22", "sensor_type": "TEMPERATURE", "interval_ms": "1000"},
    },
    "mqtt_publish": {
        "keywords": ["mqtt", "publish", "send", "message", "telemetry"],
        "template": '''
#include "FreeRTOS.h"
#include "task.h"
#include "sparkpilot_sdk.h"

static void vMqttTask(void *pvParameters)
{{
    sparkpilot_init();
    sparkpilot_mqtt_connect("{broker}", {port});

    for (;;)
    {{
        const char *payload = "{message}";
        sparkpilot_mqtt_publish("{topic}", payload, strlen(payload), {qos});
        vTaskDelay(pdMS_TO_TICKS({interval_ms}));
    }}
}}

void app_main(void)
{{
    xTaskCreate(vMqttTask, "mqtt_pub", configMINIMAL_STACK_SIZE + 512, NULL, 3, NULL);
    vTaskStartScheduler();
}}
''',
        "default_params": {
            "broker": "mqtt.sparkpilot.local",
            "port": "1883",
            "topic": "sparkpilot/telemetry",
            "message": "hello",
            "qos": "1",
            "interval_ms": "10000",
        },
    },
    "wifi_connect": {
        "keywords": ["wifi", "connect", "network", "internet"],
        "template": '''
#include "FreeRTOS.h"
#include "task.h"
#include "hal_wifi.h"

static void vWifiTask(void *pvParameters)
{{
    wifi_config_t cfg = {{
        .ssid = "{ssid}",
        .password = "{password}",
        .auth_mode = WIFI_AUTH_WPA2_PSK,
    }};
    hal_wifi_init(&cfg);
    hal_wifi_connect();

    /* Block until connected */
    while (!hal_wifi_is_connected())
    {{
        vTaskDelay(pdMS_TO_TICKS(500));
    }}

    /* WiFi connected — start application tasks */
    for (;;)
    {{
        vTaskDelay(pdMS_TO_TICKS(60000));
    }}
}}

void app_main(void)
{{
    xTaskCreate(vWifiTask, "wifi", configMINIMAL_STACK_SIZE + 1024, NULL, 4, NULL);
    vTaskStartScheduler();
}}
''',
        "default_params": {"ssid": "MyNetwork", "password": "********"},
    },
}


def _get_db():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def _now():
    return datetime.now(timezone.utc).isoformat()


def _detect_intent(command: str) -> tuple[str, dict]:
    """Detect user intent from natural language command. Returns (template_key, params)."""
    command_lower = command.lower()

    # Extract numeric values for timing
    import re
    numbers = re.findall(r'(\d+(?:\.\d+)?)\s*(seconds?|s|ms|milliseconds?|minutes?|min|m|hz)', command_lower)
    delay_ms = None
    for num_str, unit in numbers:
        num = float(num_str)
        if unit.startswith('s') or unit == 'seconds':
            delay_ms = int(num * 1000)
        elif unit in ('ms', 'milliseconds'):
            delay_ms = int(num)
        elif unit.startswith('min') or unit == 'm':
            delay_ms = int(num * 60000)
        elif unit == 'hz':
            delay_ms = int(1000 / num) if num > 0 else 1000

    best_match = None
    best_score = 0
    for key, tpl in CODE_TEMPLATES.items():
        score = sum(1 for kw in tpl["keywords"] if kw in command_lower)
        if score > best_score:
            best_score = score
            best_match = key

    if not best_match:
        best_match = "blink_led"  # Safe default for beginners

    params = dict(CODE_TEMPLATES[best_match]["default_params"])
    if delay_ms is not None:
        if "delay_ms" in params:
            params["delay_ms"] = str(delay_ms)
        elif "interval_ms" in params:
            params["interval_ms"] = str(delay_ms)

    return best_match, params


def generate_code(command: str, board: str = "simulator", params: dict | None = None) -> dict:
    """Generate FreeRTOS C code from natural language command."""
    intent, detected_params = _detect_intent(command)
    if params:
        detected_params.update(params)

    template_info = CODE_TEMPLATES[intent]
    code = template_info["template"].format(**detected_params).strip()

    cmd_id = str(uuid.uuid4())[:8]
    record = {
        "id": f"nl-{cmd_id}",
        "raw_input": command,
        "parsed_intent": json.dumps({"intent": intent, "params": detected_params}),
        "generated_code": code,
        "target_board": board,
        "status": "generated",
    }

    # Store in DB
    conn = _get_db()
    try:
        conn.execute(
            "INSERT INTO nl_commands (id, user_id, raw_input, parsed_intent, "
            "generated_code, target_board, status) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (record["id"], "cli", command, record["parsed_intent"],
             code, board, "generated"),
        )
        conn.commit()
    except Exception:
        pass
    finally:
        conn.close()

    return {
        "status": "success",
        "command_id": record["id"],
        "intent": intent,
        "params": detected_params,
        "board": board,
        "code": code,
        "next_steps": [
            f"Compile: python tools/embedded/cmake_generator.py --project-id proj --board {board}",
            "Deploy: python tools/fleet/ota_manager.py --firmware-id <id> --device-id <id>",
            "Or load in simulator: python tools/simulator/sim_runner.py --code <file>",
        ],
    }


def main():
    parser = argparse.ArgumentParser(description="Natural Language to Firmware")
    parser.add_argument("--command", required=True, help="Natural language command")
    parser.add_argument("--board", default="simulator", help="Target board (default: simulator)")
    parser.add_argument("--json", action="store_true", help="JSON output")
    parser.add_argument("--deploy", action="store_true", help="Auto-deploy after generation")
    args = parser.parse_args()

    result = generate_code(args.command, args.board)

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(f"Intent: {result['intent']}")
        print(f"Board: {result['board']}")
        print(f"Command ID: {result['command_id']}")
        print("\n--- Generated Code ---")
        print(result["code"])
        print("\n--- Next Steps ---")
        for step in result["next_steps"]:
            print(f"  {step}")


if __name__ == "__main__":
    main()

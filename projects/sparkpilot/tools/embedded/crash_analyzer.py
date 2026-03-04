#!/usr/bin/env python3
# CUI // SP-CTI
"""Crash dump analyzer for FreeRTOS self-healing.

Analyzes HardFault, stack overflow, watchdog, and assertion failures.
Matches against known embedded patterns and generates fix suggestions.

Usage:
    python tools/embedded/crash_analyzer.py --crash-type hardfault --device-id dev-001 --json
    python tools/embedded/crash_analyzer.py --analyze --crash-id crash-001 --json
    python tools/embedded/crash_analyzer.py --patterns --json
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "sparkpilot.db"

# Known crash patterns — deterministic matching (air-gap safe)
CRASH_PATTERNS = {
    "stack_overflow_task": {
        "match": ["stack overflow", "StackType_t", "pxTopOfStack"],
        "root_cause": "Task stack too small for its workload",
        "fix": "Increase stack size in xTaskCreate() — check stack watermark with uxTaskGetStackHighWaterMark()",
        "severity": "critical",
        "auto_healable": True,
        "fix_template": "Increase configMINIMAL_STACK_SIZE or task-specific stack allocation by 2x",
    },
    "hardfault_null_ptr": {
        "match": ["hardfault", "0x00000000", "null pointer", "BusFault"],
        "root_cause": "Null pointer dereference — accessing address 0x0",
        "fix": "Add null checks before pointer dereference. Check task notification/queue receive return values.",
        "severity": "critical",
        "auto_healable": False,
    },
    "hardfault_alignment": {
        "match": ["hardfault", "UsageFault", "UNALIGNED"],
        "root_cause": "Unaligned memory access on Cortex-M with strict alignment",
        "fix": "Use __attribute__((packed)) or memcpy for unaligned struct access",
        "severity": "high",
        "auto_healable": False,
    },
    "heap_exhaustion": {
        "match": ["malloc failed", "pvPortMalloc", "heap", "configTOTAL_HEAP_SIZE"],
        "root_cause": "FreeRTOS heap exhausted — too many allocations or memory leak",
        "fix": "Increase configTOTAL_HEAP_SIZE, use static allocation, or find memory leak",
        "severity": "critical",
        "auto_healable": True,
        "fix_template": "Increase configTOTAL_HEAP_SIZE by 50% in FreeRTOSConfig.h",
    },
    "watchdog_timeout": {
        "match": ["watchdog", "wdt", "timeout", "iwdg"],
        "root_cause": "Task blocked too long — missed watchdog feed deadline",
        "fix": "Check for infinite loops, blocked I/O, or priority inversion. Add watchdog feeds in long operations.",
        "severity": "high",
        "auto_healable": False,
    },
    "assertion_failure": {
        "match": ["assert", "configASSERT", "assertion failed"],
        "root_cause": "FreeRTOS kernel assertion — usually invalid parameters or ISR misuse",
        "fix": "Check if calling FreeRTOS API from wrong context (ISR vs task). Use FromISR variants in interrupts.",
        "severity": "high",
        "auto_healable": False,
    },
    "mqtt_disconnect": {
        "match": ["mqtt", "disconnect", "CONNACK", "keepalive"],
        "root_cause": "MQTT broker connection lost — network issue or keepalive timeout",
        "fix": "Implement reconnection logic with exponential backoff in MQTT task",
        "severity": "medium",
        "auto_healable": True,
        "fix_template": "Add reconnect with 1s/2s/4s/8s/16s backoff in sparkpilot_mqtt_task()",
    },
    "memory_corruption": {
        "match": ["corruption", "overwrite", "buffer overflow", "canary"],
        "root_cause": "Memory corruption — buffer overflow or use-after-free",
        "fix": "Enable stack canaries, use bounded string functions (snprintf, strlcpy), check array bounds",
        "severity": "critical",
        "auto_healable": False,
    },
}


def _get_db():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def _now():
    return datetime.now(timezone.utc).isoformat()


def analyze_crash(crash_type: str, device_id: str, stack_trace: str = "",
                  registers: dict | None = None, firmware_version: str = "") -> dict:
    """Analyze a crash dump and match against known patterns."""
    search_text = f"{crash_type} {stack_trace}".lower()

    matched_patterns = []
    for pattern_id, pattern in CRASH_PATTERNS.items():
        score = sum(1 for kw in pattern["match"] if kw.lower() in search_text)
        if score > 0:
            matched_patterns.append({
                "pattern_id": pattern_id,
                "match_score": score,
                "root_cause": pattern["root_cause"],
                "fix": pattern["fix"],
                "severity": pattern["severity"],
                "auto_healable": pattern.get("auto_healable", False),
                "fix_template": pattern.get("fix_template"),
            })

    matched_patterns.sort(key=lambda x: x["match_score"], reverse=True)

    best_match = matched_patterns[0] if matched_patterns else None
    confidence = min(1.0, (best_match["match_score"] / 3.0)) if best_match else 0.0

    crash_id = f"crash-{uuid.uuid4().hex[:8]}"
    analysis = {
        "crash_id": crash_id,
        "device_id": device_id,
        "crash_type": crash_type,
        "matched_patterns": matched_patterns[:3],
        "best_match": best_match,
        "confidence": confidence,
        "auto_healable": best_match["auto_healable"] if best_match else False,
        "recommendation": "auto_fix" if confidence >= 0.7 and best_match and best_match["auto_healable"]
                          else "suggest_fix" if confidence >= 0.3
                          else "escalate",
    }

    # Store in DB
    conn = _get_db()
    try:
        conn.execute(
            "INSERT INTO crash_dump_log "
            "(id, device_id, crash_type, stack_trace, registers, firmware_version, "
            "analysis, root_cause, auto_healed) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (crash_id, device_id, crash_type, stack_trace,
             json.dumps(registers or {}), firmware_version,
             json.dumps(analysis), best_match["root_cause"] if best_match else "unknown",
             1 if analysis["recommendation"] == "auto_fix" else 0),
        )
        conn.commit()
    except Exception:
        pass
    finally:
        conn.close()

    return analysis


def list_patterns() -> dict:
    """List all known crash patterns."""
    return {
        "status": "success",
        "pattern_count": len(CRASH_PATTERNS),
        "patterns": [
            {"id": k, "severity": v["severity"], "auto_healable": v.get("auto_healable", False),
             "root_cause": v["root_cause"]}
            for k, v in CRASH_PATTERNS.items()
        ],
    }


def main():
    parser = argparse.ArgumentParser(description="Crash Dump Analyzer")
    parser.add_argument("--crash-type", help="Crash type (hardfault, stack_overflow, watchdog, etc.)")
    parser.add_argument("--device-id", default="dev-sim", help="Device ID")
    parser.add_argument("--stack-trace", default="", help="Stack trace text")
    parser.add_argument("--firmware-version", default="", help="Firmware version")
    parser.add_argument("--analyze", action="store_true", help="Analyze a crash")
    parser.add_argument("--patterns", action="store_true", help="List known patterns")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    if args.patterns:
        result = list_patterns()
    elif args.crash_type:
        result = analyze_crash(args.crash_type, args.device_id, args.stack_trace,
                               firmware_version=args.firmware_version)
    else:
        parser.print_help()
        return

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()

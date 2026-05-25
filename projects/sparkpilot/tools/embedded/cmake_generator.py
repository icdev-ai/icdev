#!/usr/bin/env python3
# CUI // SP-CTI
"""CMake and FreeRTOSConfig.h generator for SparkPilot projects.

Generates build system files based on target board and project requirements.

Usage:
    python tools/embedded/cmake_generator.py --board esp32-s3 --project-dir ./my-project --json
    python tools/embedded/cmake_generator.py --board stm32f407 --with-tinyml --json
    python tools/embedded/cmake_generator.py --board simulator --json
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import uuid
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "sparkpilot.db"

# Board-specific configurations
BOARD_CONFIGS = {
    "esp32-s3": {
        "arch": "xtensa",
        "cpu": "Xtensa LX7",
        "flash_kb": 8192,
        "ram_kb": 512,
        "toolchain": "xtensa-esp32s3-elf",
        "freertos_port": "GCC/XTENSA_ESP32S3",
        "flash_tool": "esptool.py",
        "cmake_toolchain_file": "cmake/toolchain-esp32s3.cmake",
        "heap_size": 200000,
        "stack_size": 4096,
        "tick_rate_hz": 1000,
        "max_priorities": 25,
        "timer_task_stack": 4096,
    },
    "stm32f407": {
        "arch": "cortex-m4",
        "cpu": "ARM Cortex-M4F",
        "flash_kb": 1024,
        "ram_kb": 192,
        "toolchain": "arm-none-eabi",
        "freertos_port": "GCC/ARM_CM4F",
        "flash_tool": "STM32CubeProgrammer",
        "cmake_toolchain_file": "cmake/toolchain-arm-cm4f.cmake",
        "heap_size": 40000,
        "stack_size": 2048,
        "tick_rate_hz": 1000,
        "max_priorities": 16,
        "timer_task_stack": 2048,
    },
    "nrf52840": {
        "arch": "cortex-m4",
        "cpu": "ARM Cortex-M4F",
        "flash_kb": 1024,
        "ram_kb": 256,
        "toolchain": "arm-none-eabi",
        "freertos_port": "GCC/ARM_CM4F",
        "flash_tool": "nrfjprog",
        "cmake_toolchain_file": "cmake/toolchain-arm-cm4f.cmake",
        "heap_size": 60000,
        "stack_size": 2048,
        "tick_rate_hz": 1000,
        "max_priorities": 16,
        "timer_task_stack": 2048,
    },
    "rpi-pico": {
        "arch": "cortex-m0plus",
        "cpu": "ARM Cortex-M0+",
        "flash_kb": 2048,
        "ram_kb": 264,
        "toolchain": "arm-none-eabi",
        "freertos_port": "GCC/ARM_CM0",
        "flash_tool": "picotool",
        "cmake_toolchain_file": "cmake/toolchain-rp2040.cmake",
        "heap_size": 50000,
        "stack_size": 1024,
        "tick_rate_hz": 1000,
        "max_priorities": 8,
        "timer_task_stack": 1024,
    },
    "simulator": {
        "arch": "posix",
        "cpu": "Host",
        "flash_kb": 0,
        "ram_kb": 0,
        "toolchain": "gcc",
        "freertos_port": "GCC/Posix",
        "flash_tool": "none",
        "cmake_toolchain_file": "",
        "heap_size": 1048576,
        "stack_size": 16384,
        "tick_rate_hz": 1000,
        "max_priorities": 32,
        "timer_task_stack": 8192,
    },
}


def generate_cmakelists(board: str, project_name: str = "sparkpilot_app",
                        with_tinyml: bool = False, with_mqtt: bool = True) -> str:
    """Generate CMakeLists.txt for the given board."""
    cfg = BOARD_CONFIGS.get(board, BOARD_CONFIGS["simulator"])

    lines = [
        'cmake_minimum_required(VERSION 3.16)',
        f'project({project_name} C)',
        '',
        f'# Target: {board} ({cfg["cpu"]})',
        f'set(FREERTOS_PORT "{cfg["freertos_port"]}")',
        '',
    ]

    if cfg["cmake_toolchain_file"]:
        lines.append(f'set(CMAKE_TOOLCHAIN_FILE "${{CMAKE_SOURCE_DIR}}/{cfg["cmake_toolchain_file"]}")')
        lines.append('')

    lines.extend([
        '# FreeRTOS kernel',
        'set(FREERTOS_KERNEL_PATH "${CMAKE_SOURCE_DIR}/lib/FreeRTOS-Kernel")',
        'add_subdirectory(${FREERTOS_KERNEL_PATH} FreeRTOS)',
        '',
        '# SparkPilot Device SDK',
        'add_subdirectory(${CMAKE_SOURCE_DIR}/sdk sparkpilot_sdk)',
        '',
        '# Application sources',
        'file(GLOB APP_SOURCES "src/*.c")',
        'file(GLOB HAL_SOURCES "src/hal/*.c")',
        '',
        f'add_executable({project_name}',
        '    ${APP_SOURCES}',
        '    ${HAL_SOURCES}',
        ')',
        '',
        f'target_include_directories({project_name} PRIVATE',
        '    ${CMAKE_SOURCE_DIR}/include',
        '    ${CMAKE_SOURCE_DIR}/sdk/include',
        '    ${CMAKE_SOURCE_DIR}/config',
        ')',
        '',
        f'target_link_libraries({project_name} PRIVATE',
        '    freertos_kernel',
        '    sparkpilot_sdk',
    ])

    if with_mqtt:
        lines.append('    coreMQTT')
    if with_tinyml:
        lines.append('    tensorflow-lite-micro')

    lines.extend([
        ')',
        '',
    ])

    if board != "simulator":
        lines.extend([
            '# Flash size optimization',
            f'target_compile_options({project_name} PRIVATE -Os -ffunction-sections -fdata-sections)',
            f'target_link_options({project_name} PRIVATE -Wl,--gc-sections)',
            '',
        ])

    return '\n'.join(lines)


def generate_freertos_config(board: str, with_tinyml: bool = False) -> str:
    """Generate FreeRTOSConfig.h optimized for the target board."""
    cfg = BOARD_CONFIGS.get(board, BOARD_CONFIGS["simulator"])

    return f'''#ifndef FREERTOS_CONFIG_H
#define FREERTOS_CONFIG_H

/* --- SparkPilot FreeRTOSConfig.h --- */
/* Board: {board} | CPU: {cfg["cpu"]} | Auto-generated by SparkPilot */

/* Scheduler */
#define configUSE_PREEMPTION                    1
#define configUSE_PORT_OPTIMISED_TASK_SELECTION  {"0" if board == "simulator" else "1"}
#define configUSE_TICKLESS_IDLE                 {"0" if board == "simulator" else "1"}
#define configCPU_CLOCK_HZ                      ({"1000000" if board == "simulator" else "SystemCoreClock"})
#define configTICK_RATE_HZ                      ((TickType_t){cfg["tick_rate_hz"]})
#define configMAX_PRIORITIES                    ({cfg["max_priorities"]})
#define configMINIMAL_STACK_SIZE                ((configSTACK_DEPTH_TYPE)128)
#define configMAX_TASK_NAME_LEN                 (16)
#define configUSE_16_BIT_TICKS                  0
#define configIDLE_SHOULD_YIELD                 1

/* Memory */
#define configTOTAL_HEAP_SIZE                   ((size_t){cfg["heap_size"]})
#define configSUPPORT_STATIC_ALLOCATION         1
#define configSUPPORT_DYNAMIC_ALLOCATION        1
#define configAPPLICATION_ALLOCATED_HEAP         0
#define configSTACK_ALLOCATION_FROM_SEPARATE_HEAP 0
#define configUSE_HEAP_SCHEME                   4  /* heap_4: coalescence, best fit */

/* Task features */
#define configUSE_MUTEXES                       1
#define configUSE_RECURSIVE_MUTEXES             1
#define configUSE_COUNTING_SEMAPHORES           1
#define configUSE_TASK_NOTIFICATIONS            1
#define configTASK_NOTIFICATION_ARRAY_ENTRIES    3
#define configQUEUE_REGISTRY_SIZE               8

/* Timer */
#define configUSE_TIMERS                        1
#define configTIMER_TASK_PRIORITY               (configMAX_PRIORITIES - 1)
#define configTIMER_QUEUE_LENGTH                10
#define configTIMER_TASK_STACK_DEPTH            ({cfg["timer_task_stack"]} / sizeof(StackType_t))

/* Hooks */
#define configUSE_IDLE_HOOK                     0
#define configUSE_TICK_HOOK                     0
#define configUSE_MALLOC_FAILED_HOOK            1
#define configCHECK_FOR_STACK_OVERFLOW          2  /* Method 2: pattern check */

/* Stats (enabled for SparkPilot task visualizer) */
#define configGENERATE_RUN_TIME_STATS           1
#define configUSE_TRACE_FACILITY                1
#define configUSE_STATS_FORMATTING_FUNCTIONS    1

/* Co-routines (disabled — use tasks) */
#define configUSE_CO_ROUTINES                   0

/* Assert */
#define configASSERT(x) if ((x) == 0) {{ taskDISABLE_INTERRUPTS(); for(;;); }}

/* Interrupt nesting (Cortex-M only) */
{"" if board == "simulator" else '''#define configKERNEL_INTERRUPT_PRIORITY          255
#define configMAX_SYSCALL_INTERRUPT_PRIORITY     191
#define configLIBRARY_KERNEL_INTERRUPT_PRIORITY  15
#define configLIBRARY_MAX_SYSCALL_INTERRUPT_PRIORITY 5'''}

/* SparkPilot SDK task defaults */
#define configSPARKPILOT_MQTT_TASK_STACK        (2048 / sizeof(StackType_t))
#define configSPARKPILOT_CMD_TASK_STACK         (1024 / sizeof(StackType_t))
#define configSPARKPILOT_TELEMETRY_TASK_STACK   (1024 / sizeof(StackType_t))
{"#define configSPARKPILOT_INFERENCE_TASK_STACK   (4096 / sizeof(StackType_t))" if with_tinyml else ""}

/* POSIX simulator overrides */
{"#define configUSE_POSIX_ERRNO 1" if board == "simulator" else ""}

#endif /* FREERTOS_CONFIG_H */
'''


def generate(board: str, project_name: str = "sparkpilot_app",
             project_dir: str | None = None,
             with_tinyml: bool = False) -> dict:
    """Generate CMakeLists.txt and FreeRTOSConfig.h."""
    cmake = generate_cmakelists(board, project_name, with_tinyml)
    freertos_cfg = generate_freertos_config(board, with_tinyml)
    cfg = BOARD_CONFIGS.get(board, BOARD_CONFIGS["simulator"])

    result = {
        "status": "success",
        "board": board,
        "arch": cfg["arch"],
        "cpu": cfg["cpu"],
        "toolchain": cfg["toolchain"],
        "cmake_content": cmake,
        "freertos_config_content": freertos_cfg,
    }

    if project_dir:
        out = Path(project_dir)
        out.mkdir(parents=True, exist_ok=True)
        (out / "CMakeLists.txt").write_text(cmake)
        (out / "config").mkdir(exist_ok=True)
        (out / "config" / "FreeRTOSConfig.h").write_text(freertos_cfg)
        result["files_written"] = [
            str(out / "CMakeLists.txt"),
            str(out / "config" / "FreeRTOSConfig.h"),
        ]

    # Store in DB
    try:
        conn = sqlite3.connect(str(DB_PATH))
        conn.execute(
            "INSERT OR REPLACE INTO cmake_configs "
            "(id, project_id, board_id, cmake_content, freertos_config_content, optimization_level) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (f"cmake-{uuid.uuid4().hex[:8]}", project_name, board, cmake, freertos_cfg, "-Os"),
        )
        conn.commit()
        conn.close()
    except Exception:
        pass

    return result


def main():
    parser = argparse.ArgumentParser(description="CMake and FreeRTOSConfig.h generator")
    parser.add_argument("--board", required=True, choices=list(BOARD_CONFIGS.keys()),
                        help="Target board")
    parser.add_argument("--project-name", default="sparkpilot_app")
    parser.add_argument("--project-dir", help="Write files to directory")
    parser.add_argument("--with-tinyml", action="store_true", help="Include TFLite Micro")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    result = generate(args.board, args.project_name, args.project_dir, args.with_tinyml)

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(f"Board: {result['board']} ({result['cpu']})")
        print(f"Toolchain: {result['toolchain']}")
        print("\n--- CMakeLists.txt ---")
        print(result["cmake_content"])
        print("\n--- FreeRTOSConfig.h ---")
        print(result["freertos_config_content"])


if __name__ == "__main__":
    main()

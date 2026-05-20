#!/usr/bin/env python3
# CUI // SP-CTI
"""Seed LOG-01…LOG-13 Kanban tasks for the structured logging build automation system.

Run once after plan approval to queue tasks for the Kanban scheduler.

Usage:
    python tools/testing/seed_logging_kanban.py
    python tools/testing/seed_logging_kanban.py --base http://localhost:5050
    python tools/testing/seed_logging_kanban.py --dry-run
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))


TASKS = [
    {
        "key": "LOG-01",
        "title": "LOG-01: Create tools/logging/icdev_logger.py + __init__.py",
        "priority": "critical",
        "depends_on": None,
        "description": (
            "Create the central NDJSON logger for all ICDEV components.\n\n"
            "Files:\n"
            "  - tools/logging/__init__.py  (re-exports get_logger)\n"
            "  - tools/logging/icdev_logger.py  (_JsonFormatter, get_logger, invalidate_cache)\n\n"
            "Requirements:\n"
            "  - get_logger(component: str) -> logging.Logger\n"
            "  - Dual rotation: TimedRotatingFileHandler (daily) + RotatingFileHandler (10 MB)\n"
            "  - Config loaded from args/logging_config.yaml\n"
            "  - Logger cache (dict) — returns same instance for same component name\n"
            "  - NDJSON fields: ts, level, component, message, trace_id, session_id, extra\n"
        ),
    },
    {
        "key": "LOG-02",
        "title": "LOG-02: Create args/logging_config.yaml",
        "priority": "critical",
        "depends_on": "LOG-01",
        "description": (
            "Create args/logging_config.yaml with:\n"
            "  global_level: INFO\n"
            "  log_dir: .logs\n"
            "  rotation: {when: midnight, retention_days: 30, max_bytes: 10485760}\n"
            "  component_overrides: {genesis_daemon: {level: DEBUG}, ...}\n"
        ),
    },
    {
        "key": "LOG-03",
        "title": "LOG-03: tests/test_icdev_logger.py",
        "priority": "critical",
        "depends_on": "LOG-02",
        "description": (
            "Unit tests for tools.logging.icdev_logger:\n"
            "  - _JsonFormatter produces valid NDJSON with required fields\n"
            "  - get_logger returns same instance (cache)\n"
            "  - Log dir created automatically\n"
            "  - Written file is valid JSON\n"
            "  - Component level override respected\n"
            "  - invalidate_cache resets logger instance\n"
        ),
    },
    {
        "key": "LOG-04",
        "title": "LOG-04: Create tools/logging/build_logger.py",
        "priority": "critical",
        "depends_on": "LOG-01",
        "description": (
            "Captures pytest + Playwright runs to .logs/build.ndjson.\n\n"
            "Functions:\n"
            "  - capture_pytest(returncode, stdout, stderr, duration_s)\n"
            "  - capture_playwright(returncode, stdout, passed, failed, skipped, duration_s)\n"
            "  - _write_build_event(event_type, data)  — shared NDJSON appender\n"
            "  - _parse_pytest_failures(stdout) -> list[{test, error}]\n"
            "  - _parse_playwright_failures(stdout) -> list[{browser, test, error}]\n\n"
            "CLI: python tools/logging/build_logger.py --self-test\n"
        ),
    },
    {
        "key": "LOG-05",
        "title": "LOG-05: tests/test_build_logger.py",
        "priority": "high",
        "depends_on": "LOG-04",
        "description": (
            "Unit tests for tools.logging.build_logger:\n"
            "  - capture_pytest writes pytest_run event\n"
            "  - capture_playwright writes playwright_run event\n"
            "  - returncode != 0 sets level=ERROR\n"
            "  - Failure lines parsed from stdout\n"
            "  - Explicit count params override stdout parse\n"
        ),
    },
    {
        "key": "LOG-06",
        "title": "LOG-06: Create tools/genesis/reflexes/log_triage.py",
        "priority": "critical",
        "depends_on": "LOG-04",
        "description": (
            "Genesis reflex: reads .logs/build.ndjson → deduplicates → Kanban tasks.\n\n"
            "Functions:\n"
            "  - run(config, trust) -> dict  (main entry point)\n"
            "  - _read_ndjson_tail(path, lines=500) -> list\n"
            "  - _extract_signatures(events) -> list  (dedup by component+message_hash[:8])\n"
            "  - _load_seen() / _save_seen(seen)  (.tmp/genesis/log_triage_seen.json)\n"
            "  - _create_task(sig) -> bool  (POST /api/kanban/tasks via urllib)\n\n"
            "Schedule: every 30m, risk_tier: green\n"
            "Returns: {reflex, events_scanned, signatures_seen, new_signatures, tasks_created}\n"
        ),
    },
    {
        "key": "LOG-07",
        "title": "LOG-07: Register log_triage in daemon.py + genesis_config.yaml",
        "priority": "high",
        "depends_on": "LOG-06",
        "description": (
            "1. Add 'log_triage' to REFLEX_NAMES list in tools/genesis/daemon.py\n"
            "2. Add log_triage block to args/genesis_config.yaml under reflexes:\n"
            "   log_triage:\n"
            "     enabled: true\n"
            "     risk_tier: green\n"
            "     schedule: 'every 30m'\n"
            "     interval_seconds: 1800\n"
            "     tail_lines: 500\n"
            "     build_log: .logs/build.ndjson\n"
        ),
    },
    {
        "key": "LOG-08",
        "title": "LOG-08: tests/test_log_triage.py",
        "priority": "high",
        "depends_on": "LOG-06",
        "description": (
            "Unit tests for tools.genesis.reflexes.log_triage:\n"
            "  - _read_ndjson_tail: empty for missing file, skips invalid JSON, respects limit\n"
            "  - _extract_signatures: deduplicates same (component, message), filters INFO\n"
            "  - run: no tasks if no failures, creates task for new sig, skips seen sig\n"
        ),
    },
    {
        "key": "LOG-09",
        "title": "LOG-09: Add check_log_standard_compliance() to coherence_checker.py",
        "priority": "high",
        "depends_on": "LOG-01",
        "description": (
            "Add check_log_standard_compliance() to tools/workflow/coherence_checker.py.\n\n"
            "Logic:\n"
            "  - Grep tools/ Python files for 'logging.getLogger('\n"
            "  - Exclude tools/logging/ itself and tests/ directories\n"
            "  - Return CoherenceCheck with status='fail' if any violations found\n\n"
            "Register in CHECK_REGISTRY as 'log_standard'.\n"
            "Runs with --all; blocks --gate if violations exist.\n"
        ),
    },
    {
        "key": "LOG-10",
        "title": "LOG-10: tests/test_coherence_log_standard.py",
        "priority": "high",
        "depends_on": "LOG-09",
        "description": (
            "Unit tests for check_log_standard_compliance:\n"
            "  - Pass when no raw logging.getLogger() calls\n"
            "  - Fail when tools/ module uses logging.getLogger()\n"
            "  - Exclude tools/logging/ package from violations\n"
            "  - check_id == 'log_standard'\n"
            "  - Registered in CHECK_REGISTRY\n"
        ),
    },
    {
        "key": "LOG-11",
        "title": "LOG-11: log_health_metrics table + /api/code-quality/log-health endpoints",
        "priority": "high",
        "depends_on": "LOG-04",
        "description": (
            "Modify tools/dashboard/api/code_quality.py:\n\n"
            "1. CREATE TABLE IF NOT EXISTS log_health_metrics (id, ts, component, level, count)\n"
            "2. GET /api/code-quality/log-health → {error_count_24h, warn_count_24h,\n"
            "   last_build_status, last_build_ts, last_build_event_type,\n"
            "   last_build_passed, last_build_failed, triage_tasks_created_7d}\n"
            "3. GET /api/code-quality/log-health/latest → most recent build event\n\n"
            "Data source: .logs/build.ndjson (read directly, no extra DB writes needed)\n"
        ),
    },
    {
        "key": "LOG-12",
        "title": "LOG-12: 'Log Health' card on /code-quality dashboard",
        "priority": "high",
        "depends_on": "LOG-11",
        "description": (
            "Modify tools/dashboard/templates/code_quality.html:\n\n"
            "Add 'Log Health' section below main stat grid:\n"
            "  - stat cards: Last Build (PASS/FAIL), Errors 24h, Warnings 24h,\n"
            "    Triage Tasks 7d, Last Passed, Last Failed\n"
            "  - JS: loadLogHealth() fetches /api/code-quality/log-health,\n"
            "    populates cards, colours status green/red\n"
            "  - Timestamp line below cards\n"
            "  - Called in initial load block alongside loadSummary()\n"
        ),
    },
    {
        "key": "LOG-13",
        "title": "LOG-13: Manifest + docs update + companion sync + coherence gate",
        "priority": "medium",
        "depends_on": "LOG-12",
        "description": (
            "Final integration and verification:\n\n"
            "1. Add tools/logging entry to tools/manifest/memory-system.md (or create logging.md)\n"
            "2. Run: python tools/dx/companion.py --sync --write --json\n"
            "3. Run: python tools/workflow/coherence_checker.py --all --fix --gate\n"
            "4. Run: pytest tests/test_icdev_logger.py tests/test_build_logger.py "
            "tests/test_log_triage.py tests/test_coherence_log_standard.py -v\n"
            "5. Smoke test logger: python -c "
            "\"from tools.logging.icdev_logger import get_logger; get_logger('smoke').info('ok')\"\n"
            "6. Verify .logs/smoke.ndjson exists with one JSON line\n"
            "7. Verify http://localhost:5050/code-quality shows Log Health card\n"
            "8. Commit with message: 'feat: structured NDJSON logging + build automation (LOG-01..13)'\n"
        ),
    },
]


def seed(base_url: str, dry_run: bool = False) -> None:
    created: dict[str, str] = {}  # key → task_id

    for task in TASKS:
        dep_key = task.get("depends_on")
        dep_id = created.get(dep_key) if dep_key else None

        payload = {
            "title": task["title"],
            "task_type": "chore",
            "priority": task["priority"],
            "status": "backlog",
            "description": task["description"],
        }
        if dep_id:
            payload["depends_on_task_id"] = dep_id

        if dry_run:
            print(f"[DRY-RUN] Would create: {task['title']} (depends_on={dep_key})")
            created[task["key"]] = f"fake-{task['key']}"
            continue

        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            f"{base_url}/api/kanban/tasks",
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                body = json.loads(resp.read().decode("utf-8"))
                task_id = body.get("id", "?")
                created[task["key"]] = task_id
                print(f"Created: {task_id} — {task['title']}")
        except Exception as exc:
            print(f"ERROR creating {task['key']}: {exc}", file=sys.stderr)
            created[task["key"]] = f"failed-{task['key']}"

    print(f"\nDone. {len(TASKS)} tasks queued with dependency chain.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed LOG-01..13 Kanban tasks")
    parser.add_argument("--base", default="http://localhost:5050")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    seed(args.base, args.dry_run)


if __name__ == "__main__":
    main()

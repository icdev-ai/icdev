#!/usr/bin/env python3
"""
seed_playwright_kanban.py — One-shot script to queue Playwright E2E test tasks
into the ICDEV™ Kanban board with explicit dependency chain.

Usage:
    python tools/testing/seed_playwright_kanban.py [--base-url http://localhost:5050]
"""
import argparse
import sys
import requests

TASKS = [
    {
        "key": "scaffold",
        "title": "E2E: Scaffold test infra + helpers",
        "priority": "critical",
        "description": (
            "Set up shared Playwright helpers (screenshot dir, CUI banner assertion, "
            "request utilities). Verify playwright.config.ts targets localhost:5050 "
            "and .tmp/test_runs/screenshots/ exists."
        ),
        "depends_on": None,
    },
    {
        "key": "chat_uc",
        "title": "E2E: chat_use_cases.spec.ts — catalog + quick_action URLs",
        "priority": "critical",
        "description": (
            "Run tests/e2e/chat_use_cases.spec.ts: 12 tests covering use case sidebar "
            "visibility, activation click, and all quick_action URL HTTP status checks."
        ),
        "depends_on": "scaffold",
    },
    {
        "key": "nav_smoke",
        "title": "E2E: nav_smoke.spec.ts — all nav links resolve",
        "priority": "high",
        "description": (
            "Run tests/e2e/nav_smoke.spec.ts: 8 tests covering all 8 nav menu items "
            "and verifying no nav link returns HTTP 404."
        ),
        "depends_on": "scaffold",
    },
    {
        "key": "key_pages",
        "title": "E2E: key_pages_smoke.spec.ts — quick-action target pages",
        "priority": "high",
        "description": (
            "Run tests/e2e/key_pages_smoke.spec.ts: 16 tests covering all quick-action "
            "destination pages (/kanban, /cpmp, /oscal, /writeguard, /supply_chain, etc.)."
        ),
        "depends_on": "scaffold",
    },
    {
        "key": "right_panels",
        "title": "E2E: chat_right_panels.spec.ts — RICOAS/GOV/INTEL tabs",
        "priority": "high",
        "description": (
            "Run tests/e2e/chat_right_panels.spec.ts: 9 tests covering tab DOM presence, "
            "spinner element attachment, and content container visibility."
        ),
        "depends_on": "chat_uc",
    },
    {
        "key": "canvas_smoke",
        "title": "E2E: canvas_smoke.spec.ts — all 11 canvases",
        "priority": "medium",
        "description": (
            "Run tests/e2e/canvas_smoke.spec.ts: 22 parameterized tests (11 canvases × "
            "load-without-error + IQE widget presence)."
        ),
        "depends_on": "nav_smoke",
    },
    {
        "key": "flows",
        "title": "E2E: chat_use_case_flows.spec.ts — end-to-end flows",
        "priority": "high",
        "description": (
            "Run tests/e2e/chat_use_case_flows.spec.ts: 7 tests covering activation flow, "
            "tab switching, GOV/INTEL content, and no broken static resources."
        ),
        "depends_on": "chat_uc",
    },
    {
        "key": "kanban_api",
        "title": "E2E: kanban_api.spec.ts — CRUD + dependency",
        "priority": "medium",
        "description": (
            "Run tests/e2e/kanban_api.spec.ts: 5 tests covering Kanban task creation, "
            "list retrieval, status move, dependency wiring, and board UI columns."
        ),
        "depends_on": "scaffold",
    },
    {
        "key": "error_handling",
        "title": "E2E: chat_error_handling.spec.ts — spinner, 429, favicon",
        "priority": "medium",
        "description": (
            "Run tests/e2e/chat_error_handling.spec.ts: 6 tests covering send button, "
            "typing indicator DOM, processing bar states, favicon 204, and zero uncaught errors."
        ),
        "depends_on": "right_panels",
    },
    {
        "key": "verify_all",
        "title": "E2E: Run full suite + fix failures before demo",
        "priority": "critical",
        "description": (
            "Run: npx playwright test tests/e2e/ --reporter=html\n"
            "Review .tmp/test_runs/playwright-report/index.html. Fix all failures. "
            "Target: 0 failing tests before leadership demo."
        ),
        "depends_on": "flows",
    },
    {
        "key": "ci_wiring",
        "title": "E2E: Wire suite into CI/CD pipeline",
        "priority": "high",
        "description": (
            "Add playwright test step to .gitlab-ci.yml or GitHub Actions workflow. "
            "Set ICDEV_NO_SERVER=1 when server is already up in CI. "
            "Archive playwright-report as CI artifact."
        ),
        "depends_on": "verify_all",
    },
]


def seed(base_url: str) -> None:
    created: dict[str, str] = {}

    for task in TASKS:
        dep_key = task.get("depends_on")
        dep_id = created.get(dep_key) if dep_key else None

        payload: dict = {
            "title": task["title"],
            "task_type": "test",
            "priority": task["priority"],
            "status": "backlog",
            "description": task["description"],
        }
        if dep_id:
            payload["depends_on_task_id"] = dep_id

        try:
            resp = requests.post(f"{base_url}/api/kanban/tasks", json=payload, timeout=10)
            resp.raise_for_status()
        except requests.RequestException as exc:
            print(f"ERROR creating '{task['title']}': {exc}", file=sys.stderr)
            sys.exit(1)

        body = resp.json()
        task_id = body.get("id") or body.get("task_id")
        if not task_id:
            print(f"ERROR: No id in response for '{task['title']}': {body}", file=sys.stderr)
            sys.exit(1)

        created[task["key"]] = task_id
        dep_note = f" (depends on {dep_id})" if dep_id else ""
        print(f"  Created {task_id}{dep_note} — {task['title']}")

    print(f"\nDone. {len(TASKS)} tasks queued with dependency chain.")
    print(f"View board: {base_url}/kanban")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Seed Playwright E2E tasks into Kanban")
    parser.add_argument("--base-url", default="http://localhost:5050")
    args = parser.parse_args()
    seed(args.base_url)

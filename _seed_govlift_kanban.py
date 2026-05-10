#!/usr/bin/env python3
# CUI // SP-CTI
"""Seed Kanban tasks for the GovLift DoD IL4 Cloud Migration Tool build.

Creates atomic build tasks (gv-ep1-xx through gv-ep6-xx) reflecting work
completed and work remaining. Idempotent — skips tasks whose IDs already exist.

Usage:
    python _seed_govlift_kanban.py
    python _seed_govlift_kanban.py --dry-run
"""
import argparse
import sys
from datetime import datetime, timezone

sys.path.insert(0, ".")
from tools.db.storage import get_connection

NOW = datetime.now(timezone.utc).isoformat()

TASKS = [
    # ep1 — Security Foundation
    dict(id="gv-ep1-01", title="tools/govlift/constants.py — enum constants: statuses, types, severities, risk levels, classification markings (CUI // SP-CTI, IL4)", task_type="build", priority="high", status="done"),
    dict(id="gv-ep1-02", title="tools/govlift/db/init_db.py — init_govlift_db(): 6 tables via get_connection() + translate_sql()", task_type="build", priority="high", status="done"),
    dict(id="gv-ep1-03", title="tools/govlift/audit_engine.py — NIST AU append-only audit log: log_action(), list_audit_log(), get_audit_summary(); 7-yr retention", task_type="build", priority="high", status="done"),
    dict(id="gv-ep1-04", title="tools/govlift/stig_checker.py — DISA STIG checks: list/create/update/scan (10 RHEL-09 checks); CAT1/2/3 severity; run_quick_scan()", task_type="build", priority="high", status="done"),
    # ep2 — Core Functional
    dict(id="gv-ep2-01", title="tools/govlift/workload_scanner.py — workload inventory CRUD: list_workloads(), create_workload(), update_workload_status(), assign_wave(), get_scanner_summary()", task_type="build", priority="high", status="done"),
    dict(id="gv-ep2-02", title="tools/govlift/wave_planner.py — migration wave lifecycle: list_waves(), create_wave(), update_wave_status(), get_wave_summary()", task_type="build", priority="high", status="done"),
    dict(id="gv-ep2-03", title="tools/govlift/migration_executor.py — job tracker: list/create/start/complete/rollback migrations; phase timings; get_migration_summary()", task_type="build", priority="high", status="done"),
    # ep3 — Integration & IQE
    dict(id="gv-ep3-01", title="tools/govlift/blueprint.py — Flask Blueprint (url_prefix=''): 6 page routes + 18 API routes + IQE query; data dict pattern matching OHC", task_type="build", priority="high", status="done"),
    dict(id="gv-ep3-02", title="tools/iqe/adapters/govlift.py — 5 IQE collections: workloads, waves, migrations, stig, audit; graceful table-missing handling", task_type="build", priority="medium", status="done"),
    dict(id="gv-ep3-03", title="context/iqe/queries/govlift/seed.yaml — 8 seed natural-language queries for govlift IQE adapter", task_type="build", priority="medium", status="done"),
    # ep4 — Templates (6 pages)
    dict(id="gv-ep4-01", title="templates/govlift/index.html — executive overview: 4 stat cards (workloads/waves/migrations/audit), summary panels, IQE widget", task_type="build", priority="high", status="done"),
    dict(id="gv-ep4-02", title="templates/govlift/workloads.html — workload inventory: filter bar (status/risk), dark-theme table with risk badges, IQE widget", task_type="build", priority="high", status="done"),
    dict(id="gv-ep4-03", title="templates/govlift/waves.html — wave planner: wave cards with workload count, schedule, status badges, IQE widget", task_type="build", priority="high", status="done"),
    dict(id="gv-ep4-04", title="templates/govlift/executor.html — job tracker: migration table with phase timings, start/complete/rollback actions, IQE widget", task_type="build", priority="high", status="done"),
    dict(id="gv-ep4-05", title="templates/govlift/stig.html — STIG compliance: CAT1/2/3 breakdown, status filter, DISA finding table, IQE widget", task_type="build", priority="high", status="done"),
    dict(id="gv-ep4-06", title="templates/govlift/audit.html — audit log viewer: 200-entry paginated table, user/action/resource/IP columns, IQE widget", task_type="build", priority="high", status="done"),
    # ep5 — Wiring (app.py, base.html, conftest, hooks)
    dict(id="gv-ep5-01", title="tools/dashboard/app.py — register govlift blueprint: init_govlift_db() at startup, _CANVAS_FLAGS, IQE _CANVAS_MAP + iqe_dispatch()", task_type="build", priority="high", status="done"),
    dict(id="gv-ep5-02", title="base.html — govlift_enabled nav block (6 links, #34d399 green) + PATH_CANVAS entry [/^\\/govlift/, 'govlift']", task_type="build", priority="high", status="done"),
    dict(id="gv-ep5-03", title="tests/conftest.py — add 6 govlift table schemas to MINIMAL_ICDEV_SCHEMA (SQLite-compatible DDL)", task_type="chore", priority="medium", status="done"),
    dict(id="gv-ep5-04", title=".claude/hooks/pre_tool_use.py — add govlift_audit_log to APPEND_ONLY_TABLES (NIST AU immutability gate)", task_type="chore", priority="high", status="done"),
    # ep6 — V&V, Docs, Manifest
    dict(id="gv-ep6-01", title="tools/manifest/govlift.md — manifest shard: 8 tools, 6 DB tables, route table (page + API)", task_type="chore", priority="medium", status="done"),
    dict(id="gv-ep6-02", title="args/projects.yaml — register GovLift project: key=gv, task_prefix=gv-, 6 epics, intake + chat context refs", task_type="chore", priority="medium", status="done"),
    dict(id="gv-ep6-03", title="Restart dashboard server (PID 53424) to load rewritten govlift blueprint — verifies all 13 routes 200 OK on live server", task_type="chore", priority="high", status="scheduled", scheduled_at=NOW),
    dict(id="gv-ep6-04", title="tests/e2e/e2e_govlift_lifecycle.py — full Selenium lifecycle: workload create → wave assign → migration start → complete → STIG scan → audit verify", task_type="test", priority="high", status="scheduled", scheduled_at=NOW),
    dict(id="gv-ep6-05", title="Chat-Kanban integration: kanban_bridge.py + /api/chat/<ctx>/tasks + right-panel Tasks tab + 081 build sync extension", task_type="build", priority="high", status="scheduled", scheduled_at=NOW),
]


def seed(dry_run: bool = False) -> None:
    conn = get_connection()
    existing = {r["id"] for r in conn.execute("SELECT id FROM kanban_tasks").fetchall()}
    inserted = skipped = 0

    for t in TASKS:
        if t["id"] in existing:
            skipped += 1
            continue

        completed_at = NOW if t["status"] == "done" else None
        sa = t.get("scheduled_at")

        if dry_run:
            print(f"  DRY [{t['status']}] {t['id']} {t['title'][:60]}")
        else:
            conn.execute(
                """INSERT INTO kanban_tasks
                   (id, title, task_type, priority, status, scheduled_at, completed_at,
                    dispatch_source, created_at, updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (
                    t["id"], t["title"], t["task_type"], t["priority"], t["status"],
                    sa, completed_at, "govlift-build", NOW, NOW,
                ),
            )
            conn.commit()
            inserted += 1
            print(f"  [{t['status']}] {t['id']}")

    print(f"\n  Inserted: {inserted}  Skipped (already exist): {skipped}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    seed(dry_run=args.dry_run)

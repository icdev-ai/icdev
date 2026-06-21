# CUI // SP-CTI
"""Seed Kanban tasks for the NSA ZIG Full Implementation.

Project: zig  (task_prefix 'zig-')
Plan: C:/Users/schuo/.claude/plans/snappy-toasting-penguin.md

Run:
    python tools/kanban/seed_zig_kanban.py --dry-run
    python tools/kanban/seed_zig_kanban.py
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

_BASE = Path(__file__).resolve().parents[2]
if str(_BASE) not in sys.path:
    sys.path.insert(0, str(_BASE))

from tools.db.storage import get_connection  # noqa: E402

PROJECT_ID = "zig"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


TASKS = [
    # ── Epic: db ─────────────────────────────────────────────────────────────
    {
        "id": "zig-db-01",
        "title": "ZIG DB: Add ZIG tables to security_canvas db/init_db.py",
        "description": "Create zig_pillars, zig_capabilities, zig_activities, zig_activity_completions, zig_maturity_scores tables with SQLite+PG dual schema. COMPLETED: Tables created and seeded.",
        "status": "done",
        "epic": "db", "priority": "critical",
    },
    {
        "id": "zig-db-02",
        "title": "ZIG DB: Seed 7 ZIG pillars (User, Device, Network, App, Data, Visibility, Automation)",
        "description": "Insert 7 pillar rows with slug, name, weight, color, CSI URL. COMPLETED: 7 pillars seeded.",
        "status": "done",
        "epic": "db", "priority": "critical",
    },
    {
        "id": "zig-db-03",
        "title": "ZIG DB: Seed 42 ZIG target-level capabilities",
        "description": "Insert 42 capability rows mapped to pillars and phases. COMPLETED: 42 capabilities seeded.",
        "status": "done",
        "epic": "db", "priority": "critical",
    },
    {
        "id": "zig-db-04",
        "title": "ZIG DB: Seed 91 ZIG activities (Discovery=14, Phase1=36, Phase2=41)",
        "description": "Insert 91 activity rows with NIST control references. COMPLETED: 91 activities seeded.",
        "status": "done",
        "epic": "db", "priority": "critical",
    },
    {
        "id": "zig-db-05",
        "title": "ZIG DB: Add ZIG constants to tools/security_canvas/constants.py",
        "description": "Add ZIG_PILLARS, ZIG_CAPABILITIES, ZIG_ACTIVITIES, ZIG_MATURITY_LEVELS, ZIG_PHASES constants. COMPLETED.",
        "status": "done",
        "epic": "db", "priority": "critical",
    },
    {
        "id": "zig-db-06",
        "title": "ZIG DB: Add ZIG tables to conftest.py MINIMAL_ICDEV_SCHEMA",
        "description": "Add zig_* table schemas to tests/conftest.py to prevent test failures.",
        "status": "open",
        "epic": "db", "priority": "high",
    },
    {
        "id": "zig-db-07",
        "title": "ZIG DB: Register ZIG project in args/projects.yaml",
        "description": "Add zig project entry with task_prefix=zig-, 7 epics, briefs. COMPLETED.",
        "status": "done",
        "epic": "db", "priority": "medium",
    },
    # ── Epic: engine ─────────────────────────────────────────────────────────
    {
        "id": "zig-engine-01",
        "title": "ZIG Engine: Create zig_assessor.py — main assessment entry point",
        "description": "run_zig_assessment(): scores all 7 pillars, identifies gaps, persists to zig_maturity_scores. Bridges to zta_maturity_scorer.py. COMPLETED.",
        "status": "done",
        "epic": "engine", "priority": "critical",
    },
    {
        "id": "zig-engine-02",
        "title": "ZIG Engine: Create zig_pillar_scorer.py — per-pillar maturity scoring",
        "description": "score_pillar(), score_all_pillars(), aggregate_zig_score(). Activity completion (60%) + capability rate (40%). COMPLETED.",
        "status": "done",
        "epic": "engine", "priority": "critical",
    },
    {
        "id": "zig-engine-03",
        "title": "ZIG Engine: Create zig_activity_tracker.py — activity completion CRUD",
        "description": "set_activity_status(), get_phase_completions(), bulk_activity_status(). COMPLETED.",
        "status": "done",
        "epic": "engine", "priority": "critical",
    },
    {
        "id": "zig-engine-04",
        "title": "ZIG Engine: Create zig_phase_tracker.py — phase completion metrics",
        "description": "get_all_phases_status(), compute_fy2027_readiness(), get_capability_status_by_pillar(). COMPLETED.",
        "status": "done",
        "epic": "engine", "priority": "critical",
    },
    {
        "id": "zig-engine-05",
        "title": "ZIG Engine: Create zig_roadmap_generator.py — FY2027/FY2032 roadmap JSON",
        "description": "generate_roadmap(): milestones, pillar progress, deadline tracking. COMPLETED.",
        "status": "done",
        "epic": "engine", "priority": "high",
    },
    # ── Epic: api ─────────────────────────────────────────────────────────────
    {
        "id": "zig-api-01",
        "title": "ZIG API: GET /security/api/zig/pillars — all pillars with scores",
        "description": "Returns 7 pillars with current maturity scores and aggregate. COMPLETED.",
        "status": "done",
        "epic": "api", "priority": "critical",
    },
    {
        "id": "zig-api-02",
        "title": "ZIG API: GET /security/api/zig/pillars/<slug> — pillar detail",
        "description": "Returns pillar metadata + capabilities + maturity score. COMPLETED.",
        "status": "done",
        "epic": "api", "priority": "critical",
    },
    {
        "id": "zig-api-03",
        "title": "ZIG API: GET /security/api/zig/capabilities — filterable list",
        "description": "Filter by pillar, phase, status. COMPLETED.",
        "status": "done",
        "epic": "api", "priority": "critical",
    },
    {
        "id": "zig-api-04",
        "title": "ZIG API: PATCH /security/api/zig/capabilities/<id> — update status",
        "description": "Update implementation_status + evidence_note for a capability. COMPLETED.",
        "status": "done",
        "epic": "api", "priority": "critical",
    },
    {
        "id": "zig-api-05",
        "title": "ZIG API: GET /security/api/zig/activities — filterable activity list",
        "description": "Filter by capability, phase. Includes completion status join. COMPLETED.",
        "status": "done",
        "epic": "api", "priority": "critical",
    },
    {
        "id": "zig-api-06",
        "title": "ZIG API: PATCH /security/api/zig/activities/<id>/complete — toggle completion",
        "description": "Cycle not_started → in_progress → complete. Writes zig_activity_completions. COMPLETED.",
        "status": "done",
        "epic": "api", "priority": "critical",
    },
    {
        "id": "zig-api-07",
        "title": "ZIG API: GET /security/api/zig/maturity — aggregate scores + FY2027",
        "description": "Aggregate pillar scores, overall score, FY2027 readiness %. COMPLETED.",
        "status": "done",
        "epic": "api", "priority": "critical",
    },
    {
        "id": "zig-api-08",
        "title": "ZIG API: POST /security/api/zig/assess — run full ZIG assessment",
        "description": "Triggers run_zig_assessment(), stores results. COMPLETED.",
        "status": "done",
        "epic": "api", "priority": "critical",
    },
    {
        "id": "zig-api-09",
        "title": "ZIG API: GET /security/api/zig/phases — phase completion metrics",
        "description": "Discovery/Ph1/Ph2 activity counts done/in-progress/not-started. COMPLETED.",
        "status": "done",
        "epic": "api", "priority": "high",
    },
    {
        "id": "zig-api-10",
        "title": "ZIG API: GET /security/api/zig/roadmap — FY2027/FY2032 timeline JSON",
        "description": "Milestone JSON for Chart.js timeline rendering. COMPLETED.",
        "status": "done",
        "epic": "api", "priority": "high",
    },
    # ── Epic: ui ──────────────────────────────────────────────────────────────
    {
        "id": "zig-ui-01",
        "title": "ZIG UI: Create /security/zig/ index template — 7-pillar radar + phase progress",
        "description": "Chart.js radar chart, phase completion bars, FY2027 countdown. COMPLETED.",
        "status": "done",
        "epic": "ui", "priority": "critical",
    },
    {
        "id": "zig-ui-02",
        "title": "ZIG UI: Create /security/zig/pillar/<slug> template — capability checklist",
        "description": "Pillar score badge, capabilities table with status toggles, activity checklist. COMPLETED.",
        "status": "done",
        "epic": "ui", "priority": "critical",
    },
    {
        "id": "zig-ui-03",
        "title": "ZIG UI: Create /security/zig/phase template — phase activity grid",
        "description": "3-phase grid with activity completion checkboxes per pillar group. COMPLETED.",
        "status": "done",
        "epic": "ui", "priority": "critical",
    },
    {
        "id": "zig-ui-04",
        "title": "ZIG UI: Create /security/zig/assessment template — run assessment + results",
        "description": "Run button, pillar score grid, last assessment results. COMPLETED.",
        "status": "done",
        "epic": "ui", "priority": "critical",
    },
    {
        "id": "zig-ui-05",
        "title": "ZIG UI: Create /security/zig/roadmap template — FY2027/FY2032 timeline",
        "description": "Milestone timeline, pillar progress bars, NSA reference links. COMPLETED.",
        "status": "done",
        "epic": "ui", "priority": "high",
    },
    {
        "id": "zig-ui-06",
        "title": "ZIG UI: Add ZIG nav dropdown to security_canvas/base.html",
        "description": "Insert ZIG mega-menu with links to all 5 ZIG pages + 7 pillar sub-links. COMPLETED.",
        "status": "done",
        "epic": "ui", "priority": "high",
    },
    {
        "id": "zig-ui-07",
        "title": "ZIG UI: Add ZIG summary card to security canvas index.html",
        "description": "Async-loaded card showing overall score, maturity level, FY2027 %, 7-pillar mini-bars. COMPLETED.",
        "status": "done",
        "epic": "ui", "priority": "high",
    },
    # ── Epic: iqe ─────────────────────────────────────────────────────────────
    {
        "id": "zig-iqe-01",
        "title": "ZIG IQE: Add ZIG collections to tools/iqe/adapters/security.py",
        "description": "Register zig.pillars, zig.capabilities, zig.activities, zig.maturity, zig.gaps. COMPLETED.",
        "status": "done",
        "epic": "iqe", "priority": "high",
    },
    {
        "id": "zig-iqe-02",
        "title": "ZIG IQE: Add 7 ZIG seed queries to context/iqe/queries/security/",
        "description": "Seed queries: maturity summary, pillar scores, incomplete Phase1, gaps, discovery status. COMPLETED.",
        "status": "done",
        "epic": "iqe", "priority": "high",
    },
    {
        "id": "zig-iqe-03",
        "title": "ZIG IQE: Add IQE widget to ZIG index template",
        "description": "Include iqe_query_widget.html with canvas=security_zig. COMPLETED.",
        "status": "done",
        "epic": "iqe", "priority": "medium",
    },
    # ── Epic: sc ──────────────────────────────────────────────────────────────
    {
        "id": "zig-sc-01",
        "title": "ZIG SC: Add ZIG maturity panel to security posture.html",
        "description": "Insert ZIG pillar mini-scores panel alongside NIST coverage.",
        "status": "open",
        "epic": "sc", "priority": "high",
    },
    {
        "id": "zig-sc-02",
        "title": "ZIG SC: Add ZIG gap badges to assessment.html sidebar",
        "description": "Show capability gaps in assessment sidebar if design has ZIG assessment.",
        "status": "open",
        "epic": "sc", "priority": "medium",
    },
    {
        "id": "zig-sc-03",
        "title": "ZIG SC: Create zig_artifact_generator.py — ATO package report",
        "description": "Generate Markdown + JSON ZIG gap assessment report for ATO packages. COMPLETED.",
        "status": "done",
        "epic": "sc", "priority": "high",
    },
    {
        "id": "zig-sc-04",
        "title": "ZIG SC: Add ZIG report to artifacts.html",
        "description": 'Add "ZIG Assessment Report" download alongside SSP/SAR/POAM.',
        "status": "open",
        "epic": "sc", "priority": "medium",
    },
    # ── Epic: infra ───────────────────────────────────────────────────────────
    {
        "id": "zig-infra-01",
        "title": "ZIG Infra: Mirror all new ZIG files to icdev/ package",
        "description": "Copy 6 Python modules + 5 templates + updated files to icdev/tools/security_canvas/. COMPLETED.",
        "status": "done",
        "epic": "infra", "priority": "high",
    },
    {
        "id": "zig-infra-02",
        "title": "ZIG Infra: Add ZIG routes to start.md Pages line",
        "description": "Add /security/zig/ and all sub-pages to .claude/commands/start.md. COMPLETED.",
        "status": "done",
        "epic": "infra", "priority": "medium",
    },
    {
        "id": "zig-infra-03",
        "title": "ZIG Infra: Add ZIG to tools/manifest/security.md",
        "description": "Document all new ZIG tools in the security manifest shard.",
        "status": "open",
        "epic": "infra", "priority": "medium",
    },
    {
        "id": "zig-infra-04",
        "title": "ZIG Infra: Run companion sync",
        "description": "python tools/dx/companion.py --sync --write --json",
        "status": "open",
        "epic": "infra", "priority": "medium",
    },
    {
        "id": "zig-infra-05",
        "title": "ZIG Infra: Run coherence checker gate",
        "description": "python tools/workflow/coherence_checker.py --all --fix --gate",
        "status": "open",
        "epic": "infra", "priority": "high",
    },
    {
        "id": "zig-infra-06",
        "title": "ZIG Infra: Add zig-db-06 conftest.py MINIMAL_ICDEV_SCHEMA update",
        "description": "Add zig_pillars, zig_capabilities, zig_activities, zig_activity_completions, zig_maturity_scores schemas to tests/conftest.py.",
        "status": "open",
        "epic": "infra", "priority": "high",
    },
]


def _upsert_task(conn, task: dict, dry_run: bool) -> str:
    existing = conn.execute(
        "SELECT id, status FROM kanban_tasks WHERE id=?", (task["id"],)
    ).fetchone()
    if existing:
        return "exists"

    now = _now()
    status_map = {"done": "done", "open": "backlog", "in_progress": "in_progress"}
    db_status = status_map.get(task.get("status", "open"), "backlog")

    if dry_run:
        print(f"  [DRY-RUN] Would insert: {task['id']} — {task['title'][:60]}")
        return "dry_run"

    tags = task.get("epic", "")
    conn.execute(
        """INSERT INTO kanban_tasks
           (id, title, description, status, project_id, priority, tags, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            task["id"], task["title"], task.get("description", ""),
            db_status, PROJECT_ID, task.get("priority", "medium"),
            tags, now, now,
        ),
    )
    return "inserted"


def main():
    parser = argparse.ArgumentParser(description="Seed ZIG kanban tasks")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    conn = get_connection()
    try:
        inserted = exists = dry = 0
        for task in TASKS:
            result = _upsert_task(conn, task, args.dry_run)
            if result == "inserted":
                inserted += 1
            elif result == "exists":
                exists += 1
            else:
                dry += 1

        if not args.dry_run:
            conn.commit()
            print(f"ZIG Kanban seeded: {inserted} inserted, {exists} already existed.")
        else:
            print(f"ZIG Kanban dry-run: {dry} tasks would be inserted.")
    finally:
        conn.close()


if __name__ == "__main__":
    main()

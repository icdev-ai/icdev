# CUI // SP-CTI
"""Seed Kanban tasks for the AISG finish-and-ship effort.

Project: aisg  (task_prefix 'aisg-')  — adds new epics ship/iqe/test/doc/vv on top
of the existing AISG build project so the autonomous scheduler completes the
"wire it up + verify" work for the AI Skills Gap (Zero-to-AI Enablement) canvas.

Plan: C:/Users/schuo/.claude/plans/besides-sipa-that-we-re-tingly-hammock.md

CONTEXT — already done INLINE (in the working tree, do NOT redo):
  * tools/dashboard/app.py: AISG blueprint registered in _CANVAS_DEFS
    -> ("aisg", "ICDEV_AISG_ENABLED", "tools.aisg.blueprint", "bp")
  * .env: ICDEV_AISG_ENABLED=true
  * tools/dashboard/templates/base.html: Build ▾ dropdown now links all 10 AISG
    pages (AI Enablement + Role Views sections) and the active-path check matches.
  * Verified: all 10 routes return HTTP 200; PG has all aisg_* tables;
    tests/test_aisg_wizard.py = 9 passed.
  * NOTE: AISG backend modules (16) + templates (11) already exist + committed.
    This is WIRING + IQE + TEST + DOC + V&V only — NOT a rebuild. Do not run
    tools/kanban/seed_aisg.py (that seeds the already-built A-D module work).

Task IDs follow <prefix><epic>-<N> (aisg-<epic>-NN) so
tools/project/kanban_project_sync.py folds the new epics into args/projects.yaml.

depends_on_task_id is single-parent (engine limitation honored by the dispatcher:
a task dispatches only when its parent is 'done'/'decomposed'). Secondary
cross-epic prerequisites are named in the description text.

Run:
    python tools/kanban/seed_aisg_ship.py --dry-run
    python tools/kanban/seed_aisg_ship.py
    python tools/project/kanban_project_sync.py     # refresh projects.yaml card
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

PROJECT_ID = "aisg"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


TASKS = [
    # =========================================================================
    # EPIC ship — guardrail registration for the wiring already in the tree
    # =========================================================================
    {
        "id": "aisg-ship-01",
        "title": "start.md Pages line + manifest + commands.md for the 10 AISG routes",
        "description": (
            "Register the now-live AISG pages so the guardrails pass. (1) Add to the `Pages:` line in "
            ".claude/commands/start.md: /ai-wizard, /ai-patterns, /ai-builder, /ai-learning, /ai-roi, "
            "/ai-skills, /ai-handoff, /dashboard/compliance-view, /dashboard/executive-view, "
            "/dashboard/pm-view. (2) Confirm tools/manifest/aisg.md lists the wizard/pattern/learning/"
            "builder/roi/skills/handoff tools + the blueprint routes; update if stale. (3) Add the AISG "
            "CLI entry points to docs/reference/commands.md. The blueprint is ALREADY registered in "
            "tools/dashboard/app.py and ICDEV_AISG_ENABLED=true is in .env — do not duplicate that."
        ),
        "task_type": "chore",
        "priority": "high",
        "depends_on_task_id": None,
    },
    {
        "id": "aisg-ship-02",
        "title": "Commit AISG wiring (app.py blueprint reg, .env toggle, base.html nav)",
        "description": (
            "Commit the AISG wiring that currently sits uncommitted in the working tree so it is not lost: "
            "the _CANVAS_DEFS line in tools/dashboard/app.py, the base.html Build-dropdown nav additions, "
            "and (if tracked) the .env toggle. Use an explicit pathspec commit so unrelated pre-staged "
            "govcon/cpmp changes in app.py are NOT bundled if they are still separate — verify the diff "
            "before committing. Message: 'feat(aisg): wire Zero-to-AI Enablement canvas into dashboard "
            "(blueprint + nav)'. Requires aisg-ship-01."
        ),
        "task_type": "chore",
        "priority": "high",
        "depends_on_task_id": "aisg-ship-01",
    },
    # =========================================================================
    # EPIC iqe — IQE integration (part of the 8-component new-page gate)
    # =========================================================================
    {
        "id": "aisg-iqe-01",
        "title": "tools/iqe/adapters/aisg.py — register AISG collections",
        "description": (
            "Create tools/iqe/adapters/aisg.py following an existing adapter (e.g. tools/iqe/adapters/"
            "aiify.py or cam.py). Register at least 3 collections over the live PG tables: "
            "aisg.roadmaps (aisg_roadmaps), aisg.skills (aisg_skill_usage / aisg_skills), "
            "aisg.roi (aisg_roi_events / aisg_roi_tracking), and optionally aisg.patterns (aisg_patterns). "
            "Use get_connection() (RLS-aware) and the same collection/field-spec shape as the reference "
            "adapter. No new tables — all aisg_* tables already exist in PG."
        ),
        "task_type": "build",
        "priority": "medium",
        "depends_on_task_id": None,
    },
    {
        "id": "aisg-iqe-02",
        "title": "Wire IQE dispatch — _CANVAS_MAP entry + PATH_CANVAS regex",
        "description": (
            "Make the AISG canvas reachable by natural-language IQE. (1) Add an 'aisg' entry to the "
            "_CANVAS_MAP in iqe_dispatch() in tools/dashboard/app.py pointing at the new adapter. "
            "(2) Add a PATH_CANVAS regex entry in tools/dashboard/templates/base.html (~the PATH_CANVAS "
            "array near line 886) mapping /ai-* (and /dashboard/*-view) paths to the 'aisg' canvas so the "
            "mini query-bar targets the right collections. Confirm POST /api/iqe-query with a sample "
            "AISG query returns rows. Requires aisg-iqe-01."
        ),
        "task_type": "build",
        "priority": "medium",
        "depends_on_task_id": "aisg-iqe-01",
    },
    {
        "id": "aisg-iqe-03",
        "title": "Embed IQE widget in AISG templates + >=3 seed queries",
        "description": (
            "Add {% include \"includes/iqe_query_widget.html\" %} to the main AISG page template(s) "
            "(tools/dashboard/templates/aisg/page.html and/or wizard.html). Author at least 3 seed query "
            "files under context/iqe/queries/aisg/ (markdown, same shape as context/iqe/queries/cpmp/*), "
            "e.g. 'show ROI hours saved this month', 'list skill usage by maturity band', 'which AI "
            "patterns are deployed'. Mirror the page template to icdev/ if companion sync does not. "
            "Requires aisg-iqe-02."
        ),
        "task_type": "build",
        "priority": "medium",
        "depends_on_task_id": "aisg-iqe-02",
    },
    # =========================================================================
    # EPIC test — pytest expansion (self-provisioning SQLite pattern)
    # =========================================================================
    {
        "id": "aisg-test-01",
        "title": "pytest — pattern_registry, learning_paths, explain_translator, agent builder, role views",
        "description": (
            "Expand pytest coverage beyond tests/test_aisg_wizard.py, following its SELF-PROVISIONING "
            "pattern (per-test SQLite via patched get_connection + sql_placeholder; do NOT add tables to "
            "conftest MINIMAL_ICDEV_SCHEMA). Before writing, run `python tools/testing/api_surface_"
            "extractor.py --file tools/aisg/<module>.py --json` per module. Cover: pattern_registry "
            "(CRUD + deploy), learning_paths (3-track progress), explain_translator (audit event -> "
            "plain English), visual_agent_builder (canvas JSON -> FORGE goal markdown, topological sort), "
            "and the compliance/executive/pm view aggregators (return shape, empty-DB safety). Target each "
            "module's public surface. All green with C:\\Python314\\python -m pytest, PYTHONPATH=C:\\AI\\ICDev."
        ),
        "task_type": "test",
        "priority": "high",
        "depends_on_task_id": None,
    },
    {
        "id": "aisg-test-02",
        "title": "Playwright E2E — wizard flow + role views + explain panel + IQE widget",
        "description": (
            "End-to-end via Playwright MCP against the running dashboard (port 5050): (1) /ai-wizard — "
            "complete the 5-question flow, assert recommended goals render and a 30-day sprint is offered; "
            "(2) /dashboard/executive-view, /dashboard/compliance-view, /dashboard/pm-view each render "
            "real aggregated data; (3) open the ExplainPanel and confirm a plain-English decision; "
            "(4) exercise the IQE widget on an AISG page with a seed query and confirm real rows; "
            "(5) /ai-patterns loads. Screenshots to playwright/screenshots/aisg-*.png. Requires "
            "aisg-iqe-03 AND aisg-test-01 (cross-prereqs; single parent set to aisg-test-01)."
        ),
        "task_type": "test",
        "priority": "high",
        "depends_on_task_id": "aisg-test-01",
    },
    # =========================================================================
    # EPIC doc — feature documentation
    # =========================================================================
    {
        "id": "aisg-doc-01",
        "title": "docs/features/phase-aisg-zero-to-ai.md — shipped Phase A + B-D backlog",
        "description": (
            "Write docs/features/phase-aisg-zero-to-ai.md. Document the shipped Phase A scope (Launchpad "
            "wizard, 4 role-based dashboard views, Explain-this panel) with the live routes and the 16 "
            "backing modules / 11 templates. Capture the remaining backlog: Phase B (pattern library, "
            "learning paths, knowledge handoff), Phase C (visual agent builder, autotune, Genesis "
            "graduated trust — needs a Genesis trust_mode extension), Phase D (ROI dashboard, skills-gap "
            "tracker, talent retention — needs ROI-event hooks into existing reflexes). Note the two real "
            "net-new dependencies (Genesis trust_mode; ROI event hooks). CUI marking at top."
        ),
        "task_type": "chore",
        "priority": "medium",
        "depends_on_task_id": None,
    },
    # =========================================================================
    # EPIC ship (final) — verification gate (NOT 'vv': aisg-vv-* already exist)
    # =========================================================================
    {
        "id": "aisg-ship-03",
        "title": "Final gate — companion sync (fg) + coherence + health + full pytest + commit",
        "description": (
            "Final V&V for the AISG ship. (1) `python tools/dx/companion.py --sync --write --json` IN THE "
            "FOREGROUND (never background — background sync has deleted uncommitted work) to propagate "
            "AISG templates/skills. (2) `python tools/workflow/coherence_checker.py --all --fix --gate` "
            "passes (8-component new-page gate satisfied for AISG). (3) `python tools/testing/health_"
            "check.py --json` green. (4) Full `pytest tests/ -q` green (no regressions). (5) Commit any "
            "remaining AISG files (adapter, seed queries, tests, feature doc, mirrored templates). On "
            "Windows set $env:PYTHONPATH='C:\\AI\\ICDev'. Requires aisg-test-02 AND aisg-iqe-03 AND "
            "aisg-doc-01 AND aisg-ship-02 (single parent set to aisg-test-02; others named here)."
        ),
        "task_type": "test",
        "priority": "high",
        "depends_on_task_id": "aisg-test-02",
    },
]


def seed(dry_run: bool = False) -> int:
    conn = get_connection()
    now = _now()
    inserted = 0
    skipped = 0
    try:
        for t in TASKS:
            existing = conn.execute(
                "SELECT id FROM kanban_tasks WHERE id=?", (t["id"],)
            ).fetchone()
            if existing:
                skipped += 1
                continue
            if dry_run:
                inserted += 1
                continue
            conn.execute(
                """INSERT INTO kanban_tasks
                   (id, title, description, task_type, priority, status,
                    scheduled_at, created_at, updated_at, depends_on_task_id,
                    project_id, classification)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    t["id"],
                    t["title"],
                    t["description"],
                    t.get("task_type", "build"),
                    t.get("priority", "medium"),
                    "scheduled",
                    now,
                    now,
                    now,
                    t.get("depends_on_task_id"),
                    PROJECT_ID,
                    "CUI",
                ),
            )
            inserted += 1
        if not dry_run:
            conn.commit()
    finally:
        conn.close()
    verb = "Would seed" if dry_run else "Seeded"
    print(f"{verb} {inserted} tasks, skipped {skipped} existing (total defined: {len(TASKS)}).")
    return inserted


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Seed AISG finish-and-ship kanban tasks")
    ap.add_argument("--dry-run", action="store_true", help="Print only, no DB write")
    args = ap.parse_args()
    seed(dry_run=args.dry_run)

# CUI // SP-CTI
"""Seed Kanban tasks for the 'what next' Appendix initiatives.

Plan: C:/Users/schuo/.claude/plans/besides-sipa-that-we-re-tingly-hammock.md (Appendix)

Three NEW projects (auto-registered by tools/project/kanban_project_sync.py from the
task-ID prefix <prefix>-<epic>-<N>):

  loe  (Loose Ends)            — close out done-but-unmerged/uncommitted work
  plh  (Platform Health)       — pay down system-flagged risk debt
  gco  (GovCon Orchestration)  — chain the standalone capture tools into one flow

EXCLUDED (already handled, do NOT re-seed):
  * ACE commit            -> done this session (commit 6e716a8ce)
  * DIC stale memory      -> corrected this session
  * CHYG connection sweep -> already on the board (chyg-* project, 5 tasks)

depends_on_task_id is single-parent (engine limitation honored by the dispatcher:
a task dispatches only when its parent is 'done'/'decomposed'). Secondary cross-epic
prerequisites are named in the description text.

Run:
    python tools/kanban/seed_appendix_initiatives.py --dry-run
    python tools/kanban/seed_appendix_initiatives.py
    python tools/project/kanban_project_sync.py     # auto-create the 3 project cards
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


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


TASKS = [
    # #########################################################################
    # PROJECT loe — Loose Ends (banked value: commit/merge finished work)
    # #########################################################################
    {
        "id": "loe-aiify-01",
        "project_id": "loe",
        "title": "Merge aiify-feature — untrack .logs, then merge to main",
        "description": (
            "The AI-ify canvas + posture engines are COMPLETE on branch aiify-feature (27158324e) but the "
            "merge is blocked only by .logs being untracked on main. Fix: on the feature branch, "
            "`git rm -r --cached .logs` and add `.logs/` to .gitignore (mirror what was done on main "
            "3ca64cffa), commit, then merge aiify-feature into main. After merge, start the dashboard and "
            "verify /ai-ify/posture and the Agentic AI posture both render and are wired into the "
            "/compliance hub. Do NOT run companion sync in the background. Ref memory "
            "feedback_aiify_branch_merge_blocked + project_aiify_agentic_compliance_posture."
        ),
        "task_type": "chore",
        "priority": "high",
        "depends_on_task_id": None,
    },
    {
        "id": "loe-slides-01",
        "project_id": "loe",
        "title": "Commit the Slides Generator canvas (19 tests pass, uncommitted)",
        "description": (
            "The Slide Deck Generator canvas (tools/slides/ at /slides) is complete with 19 passing tests "
            "but was never committed. Run the suite first (C:\\Python314\\python -m pytest tests/ -k slides, "
            "PYTHONPATH=C:\\AI\\ICDev) to confirm 19 green, then commit tools/slides/** + the slides "
            "templates + any slides DB migration with an explicit pathspec commit (avoid bundling "
            "unrelated staged work). Confirm /slides renders post-commit. Ref memory project_slides_canvas "
            "+ slides-curated-deck-workflow."
        ),
        "task_type": "chore",
        "priority": "high",
        "depends_on_task_id": None,
    },
    {
        "id": "loe-dic-01",
        "project_id": "loe",
        "title": "DIC verification pass (V&V only — already built + committed)",
        "description": (
            "The Document Intelligence Canvas is fully built + committed (14 modules, 14 templates, wired "
            "in app.py). It only needs verification, NOT a rebuild and NOT re-seeding the dic-* build "
            "tasks. Run: DIC pytest (C:\\Python314\\python -m pytest tests/ -k 'dic or document_intel'), "
            "health_check --json, and Playwright E2E across the 13 DIC pages — confirm grounded NO-LLM "
            "search returns mandatory citations, the verifier abstains/strips on low support, and the IQE "
            "widget answers a seed query. Screenshots to playwright/screenshots/dic-*.png. Note any real "
            "failures as new bug cards. Ref memory dic-build-state-vs-kanban (RESOLVED)."
        ),
        "task_type": "test",
        "priority": "medium",
        "depends_on_task_id": None,
    },
    # #########################################################################
    # PROJECT plh — Platform Health (risk debt the platform flags about itself)
    # #########################################################################
    # --- epic scout: repair the broken external-intel scanners ---
    {
        "id": "plh-scout-01",
        "project_id": "plh",
        "title": "Fix scan_github() + scan_hackernews() — missing 'config' argument",
        "description": (
            "The Scout engine's GitHub-trending and HackerNews scanners fail with a missing `config` "
            "argument (per data/scout/2026-06-03.md). Locate the scanner module(s) under tools/ (grep for "
            "def scan_github / def scan_hackernews), fix the call sites / signatures so config is passed, "
            "and add a regression test that invokes each scanner with a stub config and asserts it returns "
            "a list without raising. Deterministic; no live network needed for the test (mock the HTTP "
            "layer)."
        ),
        "task_type": "fix",
        "priority": "high",
        "depends_on_task_id": None,
    },
    {
        "id": "plh-scout-02",
        "project_id": "plh",
        "title": "Fix arXiv scanner — URL control-character / max-length error",
        "description": (
            "The Scout arXiv scanner fails with a URL control-character / max-URL-length error (per "
            "data/scout/2026-06-03.md). Sanitize/url-encode the query and cap the constructed URL length; "
            "add a regression test feeding a long/special-char query and asserting a well-formed request "
            "URL. Independent of plh-scout-01."
        ),
        "task_type": "fix",
        "priority": "high",
        "depends_on_task_id": None,
    },
    {
        "id": "plh-scout-03",
        "project_id": "plh",
        "title": "V&V — run full Scout cycle, all 3 sources return findings",
        "description": (
            "Run a full Scout cycle and confirm GitHub-trending, HackerNews, and arXiv each return "
            "findings (no scanner failures) and a fresh data/scout/<date>.md is written. Requires "
            "plh-scout-01 AND plh-scout-02 (single parent set to plh-scout-01; arXiv prereq named here)."
        ),
        "task_type": "test",
        "priority": "medium",
        "depends_on_task_id": "plh-scout-01",
    },
    # --- epic testcov: close the test-coverage gap on core tools + A2A ---
    {
        "id": "plh-testcov-01",
        "project_id": "plh",
        "title": "Audit — enumerate tools/ + A2A modules with no test file (prioritized)",
        "description": (
            "Produce a prioritized list of tools/ modules and tools/a2a/* agents that have NO matching "
            "tests/test_*.py (Scout 2026-06-03 flagged ~20, e.g. check_manifest_coverage, cli_formatter, "
            "data_validator, generate_compliance_report, kanban_verify, verify_manifest, twin_chat, "
            "pytest_plugins, and a2a/{agent_client,agent_entrypoint,agent_registry,agent_server,task,"
            "task_lease}). Rank by import-fan-in / criticality. Write the list to docs/features/"
            "phase-testcov-audit.md. This sizes the batches below."
        ),
        "task_type": "chore",
        "priority": "high",
        "depends_on_task_id": None,
    },
    {
        "id": "plh-testcov-02",
        "project_id": "plh",
        "title": "Tests batch 1 — core utility tools",
        "description": (
            "Add tests for the highest-priority core utility tools from plh-testcov-01 (e.g. "
            "check_manifest_coverage, cli_formatter, data_validator, generate_compliance_report, "
            "kanban_verify, verify_manifest, twin_chat). Run `python tools/testing/api_surface_extractor.py "
            "--file <module> --json` first per module; target the public surface; self-provision any DB "
            "schema per-test (don't bloat conftest). All green. Requires plh-testcov-01."
        ),
        "task_type": "test",
        "priority": "high",
        "depends_on_task_id": "plh-testcov-01",
    },
    {
        "id": "plh-testcov-03",
        "project_id": "plh",
        "title": "Tests batch 2 — A2A agents",
        "description": (
            "Add tests for the A2A agent modules from plh-testcov-01 (agent_client, agent_entrypoint, "
            "agent_registry, agent_server, task, task_lease). Mock TLS/transport; assert registration, "
            "task lifecycle, and lease acquire/release behave. All green. Requires plh-testcov-02."
        ),
        "task_type": "test",
        "priority": "high",
        "depends_on_task_id": "plh-testcov-02",
    },
    {
        "id": "plh-testcov-04",
        "project_id": "plh",
        "title": "V&V — full pytest green + coverage report",
        "description": (
            "Run the full suite (C:\\Python314\\python -m pytest tests/ -q, PYTHONPATH=C:\\AI\\ICDev) and "
            "a coverage report; confirm no regressions and that the newly-covered modules report non-zero "
            "coverage. Summarize the delta in docs/features/phase-testcov-audit.md. Requires plh-testcov-03."
        ),
        "task_type": "test",
        "priority": "medium",
        "depends_on_task_id": "plh-testcov-03",
    },
    # #########################################################################
    # PROJECT gco — GovCon Orchestration (intake -> award, one demo story)
    # #########################################################################
    {
        "id": "gco-db-01",
        "project_id": "gco",
        "title": "DB — opportunity_pipeline_runs (append-only, RLS) + register append-only",
        "description": (
            "Create a PG-first dual-schema table opportunity_pipeline_runs (append-only/immutable) via a "
            "new tools/db/migrations/<N>_*.py, using the RLS-aware get_connection() with tenant_id + "
            "classification columns: id, opportunity_id, intake_json, vehicle_match_json, "
            "far_dfars_json, igce_json, bom_json, ndaa889_json, recommendation, risk_score, created_by, "
            "tenant_id, classification, created_at. GUARDRAIL: add 'opportunity_pipeline_runs' to "
            "APPEND_ONLY_TABLES in .claude/hooks/pre_tool_use.py AND to tests/conftest.py "
            "MINIMAL_ICDEV_SCHEMA. Independent of the engine task."
        ),
        "task_type": "build",
        "priority": "high",
        "depends_on_task_id": None,
    },
    {
        "id": "gco-engine-01",
        "project_id": "gco",
        "title": "Orchestrator — opportunity_pipeline.py chaining the existing capture tools",
        "description": (
            "Create tools/govcon/opportunity_pipeline.py that runs ONE opportunity through the existing "
            "standalone tools in sequence and returns a unified assessment: intake -> "
            "vehicle_identifier.match_initiative -> far_dfars_verifier (compliance gate) -> "
            "igce_estimator (cost) -> bom_generator + supply_chain.ndaa_889_screener (screen BOM). Reuse "
            "each tool's public API; do NOT reimplement their logic. Deterministic core; surface each "
            "stage's status + a combined gate. CLI __main__ with --opportunity-id / --json. Add to "
            "tools/manifest/ shard."
        ),
        "task_type": "build",
        "priority": "critical",
        "depends_on_task_id": None,
    },
    {
        "id": "gco-engine-02",
        "project_id": "gco",
        "title": "Persist runs + bid/no-bid recommendation rollup",
        "description": (
            "Extend opportunity_pipeline.py to persist each run to opportunity_pipeline_runs and compute a "
            "single bid / no-bid / maybe recommendation rollup from the stage results (compliance fail -> "
            "no-bid; IGCE vs ceiling fit; 889 prohibited -> block; vehicle viability). Reuse the existing "
            "bid/no-bid logic from the PMO demo (pmodemo) where possible. Requires gco-engine-01 AND "
            "gco-db-01 (single parent set to gco-engine-01; DB prereq named here)."
        ),
        "task_type": "build",
        "priority": "high",
        "depends_on_task_id": "gco-engine-01",
    },
    {
        "id": "gco-ui-01",
        "project_id": "gco",
        "title": "UI — /govcon/opportunity-pipeline staged flow page (+ mirror to icdev/)",
        "description": (
            "Add a route + template tools/dashboard/templates/govcon/opportunity_pipeline.html showing the "
            "staged flow for one opportunity: intake form -> vehicle match -> FAR/DFARS gate -> IGCE -> "
            "BOM + 889 screen -> bid/no-bid recommendation. Wire the @bp.route in the govcon blueprint to "
            "the engine. Add a nav/parent link from /govcon. Mirror the template to "
            "icdev/tools/dashboard/templates/govcon/ (or via companion sync). Requires gco-engine-02."
        ),
        "task_type": "build",
        "priority": "high",
        "depends_on_task_id": "gco-engine-02",
    },
    {
        "id": "gco-iqe-01",
        "project_id": "gco",
        "title": "IQE integration for the opportunity pipeline (8-component gate)",
        "description": (
            "Create tools/iqe/adapters/govcon_pipeline.py (collections over opportunity_pipeline_runs), "
            "add the canvas to _CANVAS_MAP in iqe_dispatch() + a PATH_CANVAS regex in base.html, include "
            "the iqe_query_widget in the new template, and author >=3 seed queries under "
            "context/iqe/queries/govcon_pipeline/ (e.g. 'show no-bid opportunities', 'runs failing "
            "FAR/DFARS', 'opportunities with 889-prohibited items'). Requires gco-ui-01."
        ),
        "task_type": "build",
        "priority": "medium",
        "depends_on_task_id": "gco-ui-01",
    },
    {
        "id": "gco-vv-01",
        "project_id": "gco",
        "title": "V&V — pytest + Playwright E2E + companion + coherence + feature doc",
        "description": (
            "End-to-end gate: pytest for opportunity_pipeline (a sample opportunity flows through all "
            "stages, recommendation persisted). Playwright E2E against /govcon/opportunity-pipeline "
            "(submit a sample, see staged results + recommendation + IQE widget); screenshots to "
            "playwright/screenshots/gco-*.png. Run companion sync (FOREGROUND) + coherence_checker --all "
            "--fix --gate (8-component gate). Write docs/features/phase-govcon-opportunity-pipeline.md. "
            "Requires gco-iqe-01."
        ),
        "task_type": "test",
        "priority": "high",
        "depends_on_task_id": "gco-iqe-01",
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
                    t["project_id"],
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
    ap = argparse.ArgumentParser(description="Seed Appendix initiatives (loe/plh/gco) kanban tasks")
    ap.add_argument("--dry-run", action="store_true", help="Print only, no DB write")
    args = ap.parse_args()
    seed(dry_run=args.dry_run)

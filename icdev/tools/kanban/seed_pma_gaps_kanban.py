# CUI // SP-CTI
"""Seed Kanban tasks for Proposals/CPMP Management Approach gaps.

Project: pma  (task_prefix 'pma-')
Purpose: Close the three capability gaps identified against Section 5.1.2.6
         of the ISC2 FMA/E proposal — areas where /proposals and /cpmp lack
         AI-assisted management approach coverage:

  Epic coord  — Government/IC partner meeting & tasker coordination
  Epic cont   — Personnel continuity (clearance/cert tracking, key-person matrix)
  Epic igap   — Information gap persistence (INT coverage mapping, collection req generation)

Run:
    python tools/kanban/seed_pma_gaps_kanban.py --dry-run
    python tools/kanban/seed_pma_gaps_kanban.py
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

PROJECT_ID = "pma"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


TASKS = [

    # ── EPIC coord ──────────────────────────────────────────────────────────
    {
        "id": "pma-coord-01",
        "title": "DB schema — meeting_logs and action_items tables for CPMP coordination",
        "description": (
            "Add two new tables to the CPMP database: `pma_meeting_logs` (id, contract_id, "
            "meeting_date, attendees JSON, summary TEXT, classification, created_at) and "
            "`pma_action_items` (id, meeting_id, contract_id, owner TEXT, description TEXT, "
            "due_date TEXT, status CHECK('open','in_progress','closed'), closed_at, created_at). "
            "Implement `tools/cpmp/meeting_coordinator.py` with `create_meeting()`, "
            "`list_meetings()`, `create_action_item()`, `update_action_item()`, and "
            "`get_overdue_items()`. Use `get_connection()` with RLS. Add append-only guard "
            "for `pma_meeting_logs` in `.claude/hooks/pre_tool_use.py` APPEND_ONLY_TABLES. "
            "Acceptance: `python -m pytest tests/test_pma_coord.py -v` passes; no raw SQLite3 calls."
        ),
        "acceptance_criteria": (
            "GIVEN a contract exists "
            "WHEN `create_meeting()` is called with attendees and summary "
            "THEN a row is inserted in pma_meeting_logs and returned with id and created_at. "
            "GIVEN a meeting exists "
            "WHEN `create_action_item()` is called with owner and due_date "
            "THEN the item is retrievable via `get_overdue_items()` when past due."
        ),
        "test_path": "tests/test_pma_coord.py",
        "status": "backlog",
        "priority": "high",
        "depends_on_task_id": None,
    },
    {
        "id": "pma-coord-02",
        "title": "API routes — POST/GET meeting logs and action items in CPMP blueprint",
        "description": (
            "Add 6 REST endpoints to `tools/dashboard/api/cpmp.py`: "
            "`POST /api/cpmp/contracts/<id>/meetings` (create meeting log), "
            "`GET  /api/cpmp/contracts/<id>/meetings` (list with pagination), "
            "`POST /api/cpmp/contracts/<id>/meetings/<mid>/actions` (create action item), "
            "`GET  /api/cpmp/contracts/<id>/actions` (list all actions, filterable by status/owner), "
            "`PUT  /api/cpmp/actions/<aid>` (update status/owner/due_date), "
            "`GET  /api/cpmp/contracts/<id>/actions/overdue` (items past due_date with status != closed). "
            "All routes require `@require_role('pm', 'cor', 'admin')`. "
            "Acceptance: integration test hits each route via Flask test client and asserts status codes."
        ),
        "acceptance_criteria": (
            "GIVEN valid contract_id "
            "WHEN POST /api/cpmp/contracts/<id>/meetings is called "
            "THEN 201 with meeting JSON is returned. "
            "GIVEN an overdue open action item "
            "WHEN GET /api/cpmp/contracts/<id>/actions/overdue is called "
            "THEN the item appears in the response list."
        ),
        "test_path": "tests/test_pma_coord_api.py",
        "status": "backlog",
        "priority": "high",
        "depends_on_task_id": "pma-coord-01",
    },
    {
        "id": "pma-coord-03",
        "title": "AI action-item extractor — LLM parses meeting notes into structured action items",
        "description": (
            "Add `extract_action_items(meeting_text: str) -> list[dict]` to "
            "`tools/cpmp/meeting_coordinator.py`. Use LLMRouter with function "
            "`meeting_action_extraction` routed via `args/llm_config.yaml`. "
            "Prompt instructs the model to return JSON list of "
            "{owner, description, due_date, priority}. "
            "Each extracted item is auto-inserted via `create_action_item()` with "
            "status='open' and a HITL flag `ai_extracted=True` so reviewers can "
            "confirm before items are dispatched. "
            "Add `POST /api/cpmp/contracts/<id>/meetings/<mid>/extract-actions` route. "
            "Acceptance: unit test with mocked LLM returns parseable list; E2E test "
            "verifies ai_extracted flag is set."
        ),
        "acceptance_criteria": (
            "GIVEN a meeting summary containing 'ACTION: John to update IMS by Friday' "
            "WHEN extract_action_items() is called "
            "THEN an action item with owner='John' and ai_extracted=True is returned. "
            "GIVEN ai_extracted=True items exist "
            "WHEN reviewer confirms via PUT /api/cpmp/actions/<id> with confirmed=true "
            "THEN ai_extracted flag is cleared and item enters normal workflow."
        ),
        "test_path": "tests/test_pma_action_extractor.py",
        "status": "backlog",
        "priority": "medium",
        "depends_on_task_id": "pma-coord-02",
    },
    {
        "id": "pma-coord-04",
        "title": "UI — Coordination tab on CPMP contract detail page (meeting log + action item board)",
        "description": (
            "Add a 'Coordination' tab to `tools/dashboard/templates/cpmp/contract_detail.html`. "
            "Tab contains: (1) meeting log table with date/attendees/summary and 'Log Meeting' button "
            "opening a modal with textarea for notes + 'Extract Actions' button that calls the AI extractor; "
            "(2) action item board grouped by status (open/in_progress/closed) with owner, due date, "
            "overdue badge (red if past due). "
            "Follow all 8-component completeness gate requirements from CLAUDE.md. "
            "Include IQE integration: add 'coordination' to `iqe_dispatch()` in app.py and at least "
            "3 seed queries in `context/iqe/queries/cpmp/`. "
            "Acceptance: Playwright screenshot shows tab renders without 500 errors; action item "
            "with past due_date shows red badge."
        ),
        "acceptance_criteria": (
            "GIVEN dashboard is running "
            "WHEN /cpmp/contracts/<id> is opened and Coordination tab clicked "
            "THEN meeting log table and action item board render without JS errors. "
            "GIVEN an action item with due_date in the past "
            "WHEN the board renders "
            "THEN the item shows a red overdue badge."
        ),
        "test_path": "tests/test_pma_coord_ui.py",
        "status": "backlog",
        "priority": "medium",
        "depends_on_task_id": "pma-coord-03",
    },

    # ── EPIC cont ────────────────────────────────────────────────────────────
    {
        "id": "pma-cont-01",
        "title": "DB schema — personnel_registry and credential_tracker tables",
        "description": (
            "Add `pma_personnel` (id, contract_id, name TEXT, lcat TEXT, clearance_level TEXT, "
            "poly_type TEXT, poly_expiry TEXT, certifications JSON, key_person BOOL, "
            "backup_person_id TEXT, status CHECK('active','departing','vacant'), "
            "classification, created_at, updated_at) and "
            "`pma_credential_alerts` (id, person_id, alert_type TEXT, "
            "expiry_date TEXT, days_warning INT DEFAULT 90, status CHECK('open','acknowledged','resolved'), "
            "created_at) to `tools/cpmp/personnel_tracker.py`. "
            "Implement `upsert_person()`, `get_expiring_credentials(days: int)`, "
            "`get_key_person_dependencies()`, and `flag_vacancy()`. "
            "Add `pma_credential_alerts` to APPEND_ONLY_TABLES in pre_tool_use.py. "
            "Acceptance: pytest tests cover expiry detection at 90/30/7-day windows."
        ),
        "acceptance_criteria": (
            "GIVEN a person with poly_expiry 45 days from now "
            "WHEN get_expiring_credentials(days=90) is called "
            "THEN the person appears in results with days_remaining=45. "
            "GIVEN a key_person=True record with no backup_person_id "
            "WHEN get_key_person_dependencies() is called "
            "THEN the person appears as a single-point-of-failure risk."
        ),
        "test_path": "tests/test_pma_personnel.py",
        "status": "backlog",
        "priority": "high",
        "depends_on_task_id": None,
    },
    {
        "id": "pma-cont-02",
        "title": "Genesis reflex — nightly credential expiry scan and auto-alert seeding",
        "description": (
            "Add `pma_credential_monitor` to the Genesis daemon reflex registry "
            "(`tools/genesis/daemon.py` REFLEX_NAMES). "
            "Reflex calls `get_expiring_credentials(days=90)` nightly, inserts alert rows "
            "for new expirations (dedup by person_id + alert_type + expiry_date), and "
            "seeds a Kanban task via task_factory for each CRITICAL expiry (<= 30 days) "
            "with title 'URGENT: renew <type> for <name> by <date>'. "
            "Also calls `get_key_person_dependencies()` and seeds a risk register entry "
            "via risk_manager.py for each unmitigated single-point-of-failure. "
            "Acceptance: unit test mocks DB and verifies alert rows and kanban tasks created."
        ),
        "acceptance_criteria": (
            "GIVEN a person with poly_expiry 25 days from now and no existing alert "
            "WHEN the reflex fires "
            "THEN a pma_credential_alerts row is inserted and a kanban task is seeded. "
            "GIVEN a key_person with no backup "
            "WHEN the reflex fires "
            "THEN a risk register entry with category='staffing' is created if not already open."
        ),
        "test_path": "tests/test_pma_credential_reflex.py",
        "status": "backlog",
        "priority": "high",
        "depends_on_task_id": "pma-cont-01",
    },
    {
        "id": "pma-cont-03",
        "title": "API routes — personnel registry and credential alert endpoints",
        "description": (
            "Add 7 REST endpoints to `tools/dashboard/api/cpmp.py`: "
            "`POST /api/cpmp/contracts/<id>/personnel` (upsert person), "
            "`GET  /api/cpmp/contracts/<id>/personnel` (list with clearance/lcat filters), "
            "`PUT  /api/cpmp/personnel/<pid>` (update status/backup), "
            "`GET  /api/cpmp/contracts/<id>/personnel/expiring` (?days=90), "
            "`GET  /api/cpmp/contracts/<id>/personnel/key-persons` (key_person=True list), "
            "`GET  /api/cpmp/contracts/<id>/personnel/alerts` (open credential alerts), "
            "`PUT  /api/cpmp/personnel/alerts/<aid>` (acknowledge/resolve alert). "
            "Acceptance: Flask test client integration tests for each route; "
            "expiring endpoint returns only persons within days window."
        ),
        "acceptance_criteria": (
            "GIVEN personnel records with mixed expiry dates "
            "WHEN GET /api/cpmp/contracts/<id>/personnel/expiring?days=60 is called "
            "THEN only records expiring within 60 days are returned. "
            "GIVEN an open credential alert "
            "WHEN PUT /api/cpmp/personnel/alerts/<aid> with status='acknowledged' "
            "THEN alert status updates and acknowledged_at timestamp is set."
        ),
        "test_path": "tests/test_pma_personnel_api.py",
        "status": "backlog",
        "priority": "medium",
        "depends_on_task_id": "pma-cont-02",
    },
    {
        "id": "pma-cont-04",
        "title": "UI — Personnel Continuity panel on CPMP contract detail page",
        "description": (
            "Add 'Personnel' tab to `tools/dashboard/templates/cpmp/contract_detail.html`. "
            "Tab contains: (1) personnel roster table (name, LCAT, clearance, poly expiry, "
            "status) with color-coded expiry badges (green >90d, yellow 30-90d, red <30d); "
            "(2) key-person dependency matrix showing key persons and their designated backups "
            "(red cell if no backup assigned); (3) credential alerts panel listing open alerts "
            "with acknowledge button. "
            "Acceptance: Playwright screenshot shows tab with color-coded expiry badges; "
            "person with no backup shows red cell in dependency matrix."
        ),
        "acceptance_criteria": (
            "GIVEN a person with poly_expiry 20 days away "
            "WHEN Personnel tab renders "
            "THEN the expiry badge is red. "
            "GIVEN a key_person with backup_person_id=NULL "
            "WHEN the dependency matrix renders "
            "THEN that cell is highlighted red with 'No backup assigned'."
        ),
        "test_path": "tests/test_pma_personnel_ui.py",
        "status": "backlog",
        "priority": "medium",
        "depends_on_task_id": "pma-cont-03",
    },

    # ── EPIC igap ────────────────────────────────────────────────────────────
    {
        "id": "pma-igap-01",
        "title": "DB schema — int_coverage_map and collection_requirements tables",
        "description": (
            "Add `pma_int_coverage` (id, contract_id, exploitation_line_id TEXT, "
            "exploitation_title TEXT, int_disciplines JSON (list of sigint/humint/imint/masint/cybint), "
            "coverage_status CHECK('covered','partial','gap'), last_updated TEXT, "
            "confidence_score REAL, classification, created_at) and "
            "`pma_collection_requirements` (id, coverage_id, discipline TEXT, "
            "requirement_text TEXT, priority CHECK('critical','high','medium','low'), "
            "status CHECK('open','tasked','satisfied','cancelled'), "
            "ai_generated BOOL DEFAULT TRUE, created_at, updated_at). "
            "Implement `tools/cpmp/int_gap_tracker.py` with `upsert_coverage()`, "
            "`get_gaps()`, `get_persistent_gaps(days: int)`, and `create_collection_req()`. "
            "Acceptance: pytest covers gap detection and persistent gap windowing."
        ),
        "acceptance_criteria": (
            "GIVEN an exploitation line with sigint='partial' and humint='gap' "
            "WHEN get_gaps() is called "
            "THEN the line appears with the uncovered disciplines listed. "
            "GIVEN a gap first recorded 45 days ago with status still 'open' "
            "WHEN get_persistent_gaps(days=30) is called "
            "THEN the gap appears in results."
        ),
        "test_path": "tests/test_pma_igap.py",
        "status": "backlog",
        "priority": "high",
        "depends_on_task_id": None,
    },
    {
        "id": "pma-igap-02",
        "title": "AI collection requirement generator — LLM drafts tasking requirements for INT gaps",
        "description": (
            "Add `generate_collection_requirements(gap: dict) -> list[dict]` to "
            "`tools/cpmp/int_gap_tracker.py`. Uses LLMRouter with function "
            "`collection_requirement_generation`. Prompt provides the exploitation line title, "
            "missing INT disciplines, and existing coverage context; model returns JSON list of "
            "{discipline, requirement_text, priority, rationale}. "
            "Each result is auto-inserted via `create_collection_req()` with ai_generated=True "
            "and status='open'. A HITL review gate requires an analyst to set status='tasked' "
            "before the requirement is formally submitted. "
            "Add `POST /api/cpmp/contracts/<id>/coverage/<cid>/generate-reqs` route. "
            "Acceptance: mocked LLM returns valid requirement list; integration test verifies "
            "ai_generated=True and status='open' on all auto-created records."
        ),
        "acceptance_criteria": (
            "GIVEN an exploitation line with humint gap "
            "WHEN generate_collection_requirements() is called "
            "THEN at least one requirement with discipline='humint' and ai_generated=True is returned. "
            "GIVEN ai_generated requirements with status='open' "
            "WHEN analyst PUTs status='tasked' "
            "THEN the HITL gate is satisfied and the requirement is activatable."
        ),
        "test_path": "tests/test_pma_colreq_generator.py",
        "status": "backlog",
        "priority": "high",
        "depends_on_task_id": "pma-igap-01",
    },
    {
        "id": "pma-igap-03",
        "title": "Genesis reflex — weekly INT gap persistence scan and collection requirement seeding",
        "description": (
            "Add `pma_int_gap_monitor` to Genesis daemon reflex registry. "
            "Reflex fires weekly, calls `get_persistent_gaps(days=14)` to surface exploitation "
            "lines with unresolved INT gaps older than 14 days, calls "
            "`generate_collection_requirements()` for each (dedup by coverage_id + discipline), "
            "and seeds a Kanban task for each NEW critical/high requirement. "
            "Also updates the risk register via risk_manager.py with a 'compliance' risk if "
            "a critical gap has persisted > 30 days without a tasked collection requirement. "
            "Acceptance: unit test mocks DB + LLM and verifies dedup logic prevents duplicate "
            "requirements on repeat reflex firings."
        ),
        "acceptance_criteria": (
            "GIVEN a persistent gap older than 14 days with no existing collection requirement "
            "WHEN the reflex fires "
            "THEN a collection requirement is generated and a kanban task is seeded. "
            "GIVEN the same gap on a second reflex firing "
            "WHEN generate_collection_requirements() would produce the same requirement "
            "THEN no duplicate row is inserted (dedup by coverage_id + discipline + status != cancelled)."
        ),
        "test_path": "tests/test_pma_igap_reflex.py",
        "status": "backlog",
        "priority": "medium",
        "depends_on_task_id": "pma-igap-02",
    },
    {
        "id": "pma-igap-04",
        "title": "API routes — INT coverage map and collection requirement endpoints",
        "description": (
            "Add 8 REST endpoints to `tools/dashboard/api/cpmp.py`: "
            "`POST /api/cpmp/contracts/<id>/coverage` (upsert coverage record), "
            "`GET  /api/cpmp/contracts/<id>/coverage` (list with status filter), "
            "`GET  /api/cpmp/contracts/<id>/coverage/gaps` (gap and partial only), "
            "`GET  /api/cpmp/contracts/<id>/coverage/persistent` (?days=14), "
            "`POST /api/cpmp/contracts/<id>/coverage/<cid>/generate-reqs` (AI generator), "
            "`GET  /api/cpmp/coverage/<cid>/requirements` (list collection requirements), "
            "`PUT  /api/cpmp/requirements/<rid>` (update status: open→tasked→satisfied), "
            "`GET  /api/cpmp/contracts/<id>/requirements` (all reqs, filterable by discipline/status). "
            "Acceptance: Flask test client integration tests; generate-reqs returns 201 with "
            "ai_generated=True records."
        ),
        "acceptance_criteria": (
            "GIVEN coverage records with mixed statuses "
            "WHEN GET /api/cpmp/contracts/<id>/coverage/gaps is called "
            "THEN only 'gap' and 'partial' records are returned. "
            "GIVEN a collection requirement with status='open' "
            "WHEN PUT /api/cpmp/requirements/<rid> with status='tasked' "
            "THEN updated_at is set and status reflects 'tasked'."
        ),
        "test_path": "tests/test_pma_igap_api.py",
        "status": "backlog",
        "priority": "medium",
        "depends_on_task_id": "pma-igap-03",
    },
    {
        "id": "pma-igap-05",
        "title": "UI — INT Coverage Map panel on CPMP contract detail page",
        "description": (
            "Add 'INT Coverage' tab to `tools/dashboard/templates/cpmp/contract_detail.html`. "
            "Tab contains: (1) coverage matrix table — exploitation lines as rows, INT disciplines "
            "as columns (SIGINT/HUMINT/IMINT/MASINT/CYBINT), cells color-coded green/yellow/red "
            "for covered/partial/gap; (2) persistent gaps panel listing lines with gaps > 14 days "
            "and their open collection requirements; (3) 'Generate Requirements' button per gap "
            "row that calls the AI generator with HITL confirmation modal before inserting. "
            "Acceptance: Playwright screenshot shows matrix with color-coded cells; "
            "gap row shows open requirement count badge."
        ),
        "acceptance_criteria": (
            "GIVEN an exploitation line with humint=gap "
            "WHEN INT Coverage tab renders "
            "THEN the HUMINT cell is red. "
            "GIVEN a persistent gap with 2 open collection requirements "
            "WHEN the panel renders "
            "THEN a badge showing '2 open reqs' appears on that row."
        ),
        "test_path": "tests/test_pma_igap_ui.py",
        "status": "backlog",
        "priority": "medium",
        "depends_on_task_id": "pma-igap-04",
    },
    {
        "id": "pma-vv-01",
        "title": "V&V — end-to-end smoke test for all three PMA gap epics",
        "description": (
            "Run a full end-to-end validation pass across all three PMA gap epics: "
            "(1) Coordination: create meeting, extract actions via AI, confirm HITL gate, verify overdue alert; "
            "(2) Personnel Continuity: insert person with expiring poly, run credential monitor reflex, "
            "verify alert row and kanban task seeded, check key-person matrix UI; "
            "(3) INT Gap: upsert coverage with gap, run gap monitor reflex, verify collection requirement "
            "generated, confirm HITL tasking workflow. "
            "Run `python tools/testing/health_check.py --json` and `python tools/workflow/coherence_checker.py --all --gate`. "
            "Document results in `docs/features/phase-pma-gaps-vv.md`. "
            "Acceptance: zero failing assertions across all three epics; coherence gate green."
        ),
        "acceptance_criteria": (
            "GIVEN all pma-coord, pma-cont, and pma-igap tasks are done "
            "WHEN the V&V smoke test runs "
            "THEN all three epic workflows complete end-to-end without errors and coherence gate passes."
        ),
        "test_path": "tests/test_pma_e2e_vv.py",
        "status": "backlog",
        "priority": "medium",
        "depends_on_task_id": "pma-igap-05",
    },
]


def seed(dry_run: bool = False) -> None:
    now = _now()
    if dry_run:
        print(f"[DRY RUN] Would seed {len(TASKS)} tasks for project '{PROJECT_ID}':")
        for t in TASKS:
            dep = f" (depends: {t['depends_on_task_id']})" if t.get("depends_on_task_id") else ""
            print(f"  {t['id']:30s}  {t['priority']:8s}  {t['title'][:70]}{dep}")
        return

    with get_connection() as conn:
        inserted = 0
        skipped = 0
        for task in TASKS:
            existing = conn.execute(
                "SELECT id FROM kanban_tasks WHERE id = ?", (task["id"],)
            ).fetchone()
            if existing:
                skipped += 1
                print(f"  SKIP  {task['id']} (already exists)")
                continue

            full_desc = task["description"]
            ac = task.get("acceptance_criteria", "")
            tp = task.get("test_path", "")
            if ac:
                full_desc += f"\n\nAcceptance Criteria:\n{ac}"
            if tp:
                full_desc += f"\n\nTest Path: {tp}"

            conn.execute(
                """
                INSERT INTO kanban_tasks
                    (id, title, description,
                     status, priority, project_id, depends_on_task_id,
                     created_at, updated_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                """,
                (
                    task["id"],
                    task["title"],
                    full_desc,
                    task["status"],
                    task["priority"],
                    PROJECT_ID,
                    task.get("depends_on_task_id"),
                    now,
                    now,
                ),
            )
            inserted += 1
            print(f"  INSERT {task['id']}")

        conn.commit()
        print(f"\nDone — {inserted} inserted, {skipped} skipped.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Seed PMA gap kanban tasks")
    parser.add_argument("--dry-run", action="store_true", help="Preview without writing")
    args = parser.parse_args()
    seed(dry_run=args.dry_run)

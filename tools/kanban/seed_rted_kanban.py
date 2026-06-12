# CUI // SP-CTI
"""Seed Kanban tasks for DIC Real-Time Collaborative Editing (RTED).

Project: rted  (task_prefix 'rted-')
Purpose: Implement pragmatic real-time co-editing for the Document Intelligence
         Canvas using pessimistic section locking, SSE presence, per-section
         edit history, and conflict detection/merge UI.

         Deliberately avoids OT/CRDT/WebSocket complexity — section locking +
         SSE heartbeat solves the real production problem (two editors clobbering
         each other) with stdlib-only, air-gap-safe tooling.

Epics:
  lock     — Pessimistic section locking
  history  — Granular per-section edit history
  presence — User presence via SSE heartbeat
  conflict — Conflict detection + merge UI
  vv       — End-to-end validation

Run:
    python tools/kanban/seed_rted_kanban.py --dry-run
    python tools/kanban/seed_rted_kanban.py
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

PROJECT_ID = "rted"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


TASKS = [

    # ── EPIC lock — Pessimistic section locking ──────────────────────────────
    {
        "id": "rted-lock-01",
        "title": "DB schema + lock manager for dic_section_locks",
        "description": (
            "Create `dic_section_locks` table: "
            "(lock_id TEXT PK, section_id TEXT NOT NULL, locked_by TEXT NOT NULL, "
            "locked_at TEXT NOT NULL, expires_at TEXT NOT NULL, doc_id TEXT, "
            "tenant_id TEXT, classification TEXT DEFAULT 'CUI'). "
            "Implement `tools/document_intelligence/lock_manager.py` with: "
            "`acquire_lock(section_id, user_id, ttl_seconds=300) -> dict | None` "
            "(returns lock dict or None if already held by another user); "
            "`release_lock(section_id, user_id) -> bool`; "
            "`renew_lock(section_id, user_id, ttl_seconds=300) -> bool`; "
            "`get_lock(section_id) -> dict | None` (returns current holder or None if expired); "
            "`purge_expired_locks() -> int` (cleanup job). "
            "Locks are NOT append-only (they expire and release). "
            "Use `get_connection()` with RLS. "
            "Acceptance: pytest covers acquire, double-acquire by different user returns None, "
            "release, expiry purge, and renew extending TTL."
        ),
        "acceptance_criteria": (
            "GIVEN section S has no lock "
            "WHEN user A calls acquire_lock(S, 'user_a') "
            "THEN a lock dict with expires_at is returned. "
            "GIVEN user A holds a lock on S "
            "WHEN user B calls acquire_lock(S, 'user_b') "
            "THEN None is returned. "
            "GIVEN a lock with expires_at in the past "
            "WHEN purge_expired_locks() runs "
            "THEN the lock row is deleted and acquire succeeds for a new user."
        ),
        "test_path": "tests/test_rted_lock_manager.py",
        "status": "backlog",
        "priority": "high",
        "depends_on_task_id": None,
    },
    {
        "id": "rted-lock-02",
        "title": "API routes — acquire, release, renew, and status for section locks",
        "description": (
            "Add 4 REST endpoints to `tools/document_intelligence/blueprint.py`: "
            "`POST /api/sections/<section_id>/lock` — acquire lock for current user; "
            "returns 200 with lock dict or 409 {locked_by, expires_at} if held by another. "
            "`DELETE /api/sections/<section_id>/lock` — release lock (only lock holder can release). "
            "`PUT /api/sections/<section_id>/lock/renew` — extend TTL by 300s; "
            "returns 403 if caller is not the lock holder. "
            "`GET /api/sections/<section_id>/lock` — return current lock status "
            "(locked_by, expires_at) or {locked: false}. "
            "All routes use `_current_user()` for identity. "
            "Acceptance: Flask test client integration tests for each route including "
            "409 on double-acquire and 403 on unauthorized release."
        ),
        "acceptance_criteria": (
            "GIVEN no lock on section S "
            "WHEN POST /api/sections/S/lock is called "
            "THEN 200 with lock dict. "
            "GIVEN user A holds the lock "
            "WHEN user B calls POST /api/sections/S/lock "
            "THEN 409 with locked_by=user_a. "
            "GIVEN user A holds the lock "
            "WHEN user B calls DELETE /api/sections/S/lock "
            "THEN 403 Forbidden."
        ),
        "test_path": "tests/test_rted_lock_api.py",
        "status": "backlog",
        "priority": "high",
        "depends_on_task_id": "rted-lock-01",
    },
    {
        "id": "rted-lock-03",
        "title": "UI — lock indicator and auto-acquire on Edit click in doc_detail.html",
        "description": (
            "Modify the Edit button behavior in `doc_detail.html`: "
            "On click, call POST /api/sections/<id>/lock before entering edit mode. "
            "If 200: enter edit mode, show 'Editing — locked by you' green badge, "
            "start a 270s JS interval that calls PUT .../lock/renew to keep lock alive. "
            "If 409: show 'Locked by <user> until <time>' warning chip and disable Edit button. "
            "On save or cancel: call DELETE .../lock to release, clear the renew interval. "
            "On page load: call GET .../lock for each section and pre-render lock status badges. "
            "Locked sections (by another user) show a red lock icon on the section header. "
            "Acceptance: manual test — open two browser tabs as different users; "
            "second user sees the lock badge; first user saves and second user can then acquire."
        ),
        "acceptance_criteria": (
            "GIVEN user A is editing section S "
            "WHEN user B loads the page "
            "THEN section S shows a red lock badge with user A's name. "
            "GIVEN user A finishes editing and saves "
            "WHEN the save completes "
            "THEN the lock is released and user B's badge clears on next poll."
        ),
        "test_path": "tests/test_rted_lock_ui.py",
        "status": "backlog",
        "priority": "high",
        "depends_on_task_id": "rted-lock-02",
    },

    # ── EPIC history — Per-section edit history ───────────────────────────────
    {
        "id": "rted-hist-01",
        "title": "DB schema + history recorder for dic_edit_history",
        "description": (
            "Create `dic_edit_history` table (append-only, NIST AU): "
            "(edit_id TEXT PK, section_id TEXT NOT NULL, doc_id TEXT, "
            "version_id TEXT, editor TEXT NOT NULL, "
            "content_before TEXT, content_after TEXT NOT NULL, "
            "char_delta INT, diff_summary TEXT, "
            "edited_at TEXT NOT NULL, tenant_id TEXT, classification TEXT DEFAULT 'CUI'). "
            "Add `dic_edit_history` to APPEND_ONLY_TABLES in `.claude/hooks/pre_tool_use.py`. "
            "Implement `record_edit(section_id, editor, before, after)` in "
            "`tools/document_intelligence/history_recorder.py`. "
            "Use stdlib `difflib.unified_diff` to produce a compact diff_summary string (<= 500 chars). "
            "Hook into the existing `/api/sections/<id>/content` POST route so every save "
            "automatically records an edit history entry. "
            "Acceptance: pytest covers record_edit, char_delta computation, "
            "diff_summary non-empty for changed content, empty content edge case."
        ),
        "acceptance_criteria": (
            "GIVEN section content changes from 'The team uses AI.' to 'The Contractor uses AI.' "
            "WHEN record_edit() is called "
            "THEN an edit_history row is inserted with char_delta=-4 (approx) and non-empty diff_summary. "
            "GIVEN content_before == content_after "
            "WHEN record_edit() is called "
            "THEN no row is inserted (no-op for identical saves)."
        ),
        "test_path": "tests/test_rted_history_recorder.py",
        "status": "backlog",
        "priority": "high",
        "depends_on_task_id": None,
    },
    {
        "id": "rted-hist-02",
        "title": "API route — GET section edit history",
        "description": (
            "Add `GET /api/sections/<section_id>/history` to the DIC blueprint. "
            "Returns up to 50 most recent edit history entries for the section, ordered "
            "newest-first: {edit_id, editor, edited_at, char_delta, diff_summary}. "
            "Supports `?limit=N` (max 100) and `?since=ISO_DATE` query params. "
            "Acceptance: integration test confirms entries appear after a content save, "
            "limit param is respected, and no content_before/content_after is returned "
            "(keep full text out of the list endpoint for bandwidth)."
        ),
        "acceptance_criteria": (
            "GIVEN 5 edits recorded for section S "
            "WHEN GET /api/sections/S/history?limit=3 is called "
            "THEN exactly 3 entries are returned in descending edited_at order. "
            "GIVEN no edits for section S "
            "WHEN GET /api/sections/S/history is called "
            "THEN 200 with empty history list."
        ),
        "test_path": "tests/test_rted_history_api.py",
        "status": "backlog",
        "priority": "medium",
        "depends_on_task_id": "rted-hist-01",
    },
    {
        "id": "rted-hist-03",
        "title": "UI — History button and edit timeline panel in doc_detail.html",
        "description": (
            "Add a '🕒 History' button to each section action row in `doc_detail.html`. "
            "On click, fetch GET /api/sections/<id>/history and render a collapsible timeline "
            "panel below the section (similar to the annotation panel pattern). "
            "Each entry shows: editor name, relative time (e.g. '2h ago'), char_delta badge "
            "(green +N / red -N), and the diff_summary in a monospace code block. "
            "Panel has a 'Close' button. "
            "Acceptance: clicking History on a section with saves shows the timeline; "
            "clicking again collapses it; sections with no history show 'No edit history yet'."
        ),
        "acceptance_criteria": (
            "GIVEN a section with 3 recorded edits "
            "WHEN the History button is clicked "
            "THEN a timeline panel appears with 3 entries showing editor, time, and char_delta. "
            "GIVEN a section with no edits "
            "WHEN the History button is clicked "
            "THEN 'No edit history yet' message is shown."
        ),
        "test_path": "tests/test_rted_history_ui.py",
        "status": "backlog",
        "priority": "medium",
        "depends_on_task_id": "rted-hist-02",
    },

    # ── EPIC presence — SSE user presence ────────────────────────────────────
    {
        "id": "rted-pres-01",
        "title": "Presence registry — in-memory heartbeat table + dic_presence_sessions",
        "description": (
            "Create `dic_presence_sessions` table (NOT append-only — rows upserted on heartbeat): "
            "(session_id TEXT PK, doc_id TEXT NOT NULL, user_id TEXT NOT NULL, "
            "active_section_id TEXT, last_seen TEXT NOT NULL, "
            "tenant_id TEXT, classification TEXT DEFAULT 'CUI'). "
            "Implement `tools/document_intelligence/presence_registry.py` with: "
            "`heartbeat(doc_id, user_id, section_id=None)` — upsert presence row with "
            "last_seen=now(); `get_presence(doc_id) -> list[dict]` — return users active "
            "within the last 60 seconds; `cleanup_stale(doc_id)` — delete rows older than 120s. "
            "Acceptance: pytest covers heartbeat upsert, get_presence only returns recent entries, "
            "cleanup removes stale rows."
        ),
        "acceptance_criteria": (
            "GIVEN user A heartbeats on doc D "
            "WHEN get_presence(D) is called within 60s "
            "THEN user A appears in the list. "
            "GIVEN user A's last_seen is 130s ago "
            "WHEN cleanup_stale(D) runs "
            "THEN user A's row is deleted and get_presence returns empty."
        ),
        "test_path": "tests/test_rted_presence_registry.py",
        "status": "backlog",
        "priority": "medium",
        "depends_on_task_id": None,
    },
    {
        "id": "rted-pres-02",
        "title": "API routes — SSE presence stream and heartbeat ping",
        "description": (
            "Add 2 endpoints to the DIC blueprint: "
            "`GET /api/doc/<doc_id>/presence/stream` — SSE stream (text/event-stream) that "
            "polls `get_presence(doc_id)` every 10s and emits a `presence` event with JSON "
            "list of {user_id, active_section_id, last_seen}. Closes when client disconnects. "
            "`POST /api/doc/<doc_id>/presence/ping` — accepts {section_id} in body, calls "
            "`heartbeat(doc_id, current_user, section_id)`, returns 204. "
            "Use Flask `Response(stream_with_context(...), mimetype='text/event-stream')` "
            "matching the existing SSE pattern in the DIC blueprint (ingest job stream). "
            "Acceptance: integration test confirms ping updates presence and SSE emits "
            "the updated list within the next poll cycle."
        ),
        "acceptance_criteria": (
            "GIVEN user A pings /api/doc/D/presence/ping with section_id=S "
            "WHEN GET /api/doc/D/presence/stream is consumed "
            "THEN a presence event containing user_a with active_section_id=S is emitted. "
            "GIVEN no pings for 120s "
            "WHEN the stream emits the next event "
            "THEN the stale user no longer appears."
        ),
        "test_path": "tests/test_rted_presence_api.py",
        "status": "backlog",
        "priority": "medium",
        "depends_on_task_id": "rted-pres-01",
    },
    {
        "id": "rted-pres-03",
        "title": "UI — presence avatars per section using EventSource in doc_detail.html",
        "description": (
            "Wire presence into `doc_detail.html` using the browser EventSource API. "
            "On page load, open `new EventSource('/document-intelligence/api/doc/<doc_id>/presence/stream')`. "
            "On each `presence` event, update presence badge in each section header: "
            "show colored initials chips for users whose `active_section_id` matches that section. "
            "A global 'N editing' chip in the page header shows total active editors. "
            "Send a heartbeat ping (POST .../presence/ping) every 30s with the currently "
            "focused section (track via element focus/click events on section cards). "
            "On page unload, send a final ping with section_id=null to clear presence. "
            "Acceptance: two browser tabs show each other's presence chips; "
            "closing a tab clears the chip within ~120s."
        ),
        "acceptance_criteria": (
            "GIVEN user A opens doc D and clicks section S "
            "WHEN user B's browser receives the next SSE presence event "
            "THEN section S shows user A's initials chip. "
            "GIVEN user A closes the tab "
            "WHEN 120s elapses "
            "THEN user A's chip disappears from user B's view."
        ),
        "test_path": "tests/test_rted_presence_ui.py",
        "status": "backlog",
        "priority": "medium",
        "depends_on_task_id": "rted-pres-02",
    },

    # ── EPIC conflict — Conflict detection and merge ──────────────────────────
    {
        "id": "rted-conf-01",
        "title": "Conflict detection — content_hash check on section save",
        "description": (
            "Modify `POST /api/sections/<section_id>/content` to accept an optional "
            "`expected_hash` field in the request body. "
            "`expected_hash` is the SHA-256 of the content the client loaded before editing "
            "(computed client-side as `btoa(content)` → SHA-256, or supplied by the lock "
            "acquire response which includes the current content hash). "
            "On save, compute `sha256(current_db_content)`. If `expected_hash` is provided "
            "and does not match, return 409 with: "
            "{conflict: true, your_content: <submitted>, current_content: <db>, "
            "current_editor: <last editor from history>}. "
            "If `expected_hash` is absent or matches, save normally (backward-compatible). "
            "Add `current_content_hash` to the lock acquire response so the client can "
            "populate expected_hash automatically. "
            "Acceptance: pytest tests expected_hash match saves, mismatch returns 409 with "
            "both versions, absent expected_hash always saves."
        ),
        "acceptance_criteria": (
            "GIVEN section S has content hash H1 "
            "WHEN POST /api/sections/S/content with expected_hash=H1 and new content "
            "THEN 200 save succeeds. "
            "GIVEN section S content changed to H2 after user A loaded it "
            "WHEN user A submits save with expected_hash=H1 "
            "THEN 409 with conflict=true, both versions, and current_editor."
        ),
        "test_path": "tests/test_rted_conflict_detection.py",
        "status": "backlog",
        "priority": "high",
        "depends_on_task_id": "rted-lock-03",
    },
    {
        "id": "rted-conf-02",
        "title": "UI — conflict resolution modal in doc_detail.html",
        "description": (
            "When the section save fetch returns 409 with conflict=true, intercept in "
            "`editSection()` JS function and show a conflict resolution modal. "
            "Modal layout: two-column side-by-side view — left: 'Your changes' (submitted), "
            "right: 'Current version' (from DB, by current_editor). "
            "Three action buttons: "
            "'Keep mine' — resubmit with force=true (skip hash check), "
            "'Keep theirs' — discard local changes and reload section content, "
            "'Merge manually' — open a textarea pre-populated with the user's content "
            "alongside the DB version for manual reconciliation, then save. "
            "Add `force=true` bypass to the save route: if force is present in body, "
            "skip the expected_hash check and overwrite. "
            "Acceptance: trigger a conflict in two tabs; conflict modal appears; "
            "each resolution option produces the correct DB outcome."
        ),
        "acceptance_criteria": (
            "GIVEN a 409 conflict response is received "
            "WHEN the conflict modal appears "
            "THEN both versions are shown side-by-side. "
            "GIVEN the user clicks 'Keep mine' "
            "WHEN the force save completes "
            "THEN the DB contains the user's version. "
            "GIVEN the user clicks 'Keep theirs' "
            "WHEN the modal closes "
            "THEN the section content reverts to the DB version without a save."
        ),
        "test_path": "tests/test_rted_conflict_ui.py",
        "status": "backlog",
        "priority": "medium",
        "depends_on_task_id": "rted-conf-01",
    },

    # ── EPIC vv — End-to-end validation ──────────────────────────────────────
    {
        "id": "rted-vv-01",
        "title": "V&V — end-to-end smoke test for all four RTED epics",
        "description": (
            "Run a full end-to-end validation pass across all four RTED epics: "
            "(1) Lock: acquire lock as user A, verify user B gets 409, release, verify B can acquire. "
            "(2) History: save section content twice, verify two edit_history rows with correct "
            "char_delta and diff_summary. "
            "(3) Presence: ping as two users, verify get_presence returns both, wait 130s and "
            "verify cleanup removes stale entry. "
            "(4) Conflict: simulate concurrent edit — load hash, change DB content via direct "
            "API call, submit save with stale hash, verify 409; test all three resolution paths. "
            "Run `python tools/testing/health_check.py --json` and "
            "`python tools/workflow/coherence_checker.py --all --gate`. "
            "Document results in `docs/features/phase-rted-collaborative-editing-vv.md`. "
            "Acceptance: zero failing assertions; coherence gate green; feature doc exists."
        ),
        "acceptance_criteria": (
            "GIVEN all rted-lock, rted-hist, rted-pres, and rted-conf tasks are done "
            "WHEN the V&V smoke test runs "
            "THEN all four epic workflows complete end-to-end and coherence gate passes."
        ),
        "test_path": "tests/test_rted_e2e_vv.py",
        "status": "backlog",
        "priority": "medium",
        "depends_on_task_id": "rted-conf-02",
    },
]


def seed(dry_run: bool = False) -> None:
    now = _now()
    if dry_run:
        print(f"[DRY RUN] Would seed {len(TASKS)} tasks for project '{PROJECT_ID}':")
        for t in TASKS:
            dep = f" (depends: {t['depends_on_task_id']})" if t.get("depends_on_task_id") else ""
            print(f"  {t['id']:25s}  {t['priority']:8s}  {t['title'][:65]}{dep}")
        return

    with get_connection() as conn:
        inserted = skipped = 0
        for task in TASKS:
            existing = conn.execute(
                "SELECT id FROM kanban_tasks WHERE id = %s", (task["id"],)
            ).fetchone()
            if existing:
                skipped += 1
                print(f"  SKIP  {task['id']}")
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
                    (id, title, description, status, priority, project_id,
                     depends_on_task_id, created_at, updated_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                """,
                (
                    task["id"], task["title"], full_desc,
                    task["status"], task["priority"], PROJECT_ID,
                    task.get("depends_on_task_id"), now, now,
                ),
            )
            inserted += 1
            print(f"  INSERT {task['id']}")

        conn.commit()
        print(f"\nDone — {inserted} inserted, {skipped} skipped.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Seed RTED collaborative editing tasks")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    seed(dry_run=args.dry_run)

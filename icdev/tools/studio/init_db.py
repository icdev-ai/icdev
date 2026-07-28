"""ICDEV™ Studio — Database Initialization.

Creates studio_* tables in icdev.db.  Idempotent — safe to run repeatedly.
Separated from tools/db/init_icdev_db.py to avoid merge conflicts during
parallel development (Phase 72).
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

# Allow running standalone or imported
_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from tools.db.storage import get_connection  # noqa: E402

STUDIO_TABLES: dict[str, str] = {
    # ── Workflows ──────────────────────────────────────────
    "studio_workflows": """
        CREATE TABLE IF NOT EXISTS studio_workflows (
            workflow_id   TEXT PRIMARY KEY,
            name          TEXT NOT NULL,
            description   TEXT,
            template_yaml TEXT NOT NULL,
            category      TEXT,
            created_by    TEXT,
            created_at    TEXT DEFAULT (datetime('now')),
            updated_at    TEXT DEFAULT (datetime('now')),
            version       INTEGER DEFAULT 1,
            shared        INTEGER DEFAULT 0
        )
    """,
    # ── Forms ──────────────────────────────────────────────
    "studio_forms": """
        CREATE TABLE IF NOT EXISTS studio_forms (
            form_id       TEXT PRIMARY KEY,
            name          TEXT NOT NULL,
            description   TEXT,
            schema_json   TEXT NOT NULL,
            created_by    TEXT,
            created_at    TEXT DEFAULT (datetime('now')),
            updated_at    TEXT DEFAULT (datetime('now')),
            version       INTEGER DEFAULT 1,
            status        TEXT DEFAULT 'draft'
                          CHECK(status IN ('draft','published','archived'))
        )
    """,
    "studio_form_submissions": """
        CREATE TABLE IF NOT EXISTS studio_form_submissions (
            submission_id TEXT PRIMARY KEY,
            form_id       TEXT NOT NULL REFERENCES studio_forms(form_id),
            data_json     TEXT NOT NULL,
            submitted_by  TEXT,
            submitted_at  TEXT DEFAULT (datetime('now'))
        )
    """,
    # ── Cases ──────────────────────────────────────────────
    "studio_case_types": """
        CREATE TABLE IF NOT EXISTS studio_case_types (
            type_id        TEXT PRIMARY KEY,
            name           TEXT NOT NULL,
            lifecycle_json TEXT NOT NULL,
            created_at     TEXT DEFAULT (datetime('now'))
        )
    """,
    "studio_cases": """
        CREATE TABLE IF NOT EXISTS studio_cases (
            case_id            TEXT PRIMARY KEY,
            type_id            TEXT NOT NULL REFERENCES studio_case_types(type_id),
            title              TEXT NOT NULL,
            description        TEXT,
            current_state      TEXT NOT NULL,
            priority           TEXT DEFAULT 'medium'
                               CHECK(priority IN ('critical','high','medium','low')),
            assigned_to        TEXT,
            created_by         TEXT,
            created_at         TEXT DEFAULT (datetime('now')),
            updated_at         TEXT DEFAULT (datetime('now')),
            due_date           TEXT,
            form_submission_id TEXT REFERENCES studio_form_submissions(submission_id)
        )
    """,
    # APPEND-ONLY — audit trail
    "studio_case_history": """
        CREATE TABLE IF NOT EXISTS studio_case_history (
            history_id TEXT PRIMARY KEY,
            case_id    TEXT NOT NULL REFERENCES studio_cases(case_id),
            from_state TEXT,
            to_state   TEXT NOT NULL,
            changed_by TEXT,
            changed_at TEXT DEFAULT (datetime('now')),
            comment    TEXT
        )
    """,
    # ── Automations ────────────────────────────────────────
    "studio_automations": """
        CREATE TABLE IF NOT EXISTS studio_automations (
            automation_id  TEXT PRIMARY KEY,
            name           TEXT NOT NULL,
            description    TEXT,
            trigger_json   TEXT NOT NULL,
            condition_json TEXT,
            action_json    TEXT NOT NULL,
            enabled        INTEGER DEFAULT 1,
            created_by     TEXT,
            created_at     TEXT DEFAULT (datetime('now'))
        )
    """,
    # APPEND-ONLY — audit trail
    "studio_automation_runs": """
        CREATE TABLE IF NOT EXISTS studio_automation_runs (
            run_id        TEXT PRIMARY KEY,
            automation_id TEXT NOT NULL REFERENCES studio_automations(automation_id),
            trigger_event TEXT,
            status        TEXT CHECK(status IN (
                              'triggered','running','success','failed','skipped')),
            result_json   TEXT,
            started_at    TEXT DEFAULT (datetime('now')),
            completed_at  TEXT
        )
    """,
    # ── Workflow Runs (APPEND-ONLY — audit trail) ──────────
    "studio_workflow_runs": """
        CREATE TABLE IF NOT EXISTS studio_workflow_runs (
            run_id         TEXT PRIMARY KEY,
            workflow_id    TEXT NOT NULL,
            workflow_name  TEXT,
            status         TEXT DEFAULT 'pending'
                           CHECK(status IN ('pending','running','success','failed','cancelled','awaiting_approval')),
            started_at     TEXT DEFAULT (datetime('now')),
            completed_at   TEXT,
            triggered_by   TEXT,
            project_id     TEXT DEFAULT 'default',
            summary_json   TEXT
        )
    """,
    "studio_workflow_run_steps": """
        CREATE TABLE IF NOT EXISTS studio_workflow_run_steps (
            step_run_id  TEXT PRIMARY KEY,
            run_id       TEXT NOT NULL,
            step_id      TEXT NOT NULL,
            step_name    TEXT,
            tool         TEXT,
            status       TEXT DEFAULT 'pending'
                         CHECK(status IN ('pending','running','success','failed','skipped','timeout','awaiting_approval','approved','rejected')),
            exit_code    INTEGER,
            stdout       TEXT,
            stderr       TEXT,
            duration_ms  INTEGER DEFAULT 0,
            started_at   TEXT DEFAULT (datetime('now')),
            completed_at TEXT
        )
    """,
    # ── Run-scoped memory (dwo-mem-01) — mutable, per-run step state ──
    "studio_run_memory": """
        CREATE TABLE IF NOT EXISTS studio_run_memory (
            run_id     TEXT NOT NULL,
            key        TEXT NOT NULL,
            value_json TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (run_id, key)
        )
    """,
    # ── Event sources & workflow triggers (dwo-evt-01) ─────
    "studio_event_sources": """
        CREATE TABLE IF NOT EXISTS studio_event_sources (
            source_id   TEXT PRIMARY KEY,
            name        TEXT NOT NULL,
            kind        TEXT NOT NULL
                        CHECK(kind IN ('gateway_channel','canvas_bus','schedule','manual')),
            config_json TEXT,
            max_il      TEXT DEFAULT 'IL2',
            enabled     INTEGER DEFAULT 1,
            created_by  TEXT,
            created_at  TEXT DEFAULT (datetime('now'))
        )
    """,
    "studio_workflow_triggers": """
        CREATE TABLE IF NOT EXISTS studio_workflow_triggers (
            trigger_id        TEXT PRIMARY KEY,
            source_id         TEXT NOT NULL REFERENCES studio_event_sources(source_id),
            workflow_id       TEXT NOT NULL REFERENCES studio_workflows(workflow_id),
            event_type        TEXT,
            filter_json       TEXT,
            input_mapping_json TEXT,
            workflow_il       TEXT DEFAULT 'IL6',
            project_id        TEXT DEFAULT 'default',
            enabled           INTEGER DEFAULT 1,
            created_at        TEXT DEFAULT (datetime('now'))
        )
    """,
    # APPEND-ONLY — audit trail: why did this run start (and why did it not).
    # idempotency_key is UNIQUE — a replayed webhook delivery loses the INSERT
    # and therefore never starts a second run (dwo-evt-02).
    "studio_trigger_events": """
        CREATE TABLE IF NOT EXISTS studio_trigger_events (
            event_id     TEXT PRIMARY KEY,
            source_id    TEXT,
            trigger_id   TEXT,
            workflow_id  TEXT,
            event_type   TEXT,
            payload_json TEXT,
            matched      INTEGER DEFAULT 0,
            outcome      TEXT,
            classification TEXT,
            idempotency_key TEXT UNIQUE,
            envelope_id  TEXT,
            run_id       TEXT,
            reason       TEXT,
            received_at  TEXT DEFAULT (datetime('now')),
            created_at   TEXT
        )
    """,
    # ── Dashboards ─────────────────────────────────────────
    "studio_dashboards": """
        CREATE TABLE IF NOT EXISTS studio_dashboards (
            dashboard_id TEXT PRIMARY KEY,
            name         TEXT NOT NULL,
            layout_json  TEXT NOT NULL,
            role_default TEXT,
            created_by   TEXT,
            created_at   TEXT DEFAULT (datetime('now')),
            updated_at   TEXT DEFAULT (datetime('now')),
            shared       INTEGER DEFAULT 0
        )
    """,
}

# Tables that must NEVER have UPDATE or DELETE operations
APPEND_ONLY_TABLES = [
    "studio_case_history",
    "studio_automation_runs",
    "studio_workflow_runs",
    "studio_workflow_run_steps",
    "studio_trigger_events",
]


def _table_exists(conn, name: str) -> bool:
    """Check if a table exists (works on both SQLite and PostgreSQL)."""
    from tools.db.storage import get_backend

    backend = get_backend()
    if backend == "postgresql":
        row = conn.execute(
            "SELECT 1 FROM information_schema.tables WHERE table_schema = 'public' AND table_name = %s",
            (name,),
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=%s",
            (name,),
        ).fetchone()
    return row is not None


def init_studio_tables(*, verbose: bool = False) -> dict:
    """Create all studio tables.  Returns summary dict."""
    conn = get_connection()
    created: list[str] = []
    skipped: list[str] = []
    try:
        for name, ddl in STUDIO_TABLES.items():
            if _table_exists(conn, name):
                skipped.append(name)
                if verbose:
                    print(f"  exists: {name}")
            else:
                conn.execute(ddl)
                created.append(name)
                if verbose:
                    print(f"  created: {name}")
        conn.commit()
    finally:
        conn.close()

    return {
        "status": "ok",
        "tables_created": created,
        "tables_existing": skipped,
        "total": len(STUDIO_TABLES),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Initialize ICDEV™ Studio tables")
    parser.add_argument("--json", action="store_true", help="JSON output")
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()

    result = init_studio_tables(verbose=args.verbose)

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(f"Studio DB init: {len(result['tables_created'])} created, {len(result['tables_existing'])} existing")
        if result["tables_created"]:
            for t in result["tables_created"]:
                print(f"  + {t}")


if __name__ == "__main__":
    main()

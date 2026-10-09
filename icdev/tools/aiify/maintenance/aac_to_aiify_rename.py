# CUI // SP-CTI
"""AAC -> AI-ify canvas table rename (data-preserving). OPERATOR SCRIPT.

MOVED OUT OF tools/db/migrations/ 2026-08-15. It sat there as a bare
``172_aiify_rename.py``, which MigrationRunner cannot discover, so it looked like
a migration and had never run. The fix for its siblings was to promote them into
discoverable directories; the fix for THIS one is the opposite, because it is not
a migration for the database the runner drives.

It writes to the AI-ify CANVAS database (AIIFY_PG_DATABASE), not to icdev, and
its own note says any connection passed by a runner is ignored. Making the runner
execute it would point a data move at the wrong database and record success
against a schema it never touched. Run it deliberately instead.


The AI Augmentation Canvas (AAC) was renamed to "AI-ify". This migration copies
existing data from the legacy ``aac_*`` tables into the new ``aiify_*`` tables
without dropping or mutating the originals — the append-only audit table
``aac_audit_log`` is never UPDATEd or DELETEd (NIST AU preserved).

Strategy: CREATE new (via the canvas init_db, which includes the new scoring
columns) + INSERT…SELECT copy preserving primary keys so cross-table foreign
keys stay intact. Idempotent: a target that already has rows is skipped.

NOTE: AI-ify canvas tables live in the *canvas* DB (env AIIFY_PG_DATABASE /
data/aiify_canvas.db), not the main ICDEV DB. This migration opens the canvas
connection itself; any conn passed by a runner is ignored.
"""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[3]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

# Legacy table -> (new table, ordered column list shared by both schemas)
_COPY_PLAN = [
    ("aac_scans", "aiify_scans",
     ["scan_id", "input_type", "input_ref", "language_profile", "total_files",
      "total_loc", "status", "project_summary", "created_at", "completed_at"]),
    ("aac_opportunities", "aiify_opportunities",
     ["opportunity_id", "scan_id", "module_path", "function_name", "line_start",
      "line_end", "language", "pattern_type", "pattern_detail", "ai_paradigm",
      "il_recommended_model", "data_requirements", "created_at"]),
    ("aac_scores", "aiify_scores",
     ["score_id", "opportunity_id", "value_score", "feasibility_score",
      "risk_score", "composite_score", "score_detail", "scored_at"]),
    ("aac_roadmaps", "aiify_roadmaps",
     ["id", "scan_id", "roadmap_id", "title", "phases", "total_effort_days",
      "aimc_links", "aadc_links", "created_at"]),
    ("aac_audit_log", "aiify_audit_log",
     ["id", "event_type", "scan_id", "actor", "detail", "created_at"]),
    ("aac_hitl_decisions", "aiify_hitl_decisions",
     ["id", "source_type", "source_id", "phase_id", "decision", "reason",
      "actor", "decided_at"]),
]

_PK = {
    "aiify_scans": "scan_id",
    "aiify_opportunities": "opportunity_id",
    "aiify_scores": "score_id",
    "aiify_roadmaps": "id",
    "aiify_audit_log": "id",
    "aiify_hitl_decisions": "id",
}


def _table_exists(cur, table: str) -> bool:
    try:
        cur.execute(f"SELECT 1 FROM {table} LIMIT 1")
        cur.fetchall()
        return True
    except Exception:
        return False


def _row_count(cur, table: str) -> int:
    try:
        cur.execute(f"SELECT COUNT(*) FROM {table}")
        return int(cur.fetchone()[0])
    except Exception:
        return 0


def migrate() -> None:
    """Apply migration 172 against the AI-ify canvas DB (idempotent)."""
    from tools.aiify.db.init_db import init_db, get_connection

    init_db()  # ensure aiify_* exist with the full (new-column) schema
    canvas = get_connection()
    is_pg = canvas.__class__.__module__.startswith("psycopg")
    copied_any = False
    try:
        cur = canvas.cursor() if hasattr(canvas, "cursor") else canvas
        for old, new, cols in _COPY_PLAN:
            if not _table_exists(cur, old):
                continue
            if _row_count(cur, new) > 0:
                continue  # already migrated
            col_csv = ", ".join(cols)
            try:
                cur.execute(f"INSERT INTO {new} ({col_csv}) SELECT {col_csv} FROM {old}")
                canvas.commit()
                copied_any = True
                print(f"[migration 172] copied {old} -> {new}")
            except Exception as exc:  # noqa: BLE001
                canvas.rollback()
                print(f"[migration 172] skip {old} -> {new}: {exc}")

        if is_pg and copied_any:
            for new, pk in _PK.items():
                try:
                    cur.execute(
                        f"SELECT setval(pg_get_serial_sequence('{new}', '{pk}'), "
                        f"COALESCE((SELECT MAX({pk}) FROM {new}), 1))"
                    )
                    canvas.commit()
                except Exception:
                    canvas.rollback()
        print("[migration 172] AAC -> AI-ify data copy complete")
    finally:
        canvas.close()


def main() -> int:
    """CLI entry point."""
    print("Migration 172: AAC -> AI-ify canvas rename")
    migrate()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

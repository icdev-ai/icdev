"""
Migration 084 — WNE (Workflow Narrative Engine) session and artifact tables.

Creates (if not present):
  wne_sessions   — chat-driven workflow sessions
  wne_artifacts  — generated artifacts per session (append-only, NIST AU)
"""
from tools.db.storage import get_connection


def up(conn=None) -> None:
    conn = get_connection()
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS wne_sessions (
                id TEXT PRIMARY KEY,
                workflow_id TEXT,
                template_slug TEXT,
                status TEXT CHECK(status IN (
                    'collecting','confirming','generating','reviewing','done','failed'
                )),
                context_json TEXT,
                chat_context_id TEXT,
                org_name TEXT,
                audience TEXT,
                program_name TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_wne_sessions_status "
            "ON wne_sessions(status)"
        )

        conn.execute("""
            CREATE TABLE IF NOT EXISTS wne_artifacts (
                id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL REFERENCES wne_sessions(id),
                artifact_type TEXT CHECK(artifact_type IN (
                    'exec_brief','coa_comparison','budget_table',
                    'roi_analysis','slide_outline','zip_bundle'
                )),
                content TEXT,
                generated_at TEXT NOT NULL
            )
        """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_wne_artifacts_session_id "
            "ON wne_artifacts(session_id)"
        )

        conn.commit()
        print("Migration 084 up: wne_sessions, wne_artifacts created.")
    finally:
        conn.close()


if __name__ == "__main__":
    up()

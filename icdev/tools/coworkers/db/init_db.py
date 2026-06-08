# CUI // SP-CTI
"""Co-Workers canvas DB initializer.

Creates two canvas tables (no classification/tenant_id columns) via
get_canvas_connection() so RLS is disabled.
"""
from __future__ import annotations

# ---------------------------------------------------------------------------
# State constants — SQL CHECK constraints are derived from these; never hardcode
# ---------------------------------------------------------------------------

COWORKER_STATES: tuple[str, ...] = (
    "active",
    "inactive",
    "deprecated",
)

_CHECK_COWORKER_STATE = f"IN ({', '.join(f\"'{v}'\" for v in COWORKER_STATES)})"

SCHEMA = f"""
CREATE TABLE IF NOT EXISTS cwk_coworkers (
    id              TEXT PRIMARY KEY,
    slug            TEXT NOT NULL UNIQUE,
    name            TEXT NOT NULL,
    description     TEXT DEFAULT '',
    role_type       TEXT DEFAULT 'general',
    capabilities_json TEXT DEFAULT '[]',
    config_json     TEXT DEFAULT '{{}}',
    status          TEXT NOT NULL DEFAULT 'active'
        CHECK(status {_CHECK_COWORKER_STATE}),
    created_at      TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at      TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_cwk_coworkers_slug   ON cwk_coworkers(slug);
CREATE INDEX IF NOT EXISTS idx_cwk_coworkers_status ON cwk_coworkers(status);

CREATE TABLE IF NOT EXISTS cwk_sessions (
    id              TEXT PRIMARY KEY,
    coworker_id     TEXT NOT NULL,
    chat_context_id TEXT,
    ace_instance_id TEXT,
    created_at      TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_cwk_sessions_coworker ON cwk_sessions(coworker_id);
CREATE INDEX IF NOT EXISTS idx_cwk_sessions_chat     ON cwk_sessions(chat_context_id);
CREATE INDEX IF NOT EXISTS idx_cwk_sessions_ace      ON cwk_sessions(ace_instance_id);
"""


def init() -> None:
    """Create all Co-Workers canvas tables (idempotent)."""
    from icdev.tools.db.storage import get_canvas_connection

    conn = get_canvas_connection("ICDEV_COWORKERS_DB_URL")
    try:
        conn.executescript(SCHEMA)
        conn.commit()
    finally:
        conn.close()


if __name__ == "__main__":
    init()
    from icdev.tools.db.storage import get_canvas_connection

    conn = get_canvas_connection("ICDEV_COWORKERS_DB_URL")
    try:
        try:
            rows = conn.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'public' AND table_name LIKE 'cwk_%' ORDER BY table_name"
            ).fetchall()
            col = 0
        except Exception:
            rows = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'cwk_%' ORDER BY name"
            ).fetchall()
            col = 0
        print(f"Co-Workers canvas DB initialized: {len(rows)} tables")
        for r in rows:
            print(f"  {r[col]}")
    finally:
        conn.close()

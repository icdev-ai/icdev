"""Auto-generated models — AppForge."""

from pathlib import Path

DB_PATH = Path(__file__).parent / "data" / "app.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS threats (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    description TEXT,
    lat         REAL,
    lon         REAL,
    status      TEXT DEFAULT 'active',
    score       REAL DEFAULT 0.0,
    metadata    TEXT,
    created_at  TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS vulnerabilitys (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    description TEXT,
    lat         REAL,
    lon         REAL,
    status      TEXT DEFAULT 'active',
    score       REAL DEFAULT 0.0,
    metadata    TEXT,
    created_at  TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS assets (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    description TEXT,
    lat         REAL,
    lon         REAL,
    status      TEXT DEFAULT 'active',
    score       REAL DEFAULT 0.0,
    metadata    TEXT,
    created_at  TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS indicators (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    description TEXT,
    lat         REAL,
    lon         REAL,
    status      TEXT DEFAULT 'active',
    score       REAL DEFAULT 0.0,
    metadata    TEXT,
    created_at  TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS campaigns (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    description TEXT,
    lat         REAL,
    lon         REAL,
    status      TEXT DEFAULT 'active',
    score       REAL DEFAULT 0.0,
    metadata    TEXT,
    created_at  TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS actors (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    description TEXT,
    lat         REAL,
    lon         REAL,
    status      TEXT DEFAULT 'active',
    score       REAL DEFAULT 0.0,
    metadata    TEXT,
    created_at  TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS kg_edges (
    edge_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    source_type TEXT NOT NULL,
    source_id   TEXT NOT NULL,
    target_type TEXT NOT NULL,
    target_id   TEXT NOT NULL,
    relation    TEXT NOT NULL,
    weight      REAL DEFAULT 1.0,
    metadata    TEXT,
    created_at  TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_kg_source ON kg_edges(source_type, source_id);
CREATE INDEX IF NOT EXISTS idx_kg_target ON kg_edges(target_type, target_id);
"""

def get_connection():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = get_connection(str(DB_PATH))
    return conn

def init_db():
    conn = get_connection()
    try:
        conn.executescript(SCHEMA)
        conn.commit()
        tables = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()]
        return {"status": "ok", "tables": tables}
    finally:
        conn.close()

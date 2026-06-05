# CUI // SP-CTI
"""Migration 157 — Create sg_osint_normalized table.

Stores OSINT records normalized from sg_raw_signals into a unified schema
with structured timestamp, source, lat/lon, and classified event_type.
"""
from tools.db.storage import get_connection, is_pg


def _table_exists(conn, table: str) -> bool:
    """True if ``table`` exists (backend-agnostic). ``sg_raw_signals`` is created
    at app runtime by the Strategos canvas, so a migrate-only fresh DB — e.g. the
    CI E2E PostgreSQL job's ``migrate.py --up`` — legitimately lacks it. On PG the
    FK ``REFERENCES sg_raw_signals(id)`` would otherwise abort the chain; skip
    rather than fail, matching migrations 020/040/041."""
    if getattr(conn, "_backend", "sqlite") == "postgresql":
        row = conn.execute(
            "SELECT 1 FROM information_schema.tables "
            "WHERE table_schema='public' AND table_name=?",
            (table,),
        ).fetchone()
        return row is not None
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone()
    return row is not None


def up(conn=None) -> None:
    conn = get_connection()

    if not _table_exists(conn, "sg_raw_signals"):
        print("Migration 157 up: sg_raw_signals absent — skipping (created at runtime by Strategos canvas).")
        conn.close()
        return

    if is_pg():
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS sg_osint_normalized (
                id            SERIAL PRIMARY KEY,
                raw_signal_id INTEGER REFERENCES sg_raw_signals(id) ON DELETE SET NULL,
                timestamp     TIMESTAMPTZ NOT NULL,
                source        TEXT NOT NULL DEFAULT '',
                latitude      DOUBLE PRECISION,
                longitude     DOUBLE PRECISION,
                event_type    TEXT NOT NULL DEFAULT 'other',
                confidence    REAL NOT NULL DEFAULT 0.0,
                geo_hint      TEXT,
                title         TEXT,
                created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )
    else:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS sg_osint_normalized (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                raw_signal_id INTEGER,
                timestamp     TEXT NOT NULL,
                source        TEXT NOT NULL DEFAULT '',
                latitude      REAL,
                longitude     REAL,
                event_type    TEXT NOT NULL DEFAULT 'other',
                confidence    REAL NOT NULL DEFAULT 0.0,
                geo_hint      TEXT,
                title         TEXT,
                created_at    TEXT NOT NULL
            )
            """
        )

    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_sg_osint_norm_ts ON sg_osint_normalized(timestamp)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_sg_osint_norm_type ON sg_osint_normalized(event_type)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_sg_osint_norm_raw ON sg_osint_normalized(raw_signal_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_sg_osint_norm_source ON sg_osint_normalized(source)"
    )

    conn.commit()
    conn.close()


if __name__ == "__main__":
    up()
    print("Migration 157 applied.")

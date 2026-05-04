# CUI // SP-CTI
"""Migration 057 — Add `processed` flag to sg_raw_signals.

Marks whether the raw signal has been picked up by downstream enrichment
(e.g. SIO lens, KG ingestion). Default 0 (false) preserves existing rows.
"""
from tools.db.storage import get_connection


def up(conn=None) -> None:
    conn = get_connection()
    try:
        conn.execute(
            "ALTER TABLE sg_raw_signals ADD COLUMN processed INTEGER NOT NULL DEFAULT 0"
        )
    except Exception:
        # Column already exists (idempotent)
        pass
    try:
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_sg_raw_signals_processed "
            "ON sg_raw_signals(processed)"
        )
    except Exception:
        pass
    conn.commit()
    conn.close()


if __name__ == "__main__":
    up()
    print("Migration 057 applied.")

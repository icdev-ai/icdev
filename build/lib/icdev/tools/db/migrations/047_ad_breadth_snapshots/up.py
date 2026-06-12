# CUI // SP-CTI
"""Migration 047 — Market breadth snapshots table.

Adds:
  ad_breadth_snapshots  TABLE — periodic 200-EMA + 52W breadth readings
"""
from tools.db.storage import get_connection

MIGRATION_ID = "047"
MIGRATION_NAME = "ad_breadth_snapshots"
DESCRIPTION = "Add ad_breadth_snapshots table for 200-EMA and 52W high/low breadth metrics"


def up(conn=None) -> None:
    conn = get_connection()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS ad_breadth_snapshots (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            pct_above_200ema  REAL    NOT NULL,
            net_hi_lo_ratio   REAL    NOT NULL,
            signal            TEXT    NOT NULL,
            above_count       INTEGER,
            below_count       INTEGER,
            near_high_count   INTEGER,
            near_low_count    INTEGER,
            universe_count    INTEGER,
            sector_json       TEXT,
            ticker_json       TEXT,
            created_at        TEXT    DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
        )
    """)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_breadth_snapshots_created ON ad_breadth_snapshots(created_at DESC)"
    )
    conn.commit()
    conn.close()


if __name__ == "__main__":
    up()
    print("Migration 047 applied.")

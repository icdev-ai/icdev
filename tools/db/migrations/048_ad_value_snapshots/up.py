# CUI // SP-CTI
"""Migration 048 — Fear & Greed and Buffett Indicator snapshot tables.

Adds:
  ad_fear_greed_snapshots  TABLE — periodic 7-component Fear & Greed index readings
  ad_buffett_snapshots     TABLE — periodic Wilshire-5000/GDP Buffett Indicator readings
"""
from tools.db.storage import get_connection

MIGRATION_ID = "048"
MIGRATION_NAME = "ad_value_snapshots"
DESCRIPTION = "Add ad_fear_greed_snapshots and ad_buffett_snapshots tables"


def up(conn=None) -> None:
    conn = get_connection()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS ad_fear_greed_snapshots (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            composite_score   REAL    NOT NULL,
            label             TEXT    NOT NULL,
            components_json   TEXT,
            entry_exit_signal TEXT,
            created_at        TEXT    DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
        )
    """)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_fg_snapshots_created ON ad_fear_greed_snapshots(created_at DESC)"
    )
    conn.execute("""
        CREATE TABLE IF NOT EXISTS ad_buffett_snapshots (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            ratio_pct     REAL    NOT NULL,
            wilshire_trn  REAL,
            gdp_trn       REAL,
            signal        TEXT    NOT NULL,
            created_at    TEXT    DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
        )
    """)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_buffett_snapshots_created ON ad_buffett_snapshots(created_at DESC)"
    )
    conn.commit()
    conn.close()


if __name__ == "__main__":
    up()
    print("Migration 048 applied.")

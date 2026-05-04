# CUI // SP-CTI
"""Migration 054 — Add buyback columns to ad_fundamental_metrics and create ad_buyback_announcements.

ad_fundamental_metrics already exists. This migration adds four columns needed
by the buyback signal engine, then creates the ad_buyback_announcements table.

Added columns on ad_fundamental_metrics
----------------------------------------
buyback_yield       REAL    — trailing buyback spend / market cap (annualised)
buyback_intensity   REAL    — buyback spend / operating cash flow
window_open         INTEGER — 1 = window open, 0 = blackout  (DEFAULT 1)
days_to_blackout    INTEGER — calendar days until next expected blackout start

New table: ad_buyback_announcements
-------------------------------------
id                  TEXT PRIMARY KEY (UUID)
ticker              TEXT NOT NULL
announced_at        TEXT NOT NULL  — ISO-8601 timestamp of press release / 8-K
authorized_amount   REAL           — total programme authorization in USD
authorized_currency TEXT DEFAULT 'USD'
source              TEXT DEFAULT '8K'  — '8K' or 'manual'
created_at          TEXT DEFAULT (datetime('now'))
"""
from tools.db.storage import get_connection, is_pg

_NEW_FM_COLUMNS = [
    ("buyback_yield",     "REAL"),
    ("buyback_intensity", "REAL"),
    ("window_open",       "INTEGER DEFAULT 1"),
    ("days_to_blackout",  "INTEGER"),
]

_CREATE_ANNOUNCEMENTS = """
CREATE TABLE IF NOT EXISTS ad_buyback_announcements (
    id                  TEXT PRIMARY KEY,
    ticker              TEXT NOT NULL,
    announced_at        TEXT NOT NULL,
    authorized_amount   REAL,
    authorized_currency TEXT DEFAULT 'USD',
    source              TEXT DEFAULT '8K',
    created_at          TEXT DEFAULT (datetime('now'))
);
"""

_INDEXES = [
    ("idx_ad_ba_ticker",        "CREATE INDEX IF NOT EXISTS idx_ad_ba_ticker ON ad_buyback_announcements(ticker)"),
    ("idx_ad_ba_announced_at",  "CREATE INDEX IF NOT EXISTS idx_ad_ba_announced_at ON ad_buyback_announcements(announced_at)"),
]


def up(conn=None) -> None:
    conn = get_connection()

    for col, col_type in _NEW_FM_COLUMNS:
        try:
            if is_pg():
                conn.execute(
                    f"ALTER TABLE ad_fundamental_metrics ADD COLUMN IF NOT EXISTS {col} {col_type}"
                )
            else:
                conn.execute(
                    f"ALTER TABLE ad_fundamental_metrics ADD COLUMN {col} {col_type}"
                )
        except Exception:
            pass  # column already present

    try:
        conn.execute(_CREATE_ANNOUNCEMENTS)
    except Exception:
        pass  # table already exists

    for _name, stmt in _INDEXES:
        try:
            conn.execute(stmt)
        except Exception:
            pass

    conn.commit()
    conn.close()


if __name__ == "__main__":
    up()
    print("Migration 054 applied.")

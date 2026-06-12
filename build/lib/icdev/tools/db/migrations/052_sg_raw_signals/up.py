# CUI // SP-CTI
"""Migration 052 — Create sg_raw_signals and sg_raw_signals_audit tables.

sg_raw_signals: deduplicated OSINT signal store populated by osint_harvester
  across all three tiers (INTERNET, GITLAB, FILE_INBOX).

sg_raw_signals_audit: append-only run log recording tier used, counts,
  and TIER_NONE diagnostic rows per NIST AU requirements.
"""
from tools.db.storage import get_connection, is_pg


def up(conn=None) -> None:
    conn = get_connection()

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS sg_raw_signals (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            url_hash    TEXT NOT NULL UNIQUE,
            title       TEXT NOT NULL,
            body        TEXT,
            source      TEXT,
            signal_date TEXT,
            geo_hint    TEXT,
            tier_used   TEXT,
            created_at  TEXT NOT NULL
        )
        """
        if not is_pg()
        else """
        CREATE TABLE IF NOT EXISTS sg_raw_signals (
            id          SERIAL PRIMARY KEY,
            url_hash    TEXT NOT NULL UNIQUE,
            title       TEXT NOT NULL,
            body        TEXT,
            source      TEXT,
            signal_date TEXT,
            geo_hint    TEXT,
            tier_used   TEXT,
            created_at  TEXT NOT NULL
        )
        """
    )

    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_sg_raw_signals_date ON sg_raw_signals(signal_date)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_sg_raw_signals_tier ON sg_raw_signals(tier_used)"
    )

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS sg_raw_signals_audit (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            run_at            TEXT NOT NULL,
            tier              TEXT NOT NULL,
            signals_harvested INTEGER NOT NULL DEFAULT 0,
            duplicates_skipped INTEGER NOT NULL DEFAULT 0,
            errors            INTEGER NOT NULL DEFAULT 0,
            reason            TEXT
        )
        """
        if not is_pg()
        else """
        CREATE TABLE IF NOT EXISTS sg_raw_signals_audit (
            id                SERIAL PRIMARY KEY,
            run_at            TEXT NOT NULL,
            tier              TEXT NOT NULL,
            signals_harvested INTEGER NOT NULL DEFAULT 0,
            duplicates_skipped INTEGER NOT NULL DEFAULT 0,
            errors            INTEGER NOT NULL DEFAULT 0,
            reason            TEXT
        )
        """
    )

    conn.commit()
    conn.close()


if __name__ == "__main__":
    up()
    print("Migration 052 applied.")

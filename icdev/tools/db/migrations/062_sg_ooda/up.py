#!/usr/bin/env python3
# CUI // SP-CTI
"""Migration 062 — sg_ooda_events, sg_coa_entries, sg_nash_scenarios."""

from tools.db.storage import get_connection

_STATEMENTS = [
    # OODA event log — one row per phase observation per domain per side
    """CREATE TABLE IF NOT EXISTS sg_ooda_events (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        wargame_id   TEXT,
        side         TEXT NOT NULL CHECK(side IN ('blue','red')),
        domain       TEXT NOT NULL CHECK(domain IN ('land','air','sea','cyber','ew','space')),
        phase        TEXT NOT NULL CHECK(phase IN ('observe','orient','decide','act')),
        latency_s    REAL NOT NULL DEFAULT 0,
        event_ts     TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        notes        TEXT,
        created_at   TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )""",

    "CREATE INDEX IF NOT EXISTS idx_ooda_wargame ON sg_ooda_events(wargame_id)",
    "CREATE INDEX IF NOT EXISTS idx_ooda_side_domain ON sg_ooda_events(side, domain)",

    # COA entries — one row per course of action
    """CREATE TABLE IF NOT EXISTS sg_coa_entries (
        id                  TEXT PRIMARY KEY,
        wargame_id          TEXT,
        side                TEXT NOT NULL CHECK(side IN ('blue','red')),
        name                TEXT NOT NULL,
        description         TEXT,
        speed               REAL NOT NULL DEFAULT 0.5,
        surprise            REAL NOT NULL DEFAULT 0.5,
        mass                REAL NOT NULL DEFAULT 0.5,
        economy_of_force    REAL NOT NULL DEFAULT 0.5,
        maneuver            REAL NOT NULL DEFAULT 0.5,
        sustainability      REAL NOT NULL DEFAULT 0.5,
        composite_score     REAL,
        selected            INTEGER NOT NULL DEFAULT 0,
        created_at          TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )""",

    "CREATE INDEX IF NOT EXISTS idx_coa_wargame ON sg_coa_entries(wargame_id)",

    # Nash payoff matrices — one row per scenario (stores JSON-serialized matrices)
    """CREATE TABLE IF NOT EXISTS sg_nash_scenarios (
        id              TEXT PRIMARY KEY,
        wargame_id      TEXT,
        name            TEXT NOT NULL,
        blue_strategies TEXT NOT NULL,
        red_strategies  TEXT NOT NULL,
        payoff_blue     TEXT NOT NULL,
        payoff_red      TEXT NOT NULL,
        pure_equilibria TEXT,
        mixed_equilibrium TEXT,
        created_at      TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )""",

    "CREATE INDEX IF NOT EXISTS idx_nash_wargame ON sg_nash_scenarios(wargame_id)",
]


def up(conn=None):
    conn = get_connection()
    try:
        for stmt in _STATEMENTS:
            conn.execute(stmt)
        conn.commit()
        print("[062_sg_ooda] migration up complete")
    finally:
        conn.close()


if __name__ == "__main__":
    up()

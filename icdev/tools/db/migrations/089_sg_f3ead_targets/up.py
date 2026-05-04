#!/usr/bin/env python3
# CUI // SP-CTI
"""Migration 089 — F3EAD targeting workflow tables.

sg_f3ead_targets  — target cards tracked through the F3EAD cycle
sg_target_events  — phase transition log (append-only, NIST AU)
"""
from tools.db.storage import get_connection

_F3EAD_PHASES = "('find','fix','finish','exploit','analyze','disseminate','complete')"


def up(conn=None) -> None:
    conn = get_connection()
    try:
        conn.execute(f"""
            CREATE TABLE IF NOT EXISTS sg_f3ead_targets (
                id              TEXT PRIMARY KEY,
                target_name     TEXT NOT NULL,
                target_type     TEXT DEFAULT 'node',
                description     TEXT,
                phase           TEXT DEFAULT 'find'
                                    CHECK(phase IN {_F3EAD_PHASES}),
                responsible_element TEXT,
                priority        INTEGER DEFAULT 3
                                    CHECK(priority BETWEEN 1 AND 5),
                theater         TEXT DEFAULT 'unspecified',
                geo_hint        TEXT,
                evidence_notes  TEXT,
                phase_entered_at TEXT,
                created_at      TEXT NOT NULL,
                updated_at      TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS sg_target_events (
                id          TEXT PRIMARY KEY,
                target_id   TEXT NOT NULL,
                from_phase  TEXT,
                to_phase    TEXT NOT NULL,
                actor       TEXT DEFAULT 'analyst',
                notes       TEXT,
                created_at  TEXT NOT NULL
            )
        """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_sg_f3ead_targets_phase "
            "ON sg_f3ead_targets(phase)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_sg_f3ead_targets_theater "
            "ON sg_f3ead_targets(theater)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_sg_target_events_target "
            "ON sg_target_events(target_id)"
        )
        conn.commit()
        print("Migration 089 up: sg_f3ead_targets + sg_target_events created.")
    finally:
        conn.close()


if __name__ == "__main__":
    up()

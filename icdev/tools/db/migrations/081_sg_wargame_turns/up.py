"""
Migration 081 — sg_wargame_turns table + blue_strength/red_strength on sg_wargames.

Creates:
  sg_wargame_turns(id, wargame_id, turn_number, blue_losses, red_losses,
                   blue_remaining, red_remaining, tempo_delta, notes, created_at)

Adds (if not present):
  sg_wargames.blue_strength INTEGER DEFAULT 0
  sg_wargames.red_strength  INTEGER DEFAULT 0
"""
from tools.db.storage import get_connection

_CREATE_TURNS = """
CREATE TABLE IF NOT EXISTS sg_wargame_turns (
    id              TEXT PRIMARY KEY,
    wargame_id      TEXT NOT NULL,
    turn_number     INTEGER NOT NULL,
    blue_losses     INTEGER NOT NULL DEFAULT 0,
    red_losses      INTEGER NOT NULL DEFAULT 0,
    blue_remaining  INTEGER NOT NULL DEFAULT 0,
    red_remaining   INTEGER NOT NULL DEFAULT 0,
    tempo_delta     REAL NOT NULL DEFAULT 0.0,
    notes           TEXT,
    created_at      TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (wargame_id) REFERENCES sg_wargames(id)
)
"""

_CREATE_TURNS_IDX = (
    "CREATE INDEX IF NOT EXISTS idx_sg_wgt_wargame_id   ON sg_wargame_turns(wargame_id)",
    "CREATE INDEX IF NOT EXISTS idx_sg_wgt_turn_number  ON sg_wargame_turns(turn_number)",
)


def _column_exists(conn, table: str, column: str) -> bool:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return any(row[1] == column for row in rows)


def up(conn=None) -> None:
    conn = get_connection()
    try:
        conn.execute(_CREATE_TURNS)

        for idx in _CREATE_TURNS_IDX:
            try:
                conn.execute(idx)
            except Exception:
                pass

        if not _column_exists(conn, "sg_wargames", "blue_strength"):
            conn.execute(
                "ALTER TABLE sg_wargames ADD COLUMN blue_strength INTEGER NOT NULL DEFAULT 0"
            )
        if not _column_exists(conn, "sg_wargames", "red_strength"):
            conn.execute(
                "ALTER TABLE sg_wargames ADD COLUMN red_strength INTEGER NOT NULL DEFAULT 0"
            )

        conn.commit()
        print(
            "Migration 081 up: sg_wargame_turns created; "
            "blue_strength/red_strength added to sg_wargames."
        )
    finally:
        conn.close()


if __name__ == "__main__":
    up()

#!/usr/bin/env python3
# CUI // SP-CTI
"""Migration 039 down: Drop canvas_events table."""
from __future__ import annotations

from tools.db.storage import get_connection


def down() -> None:
    conn = get_connection()
    try:
        conn.execute("DROP TABLE IF EXISTS canvas_events")
        conn.commit()
        print("[039_canvas_events] down: canvas_events dropped")
    finally:
        conn.close()


if __name__ == "__main__":
    down()

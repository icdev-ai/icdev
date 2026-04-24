#!/usr/bin/env python3
# CUI // SP-CTI
"""Migration 037 rollback: drop nc_simulation tables in dependency order."""
from __future__ import annotations

from tools.db.storage import get_connection


def down() -> None:
    conn = get_connection()
    try:
        conn.execute("DROP TABLE IF EXISTS nc_simulation_artifacts")
        conn.execute("DROP TABLE IF EXISTS nc_simulation_runs")
        conn.execute("DROP TABLE IF EXISTS nc_simulation_sessions")
        conn.commit()
        print("[037_nc_simulation_tables] down: nc_simulation tables dropped")
    finally:
        conn.close()


if __name__ == "__main__":
    down()

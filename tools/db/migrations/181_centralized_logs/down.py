#!/usr/bin/env python3
# CUI // SP-CTI
"""Migration 181 rollback: drop centralized_logs (eqo-log-01)."""


def down(conn):
    """Drop centralized_logs (reverses up.py)."""
    conn.execute("DROP TABLE IF EXISTS centralized_logs")
    try:
        conn.commit()
    except Exception:
        pass
    print("[migration 181] centralized_logs table dropped")

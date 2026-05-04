#!/usr/bin/env python3
# CUI // SP-CTI
"""Migration 083 — Cyber extension columns.

Changes
-------
sg_cve_feed:
  is_kev         INTEGER DEFAULT 0  -- 1 if in CISA KEV catalog
  kev_due_date   TEXT               -- CISA-mandated remediation deadline
  epss_score     REAL               -- EPSS probability of exploitation (0.0–1.0)

sg_supply_nodes:
  shodan_json      TEXT  -- raw Shodan host JSON for this node
  shodan_updated_at TEXT -- ISO-8601 timestamp of last Shodan sync

sg_attack_techniques (new table):
  MITRE ATT&CK technique catalog for cyber threat correlation.
"""
from tools.db.storage import get_connection, is_pg

# --- Column additions (idempotent) ---

_CVE_COLUMNS = [
    ("is_kev",       "INTEGER DEFAULT 0"),
    ("kev_due_date", "TEXT"),
    ("epss_score",   "REAL"),
]

_SUPPLY_COLUMNS = [
    ("shodan_json",       "TEXT"),
    ("shodan_updated_at", "TEXT"),
]

# --- New table + indexes ---

_ATTACK_TECHNIQUES_DDL = """
CREATE TABLE IF NOT EXISTS sg_attack_techniques (
    id               TEXT PRIMARY KEY,
    tactic_id        TEXT NOT NULL,
    tactic_name      TEXT NOT NULL,
    technique_id     TEXT NOT NULL,
    technique_name   TEXT NOT NULL,
    sub_technique_id TEXT,
    platforms        TEXT,
    permissions      TEXT,
    detection        TEXT,
    url              TEXT,
    stix_id          TEXT UNIQUE,
    updated_at       TEXT
)
"""

_ATTACK_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_sg_at_tactic    ON sg_attack_techniques (tactic_id)",
    "CREATE INDEX IF NOT EXISTS idx_sg_at_technique ON sg_attack_techniques (technique_id)",
    "CREATE INDEX IF NOT EXISTS idx_sg_cve_is_kev   ON sg_cve_feed (is_kev)",
]


def _column_exists(conn, table: str, column: str) -> bool:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return any(row[1] == column for row in rows)


def _add_columns(conn, table: str, columns: list) -> None:
    for col, col_def in columns:
        try:
            if is_pg():
                conn.execute(
                    f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {col} {col_def}"
                )
            else:
                if not _column_exists(conn, table, col):
                    conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {col_def}")
        except Exception:
            pass  # column already present on older SQLite builds


def up(conn=None) -> None:
    _conn = conn or get_connection()
    try:
        _add_columns(_conn, "sg_cve_feed", _CVE_COLUMNS)
        _add_columns(_conn, "sg_supply_nodes", _SUPPLY_COLUMNS)

        _conn.execute(_ATTACK_TECHNIQUES_DDL)

        for idx_sql in _ATTACK_INDEXES:
            try:
                _conn.execute(idx_sql)
            except Exception:
                pass

        _conn.commit()
        print("[083_cyber_ext_columns] migration up complete")
    finally:
        if conn is None:
            _conn.close()


if __name__ == "__main__":
    up()

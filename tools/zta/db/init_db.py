"""Initialize ZTA LAC Simulator database tables."""

from __future__ import annotations
import logging
import os

log = logging.getLogger(__name__)

_BACKEND = os.environ.get("ICDEV_STORAGE_BACKEND", "sqlite").lower()

_SCHEMA_PG = """
CREATE TABLE IF NOT EXISTS zta_lac_audit (
    id SERIAL PRIMARY KEY,
    scenario_name TEXT NOT NULL,
    decision TEXT NOT NULL CHECK (decision IN ('PERMIT', 'DENY')),
    deny_reasons TEXT DEFAULT '[]',
    principal_citizenship TEXT,
    principal_clearance TEXT,
    principal_cois TEXT DEFAULT '[]',
    resource_id TEXT,
    resource_is_eci INTEGER DEFAULT 0,
    action TEXT DEFAULT 'read',
    environment TEXT DEFAULT '{}',
    break_glass_activated INTEGER DEFAULT 0,
    audit_json TEXT DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT (NOW()::text)
);

CREATE TABLE IF NOT EXISTS zta_twin_phases (
    id SERIAL PRIMARY KEY,
    env_id TEXT NOT NULL,
    phase TEXT NOT NULL,
    phase_index INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    notes TEXT,
    advanced_at TEXT
);
"""

_SCHEMA_SQLITE = """
CREATE TABLE IF NOT EXISTS zta_lac_audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    scenario_name TEXT NOT NULL,
    decision TEXT NOT NULL CHECK (decision IN ('PERMIT', 'DENY')),
    deny_reasons TEXT DEFAULT '[]',
    principal_citizenship TEXT,
    principal_clearance TEXT,
    principal_cois TEXT DEFAULT '[]',
    resource_id TEXT,
    resource_is_eci INTEGER DEFAULT 0,
    action TEXT DEFAULT 'read',
    environment TEXT DEFAULT '{}',
    break_glass_activated INTEGER DEFAULT 0,
    audit_json TEXT DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS zta_twin_phases (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    env_id TEXT NOT NULL,
    phase TEXT NOT NULL,
    phase_index INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    notes TEXT,
    advanced_at TEXT
);
"""


def init_db(conn) -> None:
    schema = _SCHEMA_PG if _BACKEND == "postgresql" else _SCHEMA_SQLITE
    try:
        for stmt in schema.strip().split(";"):
            s = stmt.strip()
            if s:
                conn.execute(s)
        conn.commit()
        log.debug("ZTA LAC audit tables initialized")
    except Exception as exc:
        log.warning("ZTA DB init warning: %s", exc)

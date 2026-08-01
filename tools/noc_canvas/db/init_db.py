# CUI // SP-CTI
"""NOC Operations Canvas DB initializer.

Creates NOCC tables for alarm management, incident tracking,
RFC/MOP lifecycle, maintenance windows, and SLA records.

Default backend: PostgreSQL. Set NOCC_STORAGE_BACKEND=sqlite to override.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

_ICDEV_ROOT = Path(__file__).resolve().parents[3]
_SQLITE_PATH = _ICDEV_ROOT / "data" / "noc_canvas.db"

_NOCC_BACKEND = os.environ.get(
    "NOCC_STORAGE_BACKEND",
    os.environ.get("ICDEV_CANVAS_STORAGE_BACKEND", "postgresql"),
).lower()


def get_connection():
    """Return a DB connection — PostgreSQL (default) or SQLite fallback.

    Uses get_canvas_connection() to disable RLS; canvas tables lack
    classification/tenant_id columns required by the global predicate.
    """
    if _NOCC_BACKEND != "sqlite":
        try:
            from tools.db.storage import get_canvas_connection
            return get_canvas_connection("NOCC_PG_DATABASE")
        except Exception as exc:
            print(
                f"[nocc-db] PostgreSQL unavailable ({exc}), falling back to SQLite",
                file=sys.stderr,
            )
    import sqlite3 as _sqlite3
    conn = _sqlite3.connect(str(_SQLITE_PATH))
    conn.row_factory = _sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


SCHEMA_PG = """
-- ── NOC: Incidents ───────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS noc_incidents (
    id               TEXT PRIMARY KEY,
    incident_number  TEXT NOT NULL UNIQUE,
    title            TEXT NOT NULL,
    severity         TEXT NOT NULL DEFAULT 'p3'
                         CHECK(severity IN ('p1','p2','p3','p4')),
    status           TEXT NOT NULL DEFAULT 'open'
                         CHECK(status IN ('open','acknowledged','investigating','resolved','closed')),
    affected_circuit TEXT DEFAULT '',
    affected_site    TEXT DEFAULT '',
    affected_carrier TEXT DEFAULT '',
    root_cause       TEXT DEFAULT '',
    resolution       TEXT DEFAULT '',
    sla_breach       BOOLEAN DEFAULT FALSE,
    mttr_minutes     INTEGER,
    opened_by        TEXT DEFAULT '',
    assigned_to      TEXT DEFAULT '',
    classification   TEXT DEFAULT 'CUI',
    tags_json        JSONB DEFAULT '[]',
    created_at       TIMESTAMPTZ DEFAULT NOW(),
    resolved_at      TIMESTAMPTZ,
    updated_at       TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_noc_inc_severity ON noc_incidents(severity);
CREATE INDEX IF NOT EXISTS idx_noc_inc_status   ON noc_incidents(status);
CREATE INDEX IF NOT EXISTS idx_noc_inc_created  ON noc_incidents(created_at);

-- ── NOC: Alarms ──────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS noc_alarms (
    id                     TEXT PRIMARY KEY,
    alarm_source           TEXT NOT NULL DEFAULT 'custom'
                               CHECK(alarm_source IN ('solarwinds','librenms','snmp-trap','syslog','nagios','zabbix','prtg','custom')),
    source_alarm_id        TEXT DEFAULT '',
    severity               TEXT NOT NULL DEFAULT 'warning'
                               CHECK(severity IN ('critical','major','minor','warning','info')),
    alarm_type             TEXT NOT NULL DEFAULT 'interface'
                               CHECK(alarm_type IN ('interface','bgp','circuit','power','optical','cpu','memory','temperature','security','other')),
    device_name            TEXT DEFAULT '',
    device_ip              TEXT DEFAULT '',
    circuit_id             TEXT DEFAULT '',
    carrier                TEXT DEFAULT '',
    description            TEXT NOT NULL DEFAULT '',
    raw_payload            JSONB DEFAULT '{}',
    correlated_incident_id TEXT REFERENCES noc_incidents(id),
    suppressed             BOOLEAN DEFAULT FALSE,
    acknowledged           BOOLEAN DEFAULT FALSE,
    acknowledged_by        TEXT DEFAULT '',
    acknowledged_at        TIMESTAMPTZ,
    cleared                BOOLEAN DEFAULT FALSE,
    cleared_at             TIMESTAMPTZ,
    classification         TEXT DEFAULT 'CUI',
    first_seen             TIMESTAMPTZ DEFAULT NOW(),
    last_seen              TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_noc_alarm_severity ON noc_alarms(severity);
CREATE INDEX IF NOT EXISTS idx_noc_alarm_cleared  ON noc_alarms(cleared);
CREATE INDEX IF NOT EXISTS idx_noc_alarm_source   ON noc_alarms(alarm_source);
CREATE INDEX IF NOT EXISTS idx_noc_alarm_device   ON noc_alarms(device_name);

-- ── NOC: RFCs (Change Requests) ──────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS noc_rfcs (
    id                TEXT PRIMARY KEY,
    rfc_number        TEXT NOT NULL UNIQUE,
    title             TEXT NOT NULL,
    change_type       TEXT NOT NULL DEFAULT 'standard'
                          CHECK(change_type IN ('emergency','standard','normal')),
    status            TEXT NOT NULL DEFAULT 'draft'
                          CHECK(status IN ('draft','submitted','approved','executing','completed','rejected')),
    risk_level        TEXT NOT NULL DEFAULT 'medium'
                          CHECK(risk_level IN ('low','medium','high')),
    rollback_plan     TEXT DEFAULT '',
    scheduled_start   TIMESTAMPTZ,
    scheduled_end     TIMESTAMPTZ,
    actual_start      TIMESTAMPTZ,
    actual_end        TIMESTAMPTZ,
    mop_id            TEXT,
    affected_circuits JSONB DEFAULT '[]',
    affected_sites    JSONB DEFAULT '[]',
    change_owner      TEXT DEFAULT '',
    approver          TEXT DEFAULT '',
    classification    TEXT DEFAULT 'CUI',
    created_at        TIMESTAMPTZ DEFAULT NOW(),
    updated_at        TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_noc_rfc_status ON noc_rfcs(status);
CREATE INDEX IF NOT EXISTS idx_noc_rfc_type   ON noc_rfcs(change_type);

-- ── NOC: MOPs (Methods of Procedure) ─────────────────────────────────────────

CREATE TABLE IF NOT EXISTS noc_mops (
    id             TEXT PRIMARY KEY,
    mop_number     TEXT NOT NULL UNIQUE,
    title          TEXT NOT NULL,
    rfc_id         TEXT REFERENCES noc_rfcs(id),
    steps_json     JSONB NOT NULL DEFAULT '[]',
    generated_by   TEXT DEFAULT 'manual'
                       CHECK(generated_by IN ('manual','ai','ai_template')),
    ai_prompt      TEXT DEFAULT '',
    classification TEXT DEFAULT 'CUI',
    created_at     TIMESTAMPTZ DEFAULT NOW(),
    updated_at     TIMESTAMPTZ DEFAULT NOW()
);

-- ── NOC: Maintenance Windows ──────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS noc_maintenance_windows (
    id                 TEXT PRIMARY KEY,
    window_number      TEXT NOT NULL UNIQUE,
    title              TEXT NOT NULL,
    rfc_id             TEXT REFERENCES noc_rfcs(id),
    scheduled_start    TIMESTAMPTZ NOT NULL,
    scheduled_end      TIMESTAMPTZ NOT NULL,
    actual_start       TIMESTAMPTZ,
    actual_end         TIMESTAMPTZ,
    status             TEXT NOT NULL DEFAULT 'scheduled'
                           CHECK(status IN ('scheduled','in-progress','completed','cancelled')),
    impact_scope       TEXT DEFAULT 'single-circuit'
                           CHECK(impact_scope IN ('single-circuit','multi-circuit','site','region','global')),
    notification_sent  BOOLEAN DEFAULT FALSE,
    affected_customers JSONB DEFAULT '[]',
    affected_circuits  JSONB DEFAULT '[]',
    classification     TEXT DEFAULT 'CUI',
    created_at         TIMESTAMPTZ DEFAULT NOW(),
    updated_at         TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_noc_mw_status ON noc_maintenance_windows(status);
CREATE INDEX IF NOT EXISTS idx_noc_mw_start  ON noc_maintenance_windows(scheduled_start);

-- ── NOC: SLA Records ─────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS noc_sla_records (
    id                 TEXT PRIMARY KEY,
    circuit_id         TEXT NOT NULL,
    carrier            TEXT NOT NULL,
    customer           TEXT DEFAULT '',
    sla_type           TEXT NOT NULL DEFAULT 'uptime'
                           CHECK(sla_type IN ('uptime','latency_ms','jitter_ms','packet_loss_pct','mttr_min')),
    target_value       REAL NOT NULL,
    measured_value     REAL,
    measurement_period TEXT NOT NULL DEFAULT 'monthly',
    breach             BOOLEAN DEFAULT FALSE,
    breach_minutes     INTEGER DEFAULT 0,
    credit_eligible    BOOLEAN DEFAULT FALSE,
    period_start       TIMESTAMPTZ NOT NULL,
    period_end         TIMESTAMPTZ,
    classification     TEXT DEFAULT 'CUI',
    created_at         TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_noc_sla_circuit ON noc_sla_records(circuit_id);
CREATE INDEX IF NOT EXISTS idx_noc_sla_breach  ON noc_sla_records(breach);

-- ── NOC: Audit (append-only) ─────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS noc_audit (
    id             BIGSERIAL PRIMARY KEY,
    action         TEXT NOT NULL,
    entity_type    TEXT DEFAULT '',
    entity_id      TEXT DEFAULT '',
    details        TEXT DEFAULT '',
    user_id        TEXT DEFAULT '',
    classification TEXT DEFAULT 'CUI // SP-CTI',
    ts             TIMESTAMPTZ DEFAULT NOW()
);
"""

# SQLite-compatible schema (BOOLEAN→INTEGER, JSONB→TEXT, TIMESTAMPTZ→TEXT, BIGSERIAL→INTEGER AUTOINCREMENT)
SCHEMA_SQLITE = """
CREATE TABLE IF NOT EXISTS noc_incidents (
    id               TEXT PRIMARY KEY,
    incident_number  TEXT NOT NULL UNIQUE,
    title            TEXT NOT NULL,
    severity         TEXT NOT NULL DEFAULT 'p3'
                         CHECK(severity IN ('p1','p2','p3','p4')),
    status           TEXT NOT NULL DEFAULT 'open'
                         CHECK(status IN ('open','acknowledged','investigating','resolved','closed')),
    affected_circuit TEXT DEFAULT '',
    affected_site    TEXT DEFAULT '',
    affected_carrier TEXT DEFAULT '',
    root_cause       TEXT DEFAULT '',
    resolution       TEXT DEFAULT '',
    sla_breach       INTEGER DEFAULT 0,
    mttr_minutes     INTEGER,
    opened_by        TEXT DEFAULT '',
    assigned_to      TEXT DEFAULT '',
    classification   TEXT DEFAULT 'CUI',
    tags_json        TEXT DEFAULT '[]',
    created_at       TEXT DEFAULT CURRENT_TIMESTAMP,
    resolved_at      TEXT,
    updated_at       TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_noc_inc_severity ON noc_incidents(severity);
CREATE INDEX IF NOT EXISTS idx_noc_inc_status   ON noc_incidents(status);
CREATE INDEX IF NOT EXISTS idx_noc_inc_created  ON noc_incidents(created_at);

CREATE TABLE IF NOT EXISTS noc_alarms (
    id                     TEXT PRIMARY KEY,
    alarm_source           TEXT NOT NULL DEFAULT 'custom',
    source_alarm_id        TEXT DEFAULT '',
    severity               TEXT NOT NULL DEFAULT 'warning',
    alarm_type             TEXT NOT NULL DEFAULT 'interface',
    device_name            TEXT DEFAULT '',
    device_ip              TEXT DEFAULT '',
    circuit_id             TEXT DEFAULT '',
    carrier                TEXT DEFAULT '',
    description            TEXT NOT NULL DEFAULT '',
    raw_payload            TEXT DEFAULT '{}',
    correlated_incident_id TEXT REFERENCES noc_incidents(id),
    suppressed             INTEGER DEFAULT 0,
    acknowledged           INTEGER DEFAULT 0,
    acknowledged_by        TEXT DEFAULT '',
    acknowledged_at        TEXT,
    cleared                INTEGER DEFAULT 0,
    cleared_at             TEXT,
    classification         TEXT DEFAULT 'CUI',
    first_seen             TEXT DEFAULT CURRENT_TIMESTAMP,
    last_seen              TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_noc_alarm_severity ON noc_alarms(severity);
CREATE INDEX IF NOT EXISTS idx_noc_alarm_cleared  ON noc_alarms(cleared);
CREATE INDEX IF NOT EXISTS idx_noc_alarm_source   ON noc_alarms(alarm_source);
CREATE INDEX IF NOT EXISTS idx_noc_alarm_device   ON noc_alarms(device_name);

CREATE TABLE IF NOT EXISTS noc_rfcs (
    id                TEXT PRIMARY KEY,
    rfc_number        TEXT NOT NULL UNIQUE,
    title             TEXT NOT NULL,
    change_type       TEXT NOT NULL DEFAULT 'standard',
    status            TEXT NOT NULL DEFAULT 'draft',
    risk_level        TEXT NOT NULL DEFAULT 'medium',
    rollback_plan     TEXT DEFAULT '',
    scheduled_start   TEXT,
    scheduled_end     TEXT,
    actual_start      TEXT,
    actual_end        TEXT,
    mop_id            TEXT,
    affected_circuits TEXT DEFAULT '[]',
    affected_sites    TEXT DEFAULT '[]',
    change_owner      TEXT DEFAULT '',
    approver          TEXT DEFAULT '',
    classification    TEXT DEFAULT 'CUI',
    created_at        TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at        TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_noc_rfc_status ON noc_rfcs(status);
CREATE INDEX IF NOT EXISTS idx_noc_rfc_type   ON noc_rfcs(change_type);

CREATE TABLE IF NOT EXISTS noc_mops (
    id             TEXT PRIMARY KEY,
    mop_number     TEXT NOT NULL UNIQUE,
    title          TEXT NOT NULL,
    rfc_id         TEXT REFERENCES noc_rfcs(id),
    steps_json     TEXT NOT NULL DEFAULT '[]',
    generated_by   TEXT DEFAULT 'manual',
    ai_prompt      TEXT DEFAULT '',
    classification TEXT DEFAULT 'CUI',
    created_at     TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at     TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS noc_maintenance_windows (
    id                 TEXT PRIMARY KEY,
    window_number      TEXT NOT NULL UNIQUE,
    title              TEXT NOT NULL,
    rfc_id             TEXT REFERENCES noc_rfcs(id),
    scheduled_start    TEXT NOT NULL,
    scheduled_end      TEXT NOT NULL,
    actual_start       TEXT,
    actual_end         TEXT,
    status             TEXT NOT NULL DEFAULT 'scheduled',
    impact_scope       TEXT DEFAULT 'single-circuit',
    notification_sent  INTEGER DEFAULT 0,
    affected_customers TEXT DEFAULT '[]',
    affected_circuits  TEXT DEFAULT '[]',
    classification     TEXT DEFAULT 'CUI',
    created_at         TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at         TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_noc_mw_status ON noc_maintenance_windows(status);
CREATE INDEX IF NOT EXISTS idx_noc_mw_start  ON noc_maintenance_windows(scheduled_start);

CREATE TABLE IF NOT EXISTS noc_sla_records (
    id                 TEXT PRIMARY KEY,
    circuit_id         TEXT NOT NULL,
    carrier            TEXT NOT NULL,
    customer           TEXT DEFAULT '',
    sla_type           TEXT NOT NULL DEFAULT 'uptime',
    target_value       REAL NOT NULL,
    measured_value     REAL,
    measurement_period TEXT NOT NULL DEFAULT 'monthly',
    breach             INTEGER DEFAULT 0,
    breach_minutes     INTEGER DEFAULT 0,
    credit_eligible    INTEGER DEFAULT 0,
    period_start       TEXT NOT NULL,
    period_end         TEXT,
    classification     TEXT DEFAULT 'CUI',
    created_at         TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_noc_sla_circuit ON noc_sla_records(circuit_id);
CREATE INDEX IF NOT EXISTS idx_noc_sla_breach  ON noc_sla_records(breach);

CREATE TABLE IF NOT EXISTS noc_audit (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    action         TEXT NOT NULL,
    entity_type    TEXT DEFAULT '',
    entity_id      TEXT DEFAULT '',
    details        TEXT DEFAULT '',
    user_id        TEXT DEFAULT '',
    classification TEXT DEFAULT 'CUI // SP-CTI',
    ts             TEXT DEFAULT CURRENT_TIMESTAMP
);
"""


def init_db(conn=None) -> None:
    """Create NOCC schema tables (idempotent)."""
    _conn = conn or get_connection()
    owned = conn is None
    try:
        schema = SCHEMA_SQLITE if _NOCC_BACKEND == "sqlite" else SCHEMA_PG
        if _NOCC_BACKEND == "sqlite":
            _conn.executescript(schema)
        else:
            cur = _conn.cursor()
            for stmt in [s.strip() for s in schema.split(";") if s.strip()]:
                cur.execute(stmt)
            _conn.commit()
        print("[nocc-db] Schema initialized OK", file=sys.stderr)
    except Exception as exc:
        print(f"[nocc-db] Schema init error: {exc}", file=sys.stderr)
        raise
    finally:
        if owned:
            _conn.close()


if __name__ == "__main__":
    init_db()
    print(f"[nocc-db] Backend: {_NOCC_BACKEND}")

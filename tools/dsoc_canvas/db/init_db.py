# CUI // SP-CTI
"""DDoS & Security Ops Canvas (DSOC) — DB initializer.

Dual-backend: PostgreSQL (default) or SQLite fallback.
DB file: data/dsoc_canvas.db  |  env: DSOC_STORAGE_BACKEND, DSOC_DB_PATH
"""
from __future__ import annotations

import os
from pathlib import Path

_ICDEV_ROOT = Path(__file__).resolve().parents[4]
DB_PATH = Path(os.environ.get("DSOC_DB_PATH", str(_ICDEV_ROOT / "data" / "dsoc_canvas.db")))

_DSOC_BACKEND = os.environ.get(
    "DSOC_STORAGE_BACKEND",
    os.environ.get("ICDEV_CANVAS_STORAGE_BACKEND", "postgresql"),
).lower()


def get_connection():
    """Return a translating StorageConnection for DSOC canvas tables.

    PG-primary. When PostgreSQL is unreachable the SQLite fallback is routed
    through the *translating* StorageConnection (not raw sqlite3) so that
    PG-native ``%s`` runtime SQL keeps working and there is no split-brain raw
    ``data/dsoc_canvas.db``. RLS is disabled (canvas tables have no
    classification/tenant_id columns).
    """
    from tools.db.storage import (
        StorageConnection,
        _get_sqlite_connection,
        get_canvas_connection,
    )
    if _DSOC_BACKEND == "postgresql":
        try:
            return get_canvas_connection("DSOC_PG_DATABASE")
        except Exception:
            pass
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    raw = _get_sqlite_connection(str(DB_PATH))
    conn = StorageConnection(raw, "sqlite")
    conn.set_security_context(None)  # rls-bypass: canvas tables have no tenant_id/classification columns, so RLS injection would raise UndefinedColumn on every query
    return conn


SCHEMA_PG = """
CREATE TABLE IF NOT EXISTS dsoc_flowspec_rules (
    id                  SERIAL PRIMARY KEY,
    rule_name           TEXT NOT NULL,
    destination_prefix  TEXT DEFAULT '',
    source_prefix       TEXT DEFAULT '',
    protocol            TEXT DEFAULT '',
    dst_port            TEXT DEFAULT '',
    src_port            TEXT DEFAULT '',
    packet_length       TEXT DEFAULT '',
    dscp                TEXT DEFAULT '',
    action              TEXT NOT NULL CHECK(action IN ('rate-limit','drop','redirect','mark','sample')),
    rate_limit_bps      INTEGER DEFAULT 0,
    redirect_vrf        TEXT DEFAULT '',
    community           TEXT DEFAULT '',
    status              TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','withdrawn','expired','draft')),
    applied_routers     TEXT DEFAULT '',
    expires_at          TEXT DEFAULT '',
    created_by          TEXT DEFAULT 'system',
    classification      TEXT DEFAULT 'CUI',
    created_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS dsoc_rtbh_entries (
    id                      SERIAL PRIMARY KEY,
    prefix                  TEXT NOT NULL,
    trigger_reason          TEXT NOT NULL CHECK(trigger_reason IN (
                                'volumetric_attack','syn_flood','udp_flood','icmp_flood',
                                'amplification','spoofed_traffic','manual','policy')),
    triggered_by            TEXT DEFAULT 'system',
    status                  TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','withdrawn','expired')),
    community               TEXT DEFAULT '',
    nexthop                 TEXT DEFAULT '192.0.2.1',
    applied_routers         TEXT DEFAULT '',
    withdrawn_at            TEXT DEFAULT '',
    auto_withdraw_minutes   INTEGER DEFAULT 60,
    classification          TEXT DEFAULT 'CUI',
    created_at              TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS dsoc_scrubbing_centers (
    id                  SERIAL PRIMARY KEY,
    name                TEXT NOT NULL UNIQUE,
    location            TEXT DEFAULT '',
    provider            TEXT DEFAULT '',
    capacity_gbps       REAL DEFAULT 0,
    current_load_gbps   REAL DEFAULT 0,
    status              TEXT NOT NULL DEFAULT 'operational' CHECK(status IN (
                            'operational','degraded','offline','maintenance')),
    upstream_links      TEXT DEFAULT '',
    anycast_prefix      TEXT DEFAULT '',
    classification      TEXT DEFAULT 'CUI',
    created_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS dsoc_threats (
    id              SERIAL PRIMARY KEY,
    source_prefix   TEXT NOT NULL,
    threat_type     TEXT NOT NULL CHECK(threat_type IN (
                        'botnet_c2','scanner','amplifier','spoofed_source',
                        'known_attacker','tor_exit','vpn_provider','other')),
    confidence_pct  REAL DEFAULT 0,
    attack_vector   TEXT DEFAULT '',
    feed_source     TEXT DEFAULT '',
    first_seen      TEXT DEFAULT '',
    last_seen       TEXT DEFAULT '',
    packets_per_sec INTEGER DEFAULT 0,
    bits_per_sec    INTEGER DEFAULT 0,
    is_active       INTEGER DEFAULT 1,
    classification  TEXT DEFAULT 'CUI',
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS dsoc_mitigations (
    id                  SERIAL PRIMARY KEY,
    mitigation_number   TEXT NOT NULL UNIQUE,
    target_prefix       TEXT NOT NULL,
    mitigation_type     TEXT NOT NULL CHECK(mitigation_type IN (
                            'rtbh','flowspec','scrubbing','acl','hybrid')),
    status              TEXT NOT NULL DEFAULT 'active' CHECK(status IN (
                            'active','standby','completed','failed')),
    attack_type         TEXT DEFAULT '',
    peak_traffic_gbps   REAL DEFAULT 0,
    scrubbing_center_id INTEGER REFERENCES dsoc_scrubbing_centers(id) ON DELETE SET NULL,
    flowspec_rule_id    INTEGER REFERENCES dsoc_flowspec_rules(id) ON DELETE SET NULL,
    rtbh_id             INTEGER REFERENCES dsoc_rtbh_entries(id) ON DELETE SET NULL,
    started_by          TEXT DEFAULT 'system',
    started_at          TEXT DEFAULT '',
    ended_at            TEXT DEFAULT '',
    notes               TEXT DEFAULT '',
    classification      TEXT DEFAULT 'CUI',
    created_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS dsoc_bgp_hijacks (
    id                   SERIAL PRIMARY KEY,
    hijack_number        TEXT NOT NULL UNIQUE,
    hijack_type          TEXT NOT NULL CHECK(hijack_type IN (
                             'type_0','type_1','type_2','route_leak','rpki_invalid','unknown')),
    status               TEXT NOT NULL DEFAULT 'open' CHECK(status IN (
                             'open','under_review','mitigated','false_positive','resolved')),
    detected_prefix      TEXT NOT NULL,
    expected_prefix      TEXT NOT NULL,
    expected_origin_asn  INTEGER,
    observed_origin_asn  INTEGER,
    peer_asn             INTEGER,
    route_leak_type      TEXT DEFAULT '',
    confidence_pct       REAL DEFAULT 75.0,
    detection_source     TEXT DEFAULT 'internal',
    notes                TEXT DEFAULT '',
    resolved_at          TEXT DEFAULT '',
    classification       TEXT DEFAULT 'CUI',
    detected_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at           TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS dsoc_audit (
    id              SERIAL PRIMARY KEY,
    action          TEXT NOT NULL,
    entity_type     TEXT NOT NULL,
    entity_id       TEXT NOT NULL,
    user_id         TEXT DEFAULT 'system',
    classification  TEXT DEFAULT 'CUI // SP-CTI',
    ts              TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
"""

SCHEMA_SQLITE = (
    SCHEMA_PG
    .replace("SERIAL PRIMARY KEY", "INTEGER PRIMARY KEY AUTOINCREMENT")
    .replace("TIMESTAMP DEFAULT CURRENT_TIMESTAMP", "TEXT DEFAULT CURRENT_TIMESTAMP")
)


def init_db() -> None:
    from tools.db.storage import is_pg
    conn = get_connection()
    pg = is_pg(conn)
    schema = SCHEMA_PG if pg else SCHEMA_SQLITE
    try:
        for stmt in schema.split(";"):
            stmt = stmt.strip()
            if stmt:
                conn.execute(stmt)
        conn.commit()
        print(f"[init_db] DSOC schema ready ({'postgresql' if pg else 'sqlite'})")
    finally:
        conn.close()


if __name__ == "__main__":
    init_db()

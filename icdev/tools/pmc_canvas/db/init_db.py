# CUI // SP-CTI
"""Peering Management Canvas DB initializer.

Creates PMC tables for peer registry, IX memberships, prefix validation,
peering requests, and route policies.

Default backend: PostgreSQL. Set PMC_STORAGE_BACKEND=sqlite to override.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

_ICDEV_ROOT = Path(__file__).resolve().parents[3]
_SQLITE_PATH = _ICDEV_ROOT / "data" / "pmc_canvas.db"

_PMC_BACKEND = os.environ.get(
    "PMC_STORAGE_BACKEND",
    os.environ.get("ICDEV_CANVAS_STORAGE_BACKEND", "postgresql"),
).lower()


def get_connection():
    """Return a DB connection — PostgreSQL (default) or SQLite fallback.

    Uses get_canvas_connection() to disable RLS; canvas tables lack
    classification/tenant_id columns required by the global predicate.
    """
    if _PMC_BACKEND != "sqlite":
        try:
            from tools.db.storage import get_canvas_connection
            return get_canvas_connection("PMC_PG_DATABASE")
        except Exception as exc:
            print(
                f"[pmc-db] PostgreSQL unavailable ({exc}), falling back to SQLite",
                file=sys.stderr,
            )
    import sqlite3 as _sqlite3
    conn = _sqlite3.connect(str(_SQLITE_PATH))
    conn.row_factory = _sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


SCHEMA_PG = """
-- ── PMC: Peering Peers ────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS peering_peers (
    id                   TEXT PRIMARY KEY,
    asn                  INTEGER NOT NULL UNIQUE,
    org_name             TEXT NOT NULL DEFAULT '',
    peer_type            TEXT NOT NULL DEFAULT 'public'
                             CHECK(peer_type IN ('public','private','transit','customer','route-server')),
    policy               TEXT NOT NULL DEFAULT 'selective'
                             CHECK(policy IN ('open','selective','no')),
    status               TEXT NOT NULL DEFAULT 'evaluation'
                             CHECK(status IN ('evaluation','requested','active','suspended','closed')),
    peeringdb_net_id     INTEGER,
    peeringdb_synced_at  TIMESTAMPTZ,
    ipv4_prefix_count    INTEGER DEFAULT 0,
    ipv6_prefix_count    INTEGER DEFAULT 0,
    max_prefixes_v4      INTEGER DEFAULT 1000,
    max_prefixes_v6      INTEGER DEFAULT 1000,
    traffic_ratio        REAL DEFAULT 0.0,
    noc_email            TEXT DEFAULT '',
    irr_as_set           TEXT DEFAULT '',
    rir                  TEXT DEFAULT 'ARIN'
                             CHECK(rir IN ('ARIN','RIPE','APNIC','LACNIC','AFRINIC','UNKNOWN')),
    md5_password         TEXT DEFAULT '',
    multihop             INTEGER DEFAULT 1,
    notes                TEXT DEFAULT '',
    classification       TEXT DEFAULT 'CUI',
    created_at           TIMESTAMPTZ DEFAULT NOW(),
    updated_at           TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_pmc_peers_asn    ON peering_peers(asn);
CREATE INDEX IF NOT EXISTS idx_pmc_peers_status ON peering_peers(status);
CREATE INDEX IF NOT EXISTS idx_pmc_peers_type   ON peering_peers(peer_type);

-- ── PMC: Internet Exchanges ───────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS peering_ix (
    id               TEXT PRIMARY KEY,
    ix_name          TEXT NOT NULL,
    city             TEXT DEFAULT '',
    country          TEXT DEFAULT '',
    our_ipv4         TEXT DEFAULT '',
    our_ipv6         TEXT DEFAULT '',
    our_speed_gbps   REAL DEFAULT 0.0,
    monthly_cost_usd REAL DEFAULT 0.0,
    status           TEXT NOT NULL DEFAULT 'active'
                         CHECK(status IN ('active','pending','closed')),
    peeringdb_ix_id  INTEGER,
    ixlan_id         INTEGER,
    routeserver_v4   TEXT DEFAULT '',
    routeserver_v6   TEXT DEFAULT '',
    classification   TEXT DEFAULT 'CUI',
    created_at       TIMESTAMPTZ DEFAULT NOW(),
    updated_at       TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_pmc_ix_status  ON peering_ix(status);
CREATE INDEX IF NOT EXISTS idx_pmc_ix_country ON peering_ix(country);

-- ── PMC: Prefixes ─────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS peering_prefixes (
    id              TEXT PRIMARY KEY,
    peer_id         TEXT NOT NULL REFERENCES peering_peers(id) ON DELETE CASCADE,
    prefix          TEXT NOT NULL,
    address_family  TEXT NOT NULL DEFAULT 'ipv4'
                        CHECK(address_family IN ('ipv4','ipv6')),
    max_length      INTEGER,
    origin_asn      INTEGER,
    rpki_status     TEXT DEFAULT 'unknown'
                        CHECK(rpki_status IN ('valid','invalid','not-found','unknown')),
    roa_found       BOOLEAN DEFAULT FALSE,
    irr_registered  BOOLEAN DEFAULT FALSE,
    last_validated  TIMESTAMPTZ,
    classification  TEXT DEFAULT 'CUI',
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_pmc_pfx_peer    ON peering_prefixes(peer_id);
CREATE INDEX IF NOT EXISTS idx_pmc_pfx_rpki    ON peering_prefixes(rpki_status);
CREATE INDEX IF NOT EXISTS idx_pmc_pfx_family  ON peering_prefixes(address_family);
CREATE UNIQUE INDEX IF NOT EXISTS idx_pmc_pfx_unique ON peering_prefixes(peer_id, prefix);

-- ── PMC: Peering Requests ─────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS peering_requests (
    id               TEXT PRIMARY KEY,
    peer_id          TEXT REFERENCES peering_peers(id),
    request_type     TEXT NOT NULL DEFAULT 'new'
                         CHECK(request_type IN ('new','upgrade','terminate','policy_change')),
    status           TEXT NOT NULL DEFAULT 'pending'
                         CHECK(status IN ('pending','sent','accepted','rejected','withdrawn')),
    ix_id            TEXT REFERENCES peering_ix(id),
    proposed_speed   TEXT DEFAULT '',
    contact_method   TEXT DEFAULT 'email'
                         CHECK(contact_method IN ('email','peeringdb','noc','manual')),
    contact_address  TEXT DEFAULT '',
    notes            TEXT DEFAULT '',
    sent_at          TIMESTAMPTZ,
    responded_at     TIMESTAMPTZ,
    classification   TEXT DEFAULT 'CUI',
    created_at       TIMESTAMPTZ DEFAULT NOW(),
    updated_at       TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_pmc_req_status ON peering_requests(status);
CREATE INDEX IF NOT EXISTS idx_pmc_req_peer   ON peering_requests(peer_id);

-- ── PMC: Route Policies ───────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS peering_policies (
    id              TEXT PRIMARY KEY,
    policy_name     TEXT NOT NULL UNIQUE,
    our_asn         INTEGER NOT NULL,
    policy_type     TEXT NOT NULL DEFAULT 'import'
                        CHECK(policy_type IN ('import','export')),
    match_criteria  JSONB DEFAULT '{}',
    action          TEXT NOT NULL DEFAULT 'accept'
                        CHECK(action IN ('accept','reject','prepend','set-community','set-med')),
    action_params   JSONB DEFAULT '{}',
    priority        INTEGER DEFAULT 100,
    active          BOOLEAN DEFAULT TRUE,
    classification  TEXT DEFAULT 'CUI',
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_pmc_pol_type   ON peering_policies(policy_type);
CREATE INDEX IF NOT EXISTS idx_pmc_pol_active ON peering_policies(active);

-- ── PMC: Audit (append-only) ──────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS pmc_audit (
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

SCHEMA_SQLITE = """
CREATE TABLE IF NOT EXISTS peering_peers (
    id                   TEXT PRIMARY KEY,
    asn                  INTEGER NOT NULL UNIQUE,
    org_name             TEXT NOT NULL DEFAULT '',
    peer_type            TEXT NOT NULL DEFAULT 'public',
    policy               TEXT NOT NULL DEFAULT 'selective',
    status               TEXT NOT NULL DEFAULT 'evaluation',
    peeringdb_net_id     INTEGER,
    peeringdb_synced_at  TEXT,
    ipv4_prefix_count    INTEGER DEFAULT 0,
    ipv6_prefix_count    INTEGER DEFAULT 0,
    max_prefixes_v4      INTEGER DEFAULT 1000,
    max_prefixes_v6      INTEGER DEFAULT 1000,
    traffic_ratio        REAL DEFAULT 0.0,
    noc_email            TEXT DEFAULT '',
    irr_as_set           TEXT DEFAULT '',
    rir                  TEXT DEFAULT 'ARIN',
    md5_password         TEXT DEFAULT '',
    multihop             INTEGER DEFAULT 1,
    notes                TEXT DEFAULT '',
    classification       TEXT DEFAULT 'CUI',
    created_at           TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at           TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_pmc_peers_asn    ON peering_peers(asn);
CREATE INDEX IF NOT EXISTS idx_pmc_peers_status ON peering_peers(status);
CREATE INDEX IF NOT EXISTS idx_pmc_peers_type   ON peering_peers(peer_type);

CREATE TABLE IF NOT EXISTS peering_ix (
    id               TEXT PRIMARY KEY,
    ix_name          TEXT NOT NULL,
    city             TEXT DEFAULT '',
    country          TEXT DEFAULT '',
    our_ipv4         TEXT DEFAULT '',
    our_ipv6         TEXT DEFAULT '',
    our_speed_gbps   REAL DEFAULT 0.0,
    monthly_cost_usd REAL DEFAULT 0.0,
    status           TEXT NOT NULL DEFAULT 'active',
    peeringdb_ix_id  INTEGER,
    ixlan_id         INTEGER,
    routeserver_v4   TEXT DEFAULT '',
    routeserver_v6   TEXT DEFAULT '',
    classification   TEXT DEFAULT 'CUI',
    created_at       TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at       TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_pmc_ix_status  ON peering_ix(status);
CREATE INDEX IF NOT EXISTS idx_pmc_ix_country ON peering_ix(country);

CREATE TABLE IF NOT EXISTS peering_prefixes (
    id              TEXT PRIMARY KEY,
    peer_id         TEXT NOT NULL REFERENCES peering_peers(id) ON DELETE CASCADE,
    prefix          TEXT NOT NULL,
    address_family  TEXT NOT NULL DEFAULT 'ipv4',
    max_length      INTEGER,
    origin_asn      INTEGER,
    rpki_status     TEXT DEFAULT 'unknown',
    roa_found       INTEGER DEFAULT 0,
    irr_registered  INTEGER DEFAULT 0,
    last_validated  TEXT,
    classification  TEXT DEFAULT 'CUI',
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_pmc_pfx_peer   ON peering_prefixes(peer_id);
CREATE INDEX IF NOT EXISTS idx_pmc_pfx_rpki   ON peering_prefixes(rpki_status);
CREATE INDEX IF NOT EXISTS idx_pmc_pfx_family ON peering_prefixes(address_family);
CREATE UNIQUE INDEX IF NOT EXISTS idx_pmc_pfx_unique ON peering_prefixes(peer_id, prefix);

CREATE TABLE IF NOT EXISTS peering_requests (
    id               TEXT PRIMARY KEY,
    peer_id          TEXT REFERENCES peering_peers(id),
    request_type     TEXT NOT NULL DEFAULT 'new',
    status           TEXT NOT NULL DEFAULT 'pending',
    ix_id            TEXT REFERENCES peering_ix(id),
    proposed_speed   TEXT DEFAULT '',
    contact_method   TEXT DEFAULT 'email',
    contact_address  TEXT DEFAULT '',
    notes            TEXT DEFAULT '',
    sent_at          TEXT,
    responded_at     TEXT,
    classification   TEXT DEFAULT 'CUI',
    created_at       TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at       TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_pmc_req_status ON peering_requests(status);
CREATE INDEX IF NOT EXISTS idx_pmc_req_peer   ON peering_requests(peer_id);

CREATE TABLE IF NOT EXISTS peering_policies (
    id              TEXT PRIMARY KEY,
    policy_name     TEXT NOT NULL UNIQUE,
    our_asn         INTEGER NOT NULL,
    policy_type     TEXT NOT NULL DEFAULT 'import',
    match_criteria  TEXT DEFAULT '{}',
    action          TEXT NOT NULL DEFAULT 'accept',
    action_params   TEXT DEFAULT '{}',
    priority        INTEGER DEFAULT 100,
    active          INTEGER DEFAULT 1,
    classification  TEXT DEFAULT 'CUI',
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at      TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_pmc_pol_type   ON peering_policies(policy_type);
CREATE INDEX IF NOT EXISTS idx_pmc_pol_active ON peering_policies(active);

CREATE TABLE IF NOT EXISTS pmc_audit (
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
    """Create PMC schema tables (idempotent)."""
    _conn = conn or get_connection()
    owned = conn is None
    try:
        schema = SCHEMA_SQLITE if _PMC_BACKEND == "sqlite" else SCHEMA_PG
        if _PMC_BACKEND == "sqlite":
            _conn.executescript(schema)
        else:
            cur = _conn.cursor()
            for stmt in [s.strip() for s in schema.split(";") if s.strip()]:
                cur.execute(stmt)
            _conn.commit()
        print("[pmc-db] Schema initialized OK", file=sys.stderr)
    except Exception as exc:
        print(f"[pmc-db] Schema init error: {exc}", file=sys.stderr)
        raise
    finally:
        if owned:
            _conn.close()


if __name__ == "__main__":
    init_db()
    print(f"[pmc-db] Backend: {_PMC_BACKEND}")

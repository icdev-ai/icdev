# CUI // SP-CTI
"""Circuit & Capacity Canvas (CCC) — DB initializer.

Dual-backend: PostgreSQL (default) or SQLite fallback.
DB file: data/ccc_canvas.db  |  env: CCC_STORAGE_BACKEND, CCC_DB_PATH
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

_ICDEV_ROOT = Path(__file__).resolve().parents[4]
DB_PATH = Path(os.environ.get("CCC_DB_PATH", str(_ICDEV_ROOT / "data" / "ccc_canvas.db")))

_CCC_BACKEND = os.environ.get(
    "CCC_STORAGE_BACKEND",
    os.environ.get("ICDEV_CANVAS_STORAGE_BACKEND", "postgresql"),
).lower()


def get_connection():
    if _CCC_BACKEND == "postgresql":
        try:
            from tools.db.storage import get_canvas_connection
            return get_canvas_connection("CCC_PG_DATABASE")
        except Exception:
            pass
    import sqlite3
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


SCHEMA_PG = """
CREATE TABLE IF NOT EXISTS ccc_circuits (
    id                  SERIAL PRIMARY KEY,
    circuit_id          TEXT NOT NULL UNIQUE,
    circuit_type        TEXT NOT NULL CHECK(circuit_type IN (
                            'ethernet','wavelength','dark_fiber','oc3','oc12',
                            'oc48','oc192','100g','400g','mpls','ip_vpn','other')),
    carrier             TEXT NOT NULL,
    bandwidth_gbps      REAL NOT NULL DEFAULT 0,
    endpoint_a          TEXT DEFAULT '',
    endpoint_z          TEXT DEFAULT '',
    status              TEXT NOT NULL DEFAULT 'active' CHECK(status IN (
                            'active','pending','maintenance','decommissioned','test')),
    provider_circuit_id TEXT DEFAULT '',
    mrr_usd             REAL DEFAULT 0,
    install_date        TEXT DEFAULT '',
    contract_end        TEXT DEFAULT '',
    utilization_pct     REAL DEFAULT 0,
    classification      TEXT DEFAULT 'CUI',
    created_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS ccc_cross_connects (
    id               SERIAL PRIMARY KEY,
    xc_number        TEXT NOT NULL UNIQUE,
    facility         TEXT NOT NULL CHECK(facility IN (
                         'equinix','megaport','zayo','lumen','cogent',
                         'coresite','digital_realty','switch','other')),
    facility_id      TEXT DEFAULT '',
    port_a           TEXT DEFAULT '',
    port_z           TEXT DEFAULT '',
    speed_gbps       REAL DEFAULT 1,
    status           TEXT NOT NULL DEFAULT 'active' CHECK(status IN (
                         'active','pending_loa','installing','decommissioned','suspended')),
    circuit_id       INTEGER REFERENCES ccc_circuits(id) ON DELETE SET NULL,
    monthly_cost_usd REAL DEFAULT 0,
    patch_panel      TEXT DEFAULT '',
    notes            TEXT DEFAULT '',
    classification   TEXT DEFAULT 'CUI',
    created_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS ccc_loa_requests (
    id                  SERIAL PRIMARY KEY,
    loa_number          TEXT NOT NULL UNIQUE,
    circuit_id          INTEGER REFERENCES ccc_circuits(id) ON DELETE SET NULL,
    xc_id               INTEGER REFERENCES ccc_cross_connects(id) ON DELETE SET NULL,
    facility            TEXT NOT NULL,
    rack_a              TEXT DEFAULT '',
    rack_z              TEXT DEFAULT '',
    patch_panel_a       TEXT DEFAULT '',
    patch_panel_z       TEXT DEFAULT '',
    requester_name      TEXT NOT NULL DEFAULT '',
    requester_email     TEXT NOT NULL DEFAULT '',
    requester_company   TEXT NOT NULL DEFAULT '',
    status              TEXT NOT NULL DEFAULT 'draft' CHECK(status IN (
                            'draft','submitted','approved','expired','completed','rejected')),
    valid_days          INTEGER DEFAULT 30,
    submitted_at        TEXT DEFAULT '',
    approved_at         TEXT DEFAULT '',
    loa_document_path   TEXT DEFAULT '',
    notes               TEXT DEFAULT '',
    classification      TEXT DEFAULT 'CUI',
    created_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS ccc_capacity_plans (
    id                          SERIAL PRIMARY KEY,
    circuit_id                  INTEGER REFERENCES ccc_circuits(id) ON DELETE CASCADE,
    plan_date                   TEXT NOT NULL DEFAULT CURRENT_DATE,
    current_utilization_pct     REAL DEFAULT 0,
    projected_utilization_pct   REAL DEFAULT 0,
    months_to_saturation        INTEGER DEFAULT 0,
    recommended_action          TEXT DEFAULT '',
    growth_rate_pct             REAL DEFAULT 0,
    confidence_score            REAL DEFAULT 0,
    classification              TEXT DEFAULT 'CUI',
    created_at                  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS ccc_dwdm_spans (
    id                  SERIAL PRIMARY KEY,
    span_id             TEXT NOT NULL UNIQUE,
    fiber_route         TEXT NOT NULL DEFAULT '',
    total_capacity_tbps REAL DEFAULT 0,
    used_capacity_tbps  REAL DEFAULT 0,
    available_wavelengths INTEGER DEFAULT 0,
    amplifier_count     INTEGER DEFAULT 0,
    osnr_db             REAL DEFAULT 0,
    status              TEXT NOT NULL DEFAULT 'operational' CHECK(status IN (
                            'operational','degraded','dark','maintenance')),
    notes               TEXT DEFAULT '',
    classification      TEXT DEFAULT 'CUI',
    created_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS ccc_audit (
    id              SERIAL PRIMARY KEY,
    action          TEXT NOT NULL,
    entity_type     TEXT NOT NULL,
    entity_id       TEXT NOT NULL,
    user_id         TEXT DEFAULT 'system',
    classification  TEXT DEFAULT 'CUI // SP-CTI',
    ts              TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS ccc_xc_order_events (
    id              SERIAL PRIMARY KEY,
    xc_id           INTEGER NOT NULL REFERENCES ccc_cross_connects(id) ON DELETE CASCADE,
    event_type      TEXT NOT NULL DEFAULT '',
    old_status      TEXT DEFAULT '',
    new_status      TEXT DEFAULT '',
    note            TEXT DEFAULT '',
    actor           TEXT DEFAULT 'system',
    carrier_ref     TEXT DEFAULT '',
    classification  TEXT DEFAULT 'CUI',
    ts              TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""

SCHEMA_SQLITE = SCHEMA_PG.replace("SERIAL PRIMARY KEY", "INTEGER PRIMARY KEY AUTOINCREMENT") \
                          .replace("TIMESTAMP DEFAULT CURRENT_TIMESTAMP", "TEXT DEFAULT CURRENT_TIMESTAMP") \
                          .replace("TIMESTAMP DEFAULT CURRENT_DATE", "TEXT DEFAULT CURRENT_DATE") \
                          .replace("DEFAULT CURRENT_DATE", "DEFAULT (date('now'))")


def _try_exec(conn, sql_pg: str, sql_sq: str, params: tuple = ()) -> None:
    try:
        conn.execute(sql_pg, params)
    except Exception:
        conn.execute(sql_sq, params)


_CCC_MIGRATIONS = [
    # (pg_sql, sqlite_sql) — add new columns to ccc_cross_connects for order workflow
    ("ALTER TABLE ccc_cross_connects ADD COLUMN IF NOT EXISTS order_id TEXT DEFAULT ''",
     "ALTER TABLE ccc_cross_connects ADD COLUMN order_id TEXT DEFAULT ''"),
    ("ALTER TABLE ccc_cross_connects ADD COLUMN IF NOT EXISTS order_status TEXT DEFAULT 'not_ordered'",
     "ALTER TABLE ccc_cross_connects ADD COLUMN order_status TEXT DEFAULT 'not_ordered'"),
    ("ALTER TABLE ccc_cross_connects ADD COLUMN IF NOT EXISTS estimated_delivery TEXT DEFAULT ''",
     "ALTER TABLE ccc_cross_connects ADD COLUMN estimated_delivery TEXT DEFAULT ''"),
    ("ALTER TABLE ccc_cross_connects ADD COLUMN IF NOT EXISTS ordered_by TEXT DEFAULT ''",
     "ALTER TABLE ccc_cross_connects ADD COLUMN ordered_by TEXT DEFAULT ''"),
    ("ALTER TABLE ccc_cross_connects ADD COLUMN IF NOT EXISTS carrier_ticket_id TEXT DEFAULT ''",
     "ALTER TABLE ccc_cross_connects ADD COLUMN carrier_ticket_id TEXT DEFAULT ''"),
    ("ALTER TABLE ccc_cross_connects ADD COLUMN IF NOT EXISTS partner_id INTEGER",
     "ALTER TABLE ccc_cross_connects ADD COLUMN partner_id INTEGER"),
]


def init_db() -> None:
    conn = get_connection()
    schema = SCHEMA_PG if _CCC_BACKEND == "postgresql" else SCHEMA_SQLITE
    try:
        for stmt in schema.split(";"):
            stmt = stmt.strip()
            if stmt:
                conn.execute(stmt)
        conn.commit()
        # Column migrations for ccc_cross_connects order workflow fields
        for pg_sql, sq_sql in _CCC_MIGRATIONS:
            try:
                conn.execute(pg_sql)
                conn.commit()
            except Exception:
                try:
                    conn.execute(sq_sql)
                    conn.commit()
                except Exception:
                    pass  # column already exists
        print(f"[init_db] CCC schema ready ({_CCC_BACKEND})", file=sys.stderr)
    finally:
        conn.close()


if __name__ == "__main__":
    init_db()

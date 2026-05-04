#!/usr/bin/env python3
# CUI // SP-CTI
"""Migration 050 — Extend supply_chain_vendors.vendor_type with 'defense_contractor'.

SQLite cannot ALTER CHECK constraints, so the table is recreated via the
rename → create → copy → drop pattern (same approach as migration 038).
PostgreSQL supports DROP/ADD CONSTRAINT directly.
"""
from __future__ import annotations

from tools.db.storage import get_connection

MIGRATION_ID = "050"
MIGRATION_NAME = "theater_supply_chain"
DESCRIPTION = "Extend supply_chain_vendors.vendor_type to include defense_contractor"

_NEW_VENDOR_TYPES = (
    "'cots', 'gots', 'oss', 'saas', 'paas', 'iaas', "
    "'contractor', 'subcontractor', 'defense_contractor'"
)

_NEW_TABLE_SQLITE = f"""
CREATE TABLE IF NOT EXISTS supply_chain_vendors_new (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id),
    vendor_name TEXT NOT NULL,
    vendor_type TEXT CHECK(vendor_type IN ({_NEW_VENDOR_TYPES})),
    country_of_origin TEXT,
    scrm_risk_tier TEXT CHECK(scrm_risk_tier IN ('low', 'moderate', 'high', 'critical')),
    section_889_status TEXT CHECK(section_889_status IN ('compliant', 'under_review', 'prohibited', 'exempt')),
    dod_approved INTEGER DEFAULT 0,
    contact_info TEXT,
    isa_required INTEGER DEFAULT 0,
    last_assessed TEXT,
    classification TEXT DEFAULT 'CUI',
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now')),
    UNIQUE(project_id, vendor_name)
)
"""

_COPY_DATA = """
INSERT INTO supply_chain_vendors_new
    (id, project_id, vendor_name, vendor_type, country_of_origin,
     scrm_risk_tier, section_889_status, dod_approved, contact_info,
     isa_required, last_assessed, classification, created_at, updated_at)
SELECT id, project_id, vendor_name, vendor_type, country_of_origin,
       scrm_risk_tier, section_889_status, dod_approved, contact_info,
       isa_required, last_assessed, classification, created_at, updated_at
FROM supply_chain_vendors
"""


def up(conn=None) -> None:
    conn = get_connection()
    try:
        backend = getattr(conn, "_backend", "sqlite")
        if backend == "postgresql":
            conn.execute(
                "ALTER TABLE supply_chain_vendors "
                "DROP CONSTRAINT IF EXISTS supply_chain_vendors_vendor_type_check"
            )
            conn.execute(
                "ALTER TABLE supply_chain_vendors "
                "ADD CONSTRAINT supply_chain_vendors_vendor_type_check "
                f"CHECK(vendor_type IN ({_NEW_VENDOR_TYPES}))"
            )
        else:
            conn.execute(_NEW_TABLE_SQLITE)
            conn.execute(_COPY_DATA)
            conn.execute("DROP TABLE supply_chain_vendors")
            conn.execute("ALTER TABLE supply_chain_vendors_new RENAME TO supply_chain_vendors")
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_scv_project ON supply_chain_vendors(project_id)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_scv_risk ON supply_chain_vendors(scrm_risk_tier)"
            )
        conn.commit()
        print("[050_theater_supply_chain] up: vendor_type extended with defense_contractor")
    finally:
        conn.close()


if __name__ == "__main__":
    up()

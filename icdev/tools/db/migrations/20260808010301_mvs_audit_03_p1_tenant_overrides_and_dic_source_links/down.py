#!/usr/bin/env python3
# CUI // SP-CTI
"""Reverse of 20260808010301.

The table is dropped; the two ``dic_documents`` columns are NOT.

Dropping a column destroys the provenance backlinks of every document created
while the migration was applied — a down-migration that loses data is worse than
one that leaves two nullable columns behind, and the columns are inert to every
consumer that does not name them. ``tenant_component_overrides`` is safe to drop
because it holds only operator-set overrides, and their absence falls back to the
environment default, which is exactly the behaviour before it existed.
"""

MIGRATION_ID = "20260808010301"


def down(conn) -> dict:
    conn.execute("DROP INDEX IF EXISTS idx_tenant_component_overrides_key")
    conn.execute("DROP INDEX IF EXISTS idx_tenant_component_overrides_tenant")
    conn.execute("DROP TABLE IF EXISTS tenant_component_overrides")
    conn.commit()
    return {
        "status": "reverted",
        "dropped": ["tenant_component_overrides"],
        "kept": ["dic_documents.source_wg_result_id", "dic_documents.source_idr_session_id"],
    }

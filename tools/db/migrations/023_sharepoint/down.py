#!/usr/bin/env python3
# CUI // SP-CTI
"""Migration 023 rollback — drops SharePoint integration tables."""

MIGRATION_ID = "023"
MIGRATION_NAME = "sharepoint"

_INDEXES = [
    "idx_sp_lists_site_id",
    "idx_sp_items_list_id",
    "idx_sp_items_modified",
    "idx_sp_docs_site_id",
    "idx_sp_docs_path",
]

# Drop in reverse dependency order (children before parents)
_TABLES = [
    "sharepoint_items",
    "sharepoint_documents",
    "sharepoint_lists",
    "sharepoint_sites",
]


def down(conn) -> dict:
    actions = []

    for idx in _INDEXES:
        conn.execute(f"DROP INDEX IF EXISTS {idx}")
    actions.append("dropped_indexes")

    for table in _TABLES:
        conn.execute(f"DROP TABLE IF EXISTS {table}")
        actions.append(f"dropped_{table}")

    conn.commit()
    return {"status": "reverted", "actions": actions}

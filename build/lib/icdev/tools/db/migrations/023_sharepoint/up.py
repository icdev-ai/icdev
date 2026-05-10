#!/usr/bin/env python3
# CUI // SP-CTI
"""Migration 023: Create SharePoint integration tables.

Tables created:
  - sharepoint_sites      — registered SharePoint site roots (url, title, last_modified)
  - sharepoint_lists      — lists within a site (name, item_count, FK → site)
  - sharepoint_items      — list items with arbitrary JSON payload (FK → list)
  - sharepoint_documents  — document library entries with path, size, mime, content hash

All payload columns stored as TEXT (SQLite-compatible); translate_sql promotes to
JSONB on PostgreSQL automatically.  All CREATE statements are idempotent.
"""

MIGRATION_ID = "023"
MIGRATION_NAME = "sharepoint"
DESCRIPTION = (
    "Create 4 SharePoint integration tables: sharepoint_sites, sharepoint_lists, "
    "sharepoint_items (with JSON payload), and sharepoint_documents."
)

_CREATE_STMTS = [
    # sharepoint_sites — root site registrations
    """CREATE TABLE IF NOT EXISTS sharepoint_sites (
        id            TEXT PRIMARY KEY,
        url           TEXT NOT NULL,
        title         TEXT NOT NULL DEFAULT '',
        last_modified TEXT
    )""",
    # sharepoint_lists — lists/libraries within a site
    """CREATE TABLE IF NOT EXISTS sharepoint_lists (
        id         TEXT PRIMARY KEY,
        site_id    TEXT NOT NULL,
        name       TEXT NOT NULL DEFAULT '',
        item_count INTEGER DEFAULT 0
    )""",
    # sharepoint_items — individual list items; payload is JSON (TEXT/JSONB)
    """CREATE TABLE IF NOT EXISTS sharepoint_items (
        id          TEXT PRIMARY KEY,
        list_id     TEXT NOT NULL,
        title       TEXT NOT NULL DEFAULT '',
        payload     TEXT,
        modified_at TEXT
    )""",
    # sharepoint_documents — document library files
    """CREATE TABLE IF NOT EXISTS sharepoint_documents (
        id           TEXT PRIMARY KEY,
        site_id      TEXT NOT NULL,
        path         TEXT NOT NULL DEFAULT '',
        size         INTEGER DEFAULT 0,
        mime_type    TEXT DEFAULT '',
        content_hash TEXT DEFAULT ''
    )""",
]

_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_sp_lists_site_id ON sharepoint_lists (site_id)",
    "CREATE INDEX IF NOT EXISTS idx_sp_items_list_id ON sharepoint_items (list_id)",
    "CREATE INDEX IF NOT EXISTS idx_sp_items_modified ON sharepoint_items (modified_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_sp_docs_site_id ON sharepoint_documents (site_id)",
    "CREATE INDEX IF NOT EXISTS idx_sp_docs_path ON sharepoint_documents (path)",
]


def up(conn) -> dict:
    """Apply migration: create SharePoint tables (idempotent)."""
    actions = []
    errors = []

    for stmt in _CREATE_STMTS:
        table = stmt.strip().split("EXISTS")[1].split("(")[0].strip()
        try:
            conn.execute(stmt)
            actions.append(f"created_or_verified_{table}")
        except Exception as exc:
            errors.append(f"{table}: {exc}")

    for idx_stmt in _INDEXES:
        try:
            conn.execute(idx_stmt)
        except Exception as exc:
            errors.append(f"index_skipped: {exc}")

    conn.commit()
    return {
        "status": "applied" if not errors else "partial",
        "actions": actions,
        "errors": errors,
    }

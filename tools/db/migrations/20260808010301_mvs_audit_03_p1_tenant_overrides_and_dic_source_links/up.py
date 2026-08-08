#!/usr/bin/env python3
# CUI // SP-CTI
"""Migration 20260808010301 — the two shadowed migrations nothing else covers.

mvs-audit-03-d5 classified all 60 grandfathered shadowed migrations: eleven are
gaps on PostgreSQL, six of those break live code, and PR #1296 fixes four. These
are the other two, present in NO remediation on main or on any open branch. Both
lost a version collision, and ``MigrationRunner`` keeps the FIRST entry per
version, so neither ever ran anywhere:

    207_tenant_component_overrides   lost 207 to 207_mcip_dat_tables.sql
    257_idr_dic_doc_link.sql         lost 257 to 257_doc_modernization.sql

**A. tenant_component_overrides — fails OPEN, which is why it went unnoticed.**
``tools/config/component_registry.py`` wraps the lookup in
``except Exception -> logger.debug -> return env_enabled``, so on PostgreSQL the
UndefinedTable is swallowed and a component DISABLED for one tenant reads as
ENABLED whenever the global default is on — at DEBUG level, surfaced nowhere.
Four modules consume it (component_registry, idp/tenancy, and the admin_console
+ idp IQE adapters). The write path already reports failure at WARNING; it is
the silent read that makes this the worse of the two.

**B. dic_documents source links — the docgen -> Tech Writer bridge returns 500.**
``tools/document_intelligence/blueprint.py`` writes both columns twice (UPDATE
on the reuse path, INSERT on the generate path) inside one ``try`` whose handler
returns HTTP 500. The ``idr_sessions`` half of migration 257 IS present on
PostgreSQL; only the ``dic_documents`` half was lost, so the failure is partial
and reads like an application bug rather than a missing column.

Why Python and not up.sql
-------------------------
``ALTER TABLE ... ADD COLUMN IF NOT EXISTS`` is PostgreSQL-only; SQLite rejects
it outright. Both backends need these columns — on SQLite ``dic_documents`` is
created at app startup by ``tools/document_intelligence/ingest_orchestrator.py``
and that DDL carries ``source_id`` but neither source link — so the add has to be
guarded by introspection rather than by SQL syntax. The existing migration idiom
(``PRAGMA table_info``) is SQLite-only and would fail on the primary backend, so
column existence is resolved per backend here.

Fresh-install defect, invisible on this box
-------------------------------------------
The live database HAS all three objects: its ``schema_migrations`` records 207
applied as ``tenant_component_overrides`` on 2026-06-22, i.e. it won the
collision the other way round. A fresh PostgreSQL build does not — all three are
absent from ``tools/db/schema/pg_consolidated.sql``. That asymmetry is why two
prior passes at this problem missed it, and why "it works here" is not evidence.
Every statement is idempotent, so this is a no-op on the live box.
"""

import os

MIGRATION_ID = "20260808010301"
MIGRATION_NAME = "mvs_audit_03_p1_tenant_overrides_and_dic_source_links"
DESCRIPTION = (
    "Create tenant_component_overrides and add dic_documents source links — the "
    "two shadowed migrations (207, 257) no other remediation covers."
)

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS tenant_component_overrides (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    component_key TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1 CHECK(enabled IN (0, 1)),
    updated_by TEXT DEFAULT 'system',
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(tenant_id, component_key)
)
"""
# 207 declared ``updated_at TEXT NOT NULL DEFAULT (datetime('now'))``.
# datetime('now') is a syntax error on PostgreSQL — the backend this migration
# exists to repair — so the portable CURRENT_TIMESTAMP form is used instead.

_INDEXES = (
    "CREATE INDEX IF NOT EXISTS idx_tenant_component_overrides_tenant "
    "ON tenant_component_overrides(tenant_id)",
    "CREATE INDEX IF NOT EXISTS idx_tenant_component_overrides_key "
    "ON tenant_component_overrides(component_key)",
)

_DIC_COLUMNS = (("source_wg_result_id", "TEXT"), ("source_idr_session_id", "TEXT"))


def _is_pg() -> bool:
    return os.environ.get("ICDEV_STORAGE_BACKEND", "sqlite").strip().lower() in (
        "postgresql", "postgres", "pg",
    )


# information_schema spans EVERY schema in the database, so an unqualified
# `WHERE table_name = ...` answers for whichever copy it finds first. Both
# lookups are therefore pinned to current_schema() — the schema a bare CREATE or
# ALTER in this migration actually writes to. Without the pin, verifying this
# migration in a scratch schema reported both columns as already present
# (because public.dic_documents has them) and silently skipped the ALTER it
# exists to perform.


def _table_exists(conn, table: str) -> bool:
    if _is_pg():
        row = conn.execute(
            "SELECT 1 FROM information_schema.tables "
            "WHERE table_name = %s AND table_schema = current_schema()", (table,)
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone()
    return row is not None


def _columns(conn, table: str) -> set:
    if _is_pg():
        rows = conn.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = %s AND table_schema = current_schema()",
            (table,),
        ).fetchall()
        return {(r[0] if not isinstance(r, dict) else r["column_name"]) for r in rows}
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()  # nosec B608 -- fixed literal
    return {r[1] for r in rows}


def up(conn) -> dict:
    """Apply idempotently on either backend."""
    conn.execute(_CREATE_TABLE)
    for stmt in _INDEXES:
        conn.execute(stmt)

    added, skipped = [], []
    # dic_documents is created by the DIC canvas at startup, not by this chain,
    # so on a database where the canvas has never run there is nothing to alter
    # yet — the canvas will create it with its own (still incomplete) DDL later.
    # Adding the columns is therefore best-effort, never fatal.
    if _table_exists(conn, "dic_documents"):
        existing = _columns(conn, "dic_documents")
        for col, col_type in _DIC_COLUMNS:
            if col in existing:
                skipped.append(col)
                continue
            conn.execute(f"ALTER TABLE dic_documents ADD COLUMN {col} {col_type}")  # nosec B608
            added.append(col)
    else:
        skipped = [c for c, _ in _DIC_COLUMNS]

    conn.commit()
    return {
        "status": "applied",
        "table": "tenant_component_overrides",
        "indexes": len(_INDEXES),
        "dic_columns_added": added,
        "dic_columns_skipped": skipped,
    }

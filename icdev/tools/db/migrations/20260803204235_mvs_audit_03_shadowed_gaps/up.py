# CUI // SP-CTI
"""Migration 20260803204235 — schema that six shadowed migrations never delivered.

``args/migration_duplicate_versions.yaml`` grandfathers 48 duplicated migration
versions, and a duplicate version means the loser is skipped permanently and
silently: ``MigrationRunner`` keeps the FIRST entry per version, so every other
entry declaring that version never runs and the objects it declares are simply
never created. Freezing the list made the collision gate actionable; it never
established that the frozen entries were harmless.

``tools/db/shadowed_migration_audit.py`` answers that question by build rather
than by inspection, and six of the shadowed entries turned out to declare schema
that no supported backend produces on a fresh install. This migration folds
their DDL back in. Folding rather than renumbering, for two reasons:

* **Ordering.** A renumbered entry gets a 14-digit timestamp id, which sorts
  after every 3-digit legacy version — so ``236_rfi_workbench`` would run AFTER
  the five migrations that ALTER its tables, and those would still fail on the
  first pass. Folding puts the CREATEs and the widenings in one entry with no
  ordering claim to get wrong.
* **Blast radius.** ``050_theater_supply_chain`` widens a CHECK on SQLite by
  rebuilding the table — ``CREATE ... _new``, copy, ``DROP TABLE``, rename.
  Re-animating that against databases where the table is already correct is a
  destructive operation to fix a non-destructive problem. The vendor_type fix
  therefore lands in ``init_icdev_db.py``'s CREATE TABLE instead, where fresh
  SQLite databases actually get their copy of that table.

Every statement is idempotent, because this runs against three populations at
once: fresh SQLite (has the ``init_icdev_db.py`` tables, none of these), fresh
PostgreSQL (has the ``pg_consolidated.sql`` snapshot), and long-lived databases
where most of these objects were created by hand years ago.

## What was actually missing, and where

======================================  ============================  =========
shadowed entry                          object                        missing on
======================================  ============================  =========
139_govlift_rbac_roles / 247_..._check  dashboard_users.role CHECK    PG
210_sso                                 sso_providers, sso_sessions   PG+SQLite
236_rfi_workbench.sql                   rfi_workbench_* (3 tables)    PG+SQLite
113_kanban_vibe_tier1                   kanban_tasks +5 cols          SQLite
055_sg_conflict_events_cyber_op         sg_conflict_events +4 cols    SQLite
184_memory_fts5                         memory_fts (FTS5)             SQLite
======================================  ============================  =========

The role CHECK is the one with teeth. ``dashboard_users.role`` on PostgreSQL —
the primary backend — still rejects ``migration_engineer``, ``component_admin``,
``auditor`` and ``ciso``, four roles that ``tools/dashboard/auth.py``'s
RBAC_MATRIX and ``tools/govlift/rbac.py``'s GOVLIFT_ROLES both hand out and that
numerous ``@require_role(...)`` call sites gate on. TWO separate migrations were
written to widen it (139 and 247) and BOTH were shadowed, so ``create_user()``
with any of those four roles has never been able to succeed on PostgreSQL. It is
invisible to a table/column diff because the migration changes neither.

``memory_fts`` is the quiet one: ``tools/memory/session_indexer.py`` only ever
probes for it (``_fts5_available`` runs a SELECT and returns False on error) and
never creates it, so full-text search has been silently degraded to "unavailable"
on every SQLite install rather than failing loudly.
"""
from __future__ import annotations

# The same vocabulary as tools/dashboard/auth.py::VALID_DASHBOARD_ROLES, and the
# list migration 247 reproduces. Written literally ON PURPOSE: a migration is a
# frozen historical statement, so importing a live constant would let a future
# edit rewrite what an already-applied migration claims to have done. CLAUDE.md's
# "derive CHECK constraints from Python constants" rule is honoured by a test
# instead — tests/db/test_shadowed_migration_audit.py compares these two SETS, so
# they cannot drift apart the way the DB copy and the Python copy already did.
_ROLES = (
    "admin", "pm", "developer", "isso", "co", "cor",
    "migration_engineer", "component_admin", "auditor", "ciso",
    "bd", "capture_mgr", "contract_mgr", "reviewer",
)

_TAG = "[20260803204235_mvs_audit_03_shadowed_gaps]"

# --------------------------------------------------------------------------- #
# 210_sso — shadowed by 210_showcase_apps.sql
# --------------------------------------------------------------------------- #
# Consumed by tools/auth/oidc.py, saml.py, session.py, tools/admin/blueprint.py
# and tools/compliance/gdpr_eraser.py. Nothing outside the shadowed migration
# declares them, so enterprise SSO has never worked on a new deployment.
_SSO = (
    """
    CREATE TABLE IF NOT EXISTS sso_providers (
        id TEXT PRIMARY KEY,
        tenant_id TEXT NOT NULL,
        name TEXT NOT NULL,
        protocol TEXT NOT NULL CHECK(protocol IN ('saml','oidc')),
        entity_id TEXT,
        metadata_url TEXT,
        client_id TEXT,
        client_secret_enc TEXT,
        attr_mapping TEXT,
        claims_mapping TEXT,
        enabled INTEGER NOT NULL DEFAULT 1,
        created_at TEXT,
        updated_at TEXT
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_sso_providers_tenant ON sso_providers(tenant_id)",
    """
    CREATE TABLE IF NOT EXISTS sso_sessions (
        id TEXT PRIMARY KEY,
        tenant_id TEXT NOT NULL,
        provider_id TEXT NOT NULL,
        user_id TEXT,
        name_id TEXT,
        session_index TEXT,
        id_token TEXT,
        access_token_enc TEXT,
        created_at TEXT,
        expires_at TEXT
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_sso_sessions_tenant ON sso_sessions(tenant_id)",
    "CREATE INDEX IF NOT EXISTS idx_sso_sessions_provider ON sso_sessions(provider_id)",
)

# --------------------------------------------------------------------------- #
# 236_rfi_workbench.sql — shadowed by 236_personal_rag.sql
# --------------------------------------------------------------------------- #
# The widest-reaching of the six. Nine runtime modules read these tables
# (tools/govcon/rfi_workbench.py, rfi_demand.py, rfi_engine_runner.py,
# rfi_style_engine.py, capture_strategy.py, evidence_corpus.py,
# rfi_canvas_blueprint.py, tools/iqe/adapters/rfi_canvas.py,
# tools/rag/source_registry.py) and it also cascades: migrations 237, 239, 241,
# 249 and 255 ALTER these tables and every one of them fails on a fresh SQLite
# chain with "no such table: rfi_workbench_sections".
#
# The DEFAULTs the original used (datetime('now')) are SQLite-specific, so the
# NOT NULL timestamp columns are relaxed to plain TEXT here — the writers all
# supply created_at/updated_at explicitly.
_RFI = (
    """
    CREATE TABLE IF NOT EXISTS rfi_workbench_sessions (
        id TEXT PRIMARY KEY,
        rfi_number TEXT,
        rfi_title TEXT,
        profile_name TEXT DEFAULT 'own_company',
        upload_filename TEXT,
        parsed_data TEXT,
        status TEXT NOT NULL DEFAULT 'draft'
            CHECK (status IN ('draft','in_progress','complete','exported')),
        total_sections INTEGER DEFAULT 0,
        approved_sections INTEGER DEFAULT 0,
        created_at TEXT,
        updated_at TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS rfi_workbench_sections (
        id TEXT PRIMARY KEY,
        session_id TEXT NOT NULL REFERENCES rfi_workbench_sessions(id) ON DELETE CASCADE,
        part TEXT NOT NULL,
        item_number TEXT NOT NULL,
        title TEXT NOT NULL,
        topic TEXT,
        question_text TEXT,
        content TEXT DEFAULT '',
        ai_draft TEXT DEFAULT '',
        status TEXT NOT NULL DEFAULT 'pending'
            CHECK (status IN ('pending','ai_draft_ready','hitl_approved','hitl_rejected','accepted')),
        hitl_action TEXT,
        hitl_comment TEXT DEFAULT '',
        writeguard_result TEXT,
        writeguard_score REAL,
        generation_count INTEGER DEFAULT 0,
        created_at TEXT,
        updated_at TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS rfi_workbench_exports (
        id TEXT PRIMARY KEY,
        session_id TEXT NOT NULL REFERENCES rfi_workbench_sessions(id) ON DELETE CASCADE,
        export_format TEXT NOT NULL CHECK (export_format IN ('pdf','docx','md')),
        file_path TEXT,
        exported_at TEXT
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_rfi_sections_session ON rfi_workbench_sections(session_id)",
    "CREATE INDEX IF NOT EXISTS idx_rfi_exports_session ON rfi_workbench_exports(session_id)",
)

# --------------------------------------------------------------------------- #
# Column-level gaps. Present on PostgreSQL via pg_consolidated.sql, absent from
# a fresh SQLite build because init_icdev_db.py's CREATE TABLE never grew them.
# --------------------------------------------------------------------------- #
_COLUMNS = {
    # 113_kanban_vibe_tier1 — shadowed by 113_aadc_compliance.sql
    "kanban_tasks": (
        ("start_date", "TEXT"),
        ("target_date", "TEXT"),
        ("files_changed", "INTEGER DEFAULT 0"),
        ("lines_added", "INTEGER DEFAULT 0"),
        ("lines_removed", "INTEGER DEFAULT 0"),
    ),
    # 055_sg_conflict_events_cyber_op — shadowed by 055_cta_scores_cache.sql
    "sg_conflict_events": (
        ("technique_ids", "TEXT"),
        ("threat_actor", "TEXT"),
        ("malware_family", "TEXT"),
        ("confidence", "REAL"),
    ),
}

_KANBAN_COMMENTS = """
CREATE TABLE IF NOT EXISTS kanban_task_comments (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL,
    author TEXT,
    body TEXT NOT NULL,
    created_at TEXT
)
"""

# 184_memory_fts5 — shadowed by 184_coworkers_canvas_tables.sql.
# FTS5 is a SQLite extension; on PostgreSQL session_indexer.py falls back to
# ILIKE, so there is nothing to create there.
_MEMORY_FTS = (
    "CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts "
    "USING fts5(id UNINDEXED, content, type, tags)"
)


def _is_pg(conn) -> bool:
    return getattr(conn, "_backend", "sqlite") == "postgresql"


def _table_exists(conn, table: str) -> bool:
    try:
        if _is_pg(conn):
            row = conn.execute(
                "SELECT 1 FROM information_schema.tables "
                "WHERE table_schema='public' AND table_name=%s",
                (table,),
            ).fetchone()
        else:
            # pg-portability: sqlite-only branch — no information_schema here.
            row = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                (table,),
            ).fetchone()
        return row is not None
    except Exception:  # noqa: BLE001
        return False


def _columns(conn, table: str) -> set[str]:
    try:
        if _is_pg(conn):
            rows = conn.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema='public' AND table_name=%s",
                (table,),
            ).fetchall()
            return {str(dict(r)["column_name"]).lower() for r in rows}
        # pg-portability: sqlite-only branch.
        return {
            str(r[1]).lower()
            for r in conn.execute(f'PRAGMA table_info("{table}")').fetchall()
        }
    except Exception:  # noqa: BLE001
        return set()


def _widen_role_check(conn, actions: list[str]) -> None:
    """Replace dashboard_users_role_check with the full VALID_DASHBOARD_ROLES set.

    PostgreSQL only. SQLite cannot ALTER a CHECK constraint without rebuilding
    the table, and it does not need to: ``init_icdev_db.py``'s CREATE TABLE
    already carries all fourteen roles, which is exactly why this defect was
    invisible to anyone testing on SQLite.
    """
    if not _is_pg(conn):
        actions.append("role_check: skipped (SQLite gets it from init_icdev_db.py)")
        return
    try:
        row = conn.execute(
            "SELECT pg_get_constraintdef(c.oid) AS def "
            "FROM pg_constraint c JOIN pg_class t ON c.conrelid = t.oid "
            "WHERE t.relname = %s AND c.conname = %s",
            ("dashboard_users", "dashboard_users_role_check"),
        ).fetchone()
        current = dict(row).get("def", "") if row else ""
        if current and all(f"'{r}'" in current for r in _ROLES):
            actions.append("role_check: already current")
            return
        conn.execute(
            "ALTER TABLE dashboard_users "
            "DROP CONSTRAINT IF EXISTS dashboard_users_role_check"
        )
        role_list = ", ".join(f"'{r}'::text" for r in _ROLES)
        conn.execute(
            "ALTER TABLE dashboard_users ADD CONSTRAINT dashboard_users_role_check "
            f"CHECK (role = ANY (ARRAY[{role_list}]))"
        )
        actions.append(f"role_check: widened to {len(_ROLES)} roles")
    except Exception as exc:  # noqa: BLE001
        actions.append(f"role_check: FAILED — {exc}")


def up(conn=None) -> dict:
    from tools.db.storage import get_connection

    own = conn is None
    conn = conn or get_connection()
    actions: list[str] = []
    try:
        if _table_exists(conn, "dashboard_users"):
            _widen_role_check(conn, actions)

        for label, statements in (("sso", _SSO), ("rfi_workbench", _RFI)):
            for stmt in statements:
                try:
                    conn.execute(stmt)
                except Exception as exc:  # noqa: BLE001
                    actions.append(f"{label}: FAILED — {exc}")
            actions.append(f"{label}: ready")

        for table, columns in _COLUMNS.items():
            if not _table_exists(conn, table):
                # The base table is itself absent on this backend
                # (sg_conflict_events is never created on a fresh SQLite build).
                # An ALTER would fail; say so rather than swallow it.
                actions.append(f"{table}: base table absent — columns skipped")
                continue
            have = _columns(conn, table)
            added = []
            for col, defn in columns:
                if col.lower() in have:
                    continue
                try:
                    conn.execute(
                        f'ALTER TABLE {table} ADD COLUMN {col} {defn}'
                    )
                    added.append(col)
                except Exception as exc:  # noqa: BLE001
                    actions.append(f"{table}.{col}: FAILED — {exc}")
            actions.append(f"{table}: added {added}" if added else f"{table}: complete")

        try:
            conn.execute(_KANBAN_COMMENTS)
            actions.append("kanban_task_comments: ready")
        except Exception as exc:  # noqa: BLE001
            actions.append(f"kanban_task_comments: FAILED — {exc}")

        if not _is_pg(conn):
            try:
                conn.execute(_MEMORY_FTS)
                actions.append("memory_fts: ready")
            except Exception as exc:  # noqa: BLE001
                # FTS5 is a compile-time option; an install without it is a
                # degraded search, not a broken migration.
                actions.append(f"memory_fts: unavailable — {exc}")
        else:
            actions.append("memory_fts: skipped (FTS5 is SQLite-only)")

        conn.commit()
        for a in actions:
            print(f"{_TAG} {a}")
        return {"status": "applied", "actions": actions}
    finally:
        if own:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass

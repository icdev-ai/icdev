# CUI // SP-CTI
"""ICDEV Cortex — session state + append-only governance audit persistence.

Two tables back the Cortex governance pipeline (see tools/cortex/governance.py):

  * ``cortex_sessions`` — per-caller Cortex session state (tenant, classification,
    user, domain). Mutable lifecycle (``status`` / ``updated_at``).
  * ``cortex_audit``    — one append-only row per governed Cortex call, the
    compliance backbone (NIST AU) for the platform's unified AI layer. Rows are
    INSERT-only and never UPDATEd/DELETEd; this is additionally enforced at the
    hook layer (``APPEND_ONLY_TABLES`` in ``.claude/hooks/pre_tool_use.py``).

Connection choice — RLS-aware ``get_connection()``, NOT ``get_canvas_connection()``.
    Canvas tables (``aac_*``, ``dsoc_*``, ...) omit ``classification`` / ``tenant_id``
    and therefore MUST use ``get_canvas_connection()`` to skip the global RLS
    predicate. These Cortex tables are the opposite case: they DO carry
    ``tenant_id`` + ``classification`` precisely so governance audit reads are
    row-level filtered by tenant and Bell-LaPadula read-down. Using
    ``get_canvas_connection()`` here would disable that isolation. So every
    connection is opened through the RLS-aware ``tools.db.storage.get_connection()``.

Dual-backend: PostgreSQL is primary (repo is PG-primary); ``SCHEMA_PG`` is the
single source of DDL truth and ``SCHEMA_SQLITE`` is derived from it via
``.replace()`` so the two backends never drift. All runtime writes author
PostgreSQL ``%s`` placeholders (psycopg2 paramstyle); the storage shim rewrites
them to ``?`` on the SQLite init-fallback — never the reverse, never ``?`` at a
runtime call site.
"""
from __future__ import annotations

import json
import os
import sys
import uuid
from types import SimpleNamespace
from typing import Optional

from tools.logging.icdev_logger import get_logger

logger = get_logger("icdev.cortex.db")

# Set once per process after the cortex tables are confirmed to exist, so the
# audit/session writes self-heal on a fresh PostgreSQL bootstrapped from
# pg_consolidated.sql (which predates the cortex migrations 262/263). Without
# this, the first governed call's record_audit INSERT hits a missing table, the
# error is swallowed by governance._gate_record_audit, and the NIST-AU audit row
# is silently lost.
_SCHEMA_ENSURED = False

# Overall audit outcomes, most-to-least severe. Single-sourced here so the
# migration CHECK constraint and the derivation in ``_derive_outcome`` agree.
AUDIT_OUTCOMES = ("blocked", "fail", "warn", "pass")


# --------------------------------------------------------------------------- #
# PostgreSQL DDL (canonical). SQLite is derived from this via .replace() below.
# Kept byte-for-byte in sync with tools/db/migrations/262_cortex_tables.sql.
# --------------------------------------------------------------------------- #
SCHEMA_PG = """
CREATE TABLE IF NOT EXISTS cortex_sessions (
    id              TEXT PRIMARY KEY,
    tenant_id       TEXT NOT NULL DEFAULT 'default',
    classification  TEXT NOT NULL DEFAULT 'CUI',
    user_id         TEXT,
    domain          TEXT,
    air_gap         BOOLEAN NOT NULL DEFAULT FALSE,
    status          TEXT NOT NULL DEFAULT 'active',
    metadata_json   JSONB,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_cortex_sessions_tenant ON cortex_sessions(tenant_id);
CREATE INDEX IF NOT EXISTS idx_cortex_sessions_user ON cortex_sessions(user_id);

CREATE TABLE IF NOT EXISTS cortex_audit (
    id              TEXT PRIMARY KEY,
    session_id      TEXT,
    tenant_id       TEXT NOT NULL DEFAULT 'default',
    classification  TEXT NOT NULL DEFAULT 'CUI',
    function        TEXT NOT NULL DEFAULT 'cortex',
    agent_id        TEXT,
    user_id         TEXT,
    gates_json      JSONB,
    outcome         TEXT NOT NULL DEFAULT 'pass'
        CHECK (outcome IN ('pass', 'warn', 'fail', 'blocked')),
    blocked         BOOLEAN NOT NULL DEFAULT FALSE,
    provenance_id   TEXT,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_cortex_audit_session ON cortex_audit(session_id);
CREATE INDEX IF NOT EXISTS idx_cortex_audit_tenant ON cortex_audit(tenant_id);
CREATE INDEX IF NOT EXISTS idx_cortex_audit_function ON cortex_audit(function);
CREATE INDEX IF NOT EXISTS idx_cortex_audit_created_at ON cortex_audit(created_at);
-- Composite matching the RLS predicate shape (ctx-perf-05, migration
-- 20260814192722_cortex_rls_composite_indexes). get_connection() rewrites every
-- read to carry `AND tenant_id = ?`, so the metrics window query is really
-- tenant_id + created_at and the single-column indexes above can serve only one
-- half of it. classification is deliberately excluded — it arrives as an IN-list
-- and would forfeit the created_at ordering.
CREATE INDEX IF NOT EXISTS idx_cortex_audit_tenant_created ON cortex_audit(tenant_id, created_at);

-- Cortex canvas (chat) tables — the /cortex chat surface's conversation store,
-- distinct from the governance cortex_sessions/cortex_audit above. Deploy DDL:
-- migration 263_cortex_canvas_tables.sql. ``grounded`` is INTEGER (0/1), not
-- BOOLEAN, so the blueprint's best-effort writes never hit the PG int-vs-bool
-- coercion issue.
CREATE TABLE IF NOT EXISTS cortex_chat_sessions (
    session_id      TEXT PRIMARY KEY,
    user_id         TEXT,
    mode            TEXT NOT NULL DEFAULT 'ask',
    domain          TEXT NOT NULL DEFAULT 'general',
    title           TEXT,
    status          TEXT NOT NULL DEFAULT 'active',
    classification  TEXT NOT NULL DEFAULT 'CUI',
    tenant_id       TEXT NOT NULL DEFAULT 'default',
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_cortex_chat_sessions_tenant ON cortex_chat_sessions(tenant_id);
CREATE INDEX IF NOT EXISTS idx_cortex_chat_sessions_user ON cortex_chat_sessions(user_id);
CREATE INDEX IF NOT EXISTS idx_cortex_chat_sessions_tenant_created ON cortex_chat_sessions(tenant_id, created_at);
CREATE TABLE IF NOT EXISTS cortex_messages (
    message_id      TEXT PRIMARY KEY,
    session_id      TEXT,
    turn_number     INTEGER NOT NULL DEFAULT 0,
    role            TEXT NOT NULL DEFAULT 'user',
    content         TEXT,
    facade          TEXT,
    grounded        INTEGER NOT NULL DEFAULT 0,
    confidence      TEXT,
    citations       TEXT,
    governance      TEXT,
    classification  TEXT NOT NULL DEFAULT 'CUI',
    tenant_id       TEXT NOT NULL DEFAULT 'default',
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_cortex_messages_session ON cortex_messages(session_id, turn_number);
CREATE INDEX IF NOT EXISTS idx_cortex_messages_tenant_session_turn ON cortex_messages(tenant_id, session_id, turn_number);
CREATE TABLE IF NOT EXISTS cortex_search_history (
    query_id        TEXT PRIMARY KEY,
    session_id      TEXT,
    user_id         TEXT,
    mode            TEXT NOT NULL DEFAULT 'search',
    domain          TEXT NOT NULL DEFAULT 'general',
    query_text      TEXT,
    strategy        TEXT,
    result_count    INTEGER NOT NULL DEFAULT 0,
    grounded        INTEGER NOT NULL DEFAULT 0,
    classification  TEXT NOT NULL DEFAULT 'CUI',
    tenant_id       TEXT NOT NULL DEFAULT 'default',
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_cortex_search_history_session ON cortex_search_history(session_id);
CREATE INDEX IF NOT EXISTS idx_cortex_search_history_tenant_created ON cortex_search_history(tenant_id, created_at);
"""


# SQLite-flavored DDL — derived from SCHEMA_PG so the two backends never drift.
SCHEMA_SQLITE = (
    SCHEMA_PG
    .replace("JSONB", "TEXT")
    .replace("TIMESTAMP DEFAULT CURRENT_TIMESTAMP", "TEXT DEFAULT CURRENT_TIMESTAMP")
    .replace("BOOLEAN NOT NULL DEFAULT FALSE", "INTEGER NOT NULL DEFAULT 0")
)


def _backend_of(conn) -> str:
    """Resolve the backend ('postgresql' | 'sqlite') of a live connection.

    Prefer the connection's own declared backend so the executed schema can never
    mismatch the connection; fall back to the env var (default 'postgresql').
    """
    declared = getattr(conn, "_backend", None)
    if declared:
        return "postgresql" if str(declared).lower().startswith(("postgre", "pg")) else "sqlite"
    backend = os.environ.get("ICDEV_STORAGE_BACKEND", "postgresql").lower()
    return "postgresql" if backend in ("postgresql", "postgres", "pg") else "sqlite"


def init_db(conn=None) -> None:
    """Create the Cortex session + audit tables. Idempotent and graceful if the
    schema already exists.

    Pass ``conn`` to reuse an existing connection (e.g. tests); otherwise an
    RLS-aware connection is opened via ``get_connection()`` and closed on exit.
    """
    from tools.db.storage import get_connection

    own_conn = conn is None
    if own_conn:
        conn = get_connection()
    try:
        schema = SCHEMA_PG if _backend_of(conn) == "postgresql" else SCHEMA_SQLITE
        cur = conn.cursor()
        for stmt in schema.split(";"):
            stmt = stmt.strip()
            if stmt:
                cur.execute(stmt)
        conn.commit()
    finally:
        if own_conn:
            conn.close()


# --------------------------------------------------------------------------- #
# Write helpers — RLS-aware, %s placeholders, INSERT-only for the audit table.
# --------------------------------------------------------------------------- #
def _ensure_schema(conn) -> None:
    """Idempotently create the cortex tables once per process before a write.

    Guards against silent audit loss on a fresh PostgreSQL that was bootstrapped
    from pg_consolidated.sql (no cortex tables) and never ran migration 262/263.
    init_db() is CREATE ... IF NOT EXISTS, so the steady-state cost is one no-op
    DDL pass on the first write; thereafter the module flag short-circuits it.
    Best-effort: a failure here does not mask the subsequent insert's own error.
    """
    global _SCHEMA_ENSURED
    if _SCHEMA_ENSURED:
        return
    try:
        init_db(conn)
        _SCHEMA_ENSURED = True
    except Exception as exc:  # noqa: BLE001
        logger.debug("cortex _ensure_schema best-effort init failed: %s", exc)


def _derive_outcome(payload: dict) -> str:
    """Collapse a governance report into one overall audit outcome.

    ``blocked`` dominates; otherwise the worst per-gate outcome wins
    (fail > warn > pass). ``skip`` is not an overall outcome — it degrades to
    ``pass`` since a skipped gate is a deliberate, observable no-op.
    """
    if payload.get("blocked"):
        return "blocked"
    values = set((payload.get("outcomes") or {}).values())
    if "fail" in values:
        return "fail"
    if "warn" in values:
        return "warn"
    return "pass"


def ensure_session(ctx, conn=None) -> Optional[str]:
    """Persist a ``cortex_sessions`` row for ``ctx`` if it carries a session id.

    Returns the session id (no-op returns None when ``ctx`` has no ``session_id``).
    Idempotent: an existing session id is left untouched (INSERT OR IGNORE ->
    ON CONFLICT DO NOTHING on PG). Best-effort — governance must never fail
    because session bookkeeping did.
    """
    session_id = getattr(ctx, "session_id", "") or ""
    if not session_id:
        return None

    own_conn = conn is None
    if own_conn:
        from tools.db.storage import get_connection
        conn = get_connection()
    try:
        _ensure_schema(conn)
        conn.execute(
            "INSERT OR IGNORE INTO cortex_sessions "
            "(id, tenant_id, classification, user_id, domain, air_gap, status) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s)",
            (
                session_id,
                getattr(ctx, "tenant_id", "") or "default",
                getattr(ctx, "classification", "") or "CUI",
                getattr(ctx, "user_id", "") or None,
                getattr(ctx, "domain", "") or None,
                # Python bool — psycopg2 adapts to PG boolean, sqlite3 to 0/1.
                # Passing int 1/0 fails on PG (air_gap is BOOLEAN).
                bool(getattr(ctx, "air_gap", False)),
                "active",
            ),
        )
        conn.commit()
        return session_id
    finally:
        if own_conn:
            conn.close()


def record_audit(payload: dict, conn=None) -> str:
    """Insert one append-only ``cortex_audit`` row from a governance payload.

    ``payload`` is the exact dict the governance pipeline assembles in
    ``GovernancePipeline._audit``. Writes are INSERT-only (NIST AU); the row id
    is the pipeline's ``record_id`` when present, else a fresh uuid.

    Returns the audit row id. Raises on a hard DB error so the caller
    (``governance._gate_record_audit``, itself wrapped in a best-effort guard)
    can log it — the audit write must never silently vanish.
    """
    audit_id = payload.get("record_id") or f"cgov-{uuid.uuid4().hex[:16]}"

    own_conn = conn is None
    if own_conn:
        from tools.db.storage import get_connection
        conn = get_connection()
    try:
        _ensure_schema(conn)
        conn.execute(
            "INSERT INTO cortex_audit "
            "(id, session_id, tenant_id, classification, function, agent_id, "
            " user_id, gates_json, outcome, blocked, provenance_id) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (
                audit_id,
                payload.get("session_id") or None,
                payload.get("tenant_id") or "default",
                payload.get("classification") or "CUI",
                payload.get("operation") or "cortex",
                payload.get("agent_id") or None,
                payload.get("user_id") or None,
                json.dumps(
                    {
                        # Which governance profile ran (hgx-gov-01). "default"
                        # is the full chain; a narrower one shows up as `skip`
                        # outcomes below, and this names which one it was.
                        "profile": payload.get("profile") or "default",
                        "gates_run": payload.get("gates_run") or [],
                        "outcomes": payload.get("outcomes") or {},
                        "redactions_applied": payload.get("redactions_applied", 0),
                        "blocked_gate": payload.get("blocked_gate") or "",
                        "blocked_reason": payload.get("blocked_reason") or "",
                        # Accounting for /cortex/metrics observability (no dedicated
                        # audit columns for these — see GovernancePipeline._audit).
                        "domain": payload.get("domain") or "",
                        "cost_usd": float(payload.get("cost_usd") or 0.0),
                        "latency_ms": int(payload.get("latency_ms") or 0),
                        "input_tokens": int(payload.get("input_tokens") or 0),
                        "output_tokens": int(payload.get("output_tokens") or 0),
                        "provider": payload.get("provider") or "",
                        "model": payload.get("model") or "",
                        "cache_hit": bool(payload.get("cache_hit")),
                        # Wall-clock cost of the governance chain itself
                        # (ctx-obs-02). latency_ms above times the LLM call
                        # ONLY, so these are what make gate overhead derivable:
                        # total_ms - operation_ms = governance_ms. Absent (0)
                        # on rows written before ctx-obs-02 and on the
                        # cache-hit path, which never enters the pipeline —
                        # /cortex/metrics reads 0 as "not measured", never as
                        # "free".
                        "total_ms": float(payload.get("total_ms") or 0.0),
                        "operation_ms": float(payload.get("operation_ms") or 0.0),
                        "governance_ms": float(payload.get("governance_ms") or 0.0),
                        "gate_ms": payload.get("gate_ms") or {},
                    },
                    default=str,
                ),
                _derive_outcome(payload),
                # Python bool — psycopg2 adapts to PG boolean, sqlite3 to 0/1.
                # Passing int 1/0 fails on PG (blocked is BOOLEAN).
                bool(payload.get("blocked")),
                payload.get("provenance_id") or None,
            ),
        )
        conn.commit()
        return audit_id
    finally:
        if own_conn:
            conn.close()


def _session_ctx_from_payload(payload: dict):
    """Adapt a governance audit payload to the attribute shape ensure_session reads.

    The governed hot path assembles a dict, not a CortexContext; ``ensure_session``
    reads its fields via getattr. This keeps the ``_gate_record_audit(payload)``
    seam single-argument so the eight existing test doubles for it stay valid.
    """
    return SimpleNamespace(
        session_id=payload.get("session_id") or "",
        tenant_id=payload.get("tenant_id") or "default",
        classification=payload.get("classification") or "CUI",
        user_id=payload.get("user_id") or None,
        domain=payload.get("domain") or None,
        air_gap=bool(payload.get("air_gap", False)),
    )


def record_governed_call(payload: dict, conn=None) -> str:
    """Persist one governed Cortex call's session + audit rows over ONE connection.

    ``ensure_session`` and ``record_audit`` each open, commit and close their own
    connection when called standalone. The governed hot path needs both, and the
    cache-hit path needs the audit row too, so opening a single connection here
    and threading it through both collapses the per-call DB round-trips to one.

    Session bookkeeping is best-effort and is rolled back on failure: on
    PostgreSQL a failed statement poisons the whole transaction, so without the
    rollback a broken session write would take the append-only ``cortex_audit``
    INSERT down with it. The audit row is the compliance artifact and wins.

    Returns the audit row id. Raises on a hard audit-write error so the caller's
    best-effort guard can log it — the audit write must never silently vanish.
    """
    own_conn = conn is None
    if own_conn:
        from tools.db.storage import get_connection
        conn = get_connection()
    try:
        if payload.get("session_id"):
            try:
                ensure_session(_session_ctx_from_payload(payload), conn=conn)
            except Exception as exc:  # noqa: BLE001 — audit row must still land
                logger.warning("cortex session bookkeeping failed: %s", exc)
                try:
                    conn.rollback()
                except Exception:  # noqa: BLE001 — rollback is itself best-effort
                    pass
        return record_audit(payload, conn=conn)
    finally:
        if own_conn:
            conn.close()


if __name__ == "__main__":
    init_db()
    print("[init_db] Cortex session + audit schema ready", file=sys.stderr)

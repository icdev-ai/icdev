# CUI // SP-CTI
"""Tests for Cortex session + append-only governance audit persistence (ctx-govern-03).

Runs under conftest, which forces ICDEV_STORAGE_BACKEND=sqlite for unit
isolation. The PRIMARY acceptance target is PostgreSQL — see
tools/cortex/db/verify_pg.py, run against the live .env PG database; these tests
prove the code path (schema, insert-only writes, outcome derivation, RLS
predicate wiring, one-row-per-governed-call) on the SQLite fallback.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from tools.cortex.db.init_db import (
    AUDIT_OUTCOMES,
    SCHEMA_PG,
    SCHEMA_SQLITE,
    _derive_outcome,
    ensure_session,
    init_db,
    record_audit,
)
from tools.cortex.schemas import CortexContext

_REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def cortex_db(tmp_path, monkeypatch):
    """Point get_connection() at a fresh temp SQLite DB with the Cortex tables."""
    db_path = tmp_path / "cortex.db"
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("ICDEV_DB_PATH", str(db_path))
    init_db()
    return db_path


def _fetch_audit_rows():
    from tools.db.storage import get_connection

    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT id, session_id, tenant_id, classification, function, "
            "agent_id, user_id, gates_json, outcome, blocked, provenance_id "
            "FROM cortex_audit ORDER BY created_at"
        )
        cols = [
            "id", "session_id", "tenant_id", "classification", "function",
            "agent_id", "user_id", "gates_json", "outcome", "blocked", "provenance_id",
        ]
        return [dict(zip(cols, row)) for row in cur.fetchall()]
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------
def test_init_db_creates_both_tables(cortex_db):
    from tools.db.storage import get_connection

    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name IN ('cortex_sessions', 'cortex_audit')"
        )
        names = {row[0] for row in cur.fetchall()}
    finally:
        conn.close()
    assert names == {"cortex_sessions", "cortex_audit"}


def test_init_db_is_idempotent(cortex_db):
    init_db()  # second call must not raise
    init_db()


def test_schema_sqlite_is_derived_from_pg():
    # SQLite flavor must not leak PG-only DDL types (the shim never translates DDL).
    assert "JSONB" not in SCHEMA_SQLITE
    assert "BOOLEAN NOT NULL DEFAULT FALSE" not in SCHEMA_SQLITE
    # PG canonical keeps them.
    assert "JSONB" in SCHEMA_PG


# ---------------------------------------------------------------------------
# Outcome derivation
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "payload,expected",
    [
        ({"blocked": True, "outcomes": {"pre_check": "fail"}}, "blocked"),
        ({"blocked": False, "outcomes": {"operation": "fail"}}, "fail"),
        ({"blocked": False, "outcomes": {"citation_grounding": "warn"}}, "warn"),
        ({"blocked": False, "outcomes": {"provenance": "pass"}}, "pass"),
        ({"blocked": False, "outcomes": {"citation_grounding": "skip"}}, "pass"),
        ({}, "pass"),
    ],
)
def test_derive_outcome(payload, expected):
    assert _derive_outcome(payload) == expected
    assert expected in AUDIT_OUTCOMES


# ---------------------------------------------------------------------------
# record_audit
# ---------------------------------------------------------------------------
def test_record_audit_inserts_one_row_with_expected_shape(cortex_db):
    audit_id = record_audit(
        {
            "record_id": "cgov-abc123",
            "operation": "cortex.complete",
            "agent_id": "cortex",
            "session_id": "sess-1",
            "tenant_id": "tenant-a",
            "user_id": "u1",
            "classification": "CUI",
            "blocked": False,
            "gates_run": ["pre_check", "provenance"],
            "outcomes": {"pre_check": "pass", "provenance": "pass"},
            "redactions_applied": 2,
            "provenance_id": "scr-999",
        }
    )
    assert audit_id == "cgov-abc123"

    rows = _fetch_audit_rows()
    assert len(rows) == 1
    row = rows[0]
    assert row["id"] == "cgov-abc123"
    assert row["session_id"] == "sess-1"
    assert row["tenant_id"] == "tenant-a"
    assert row["function"] == "cortex.complete"
    assert row["outcome"] == "pass"
    assert row["provenance_id"] == "scr-999"
    assert int(row["blocked"]) == 0
    gates = json.loads(row["gates_json"])
    assert gates["redactions_applied"] == 2
    assert gates["outcomes"]["provenance"] == "pass"


def test_record_audit_generates_id_when_absent(cortex_db):
    audit_id = record_audit({"operation": "cortex.classify", "tenant_id": "t"})
    assert audit_id.startswith("cgov-")
    assert len(_fetch_audit_rows()) == 1


def test_record_audit_is_insert_only_distinct_rows(cortex_db):
    record_audit({"record_id": "cgov-1", "operation": "cortex.a", "tenant_id": "t"})
    record_audit({"record_id": "cgov-2", "operation": "cortex.b", "tenant_id": "t"})
    rows = _fetch_audit_rows()
    assert [r["id"] for r in rows] == ["cgov-1", "cgov-2"]


def test_record_audit_blocked_payload_persists_blocked_outcome(cortex_db):
    record_audit(
        {
            "record_id": "cgov-blk",
            "operation": "cortex.complete",
            "tenant_id": "t",
            "blocked": True,
            "blocked_gate": "pre_check",
            "blocked_reason": "injection",
            "outcomes": {"pre_check": "fail"},
        }
    )
    row = _fetch_audit_rows()[0]
    assert row["outcome"] == "blocked"
    assert int(row["blocked"]) == 1
    assert json.loads(row["gates_json"])["blocked_reason"] == "injection"


# ---------------------------------------------------------------------------
# ensure_session
# ---------------------------------------------------------------------------
def test_ensure_session_persists_and_is_idempotent(cortex_db):
    from tools.db.storage import get_connection

    ctx = CortexContext(session_id="sess-x", tenant_id="tenant-a", user_id="u1", domain="rfi")
    assert ensure_session(ctx) == "sess-x"
    assert ensure_session(ctx) == "sess-x"  # idempotent — no duplicate/raise

    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute("SELECT tenant_id, user_id, domain FROM cortex_sessions WHERE id = %s", ("sess-x",))
        rows = cur.fetchall()
    finally:
        conn.close()
    assert len(rows) == 1


def test_ensure_session_noop_without_session_id(cortex_db):
    assert ensure_session(CortexContext(tenant_id="t")) is None


# ---------------------------------------------------------------------------
# RLS predicate wiring (SQLite proxy for the PG acceptance gate)
# ---------------------------------------------------------------------------
def test_rls_predicate_isolates_tenants(cortex_db):
    from tools.db.storage import get_connection
    from tools.security.security_context import SecurityContext

    record_audit({"record_id": "cgov-a", "operation": "cortex.x", "tenant_id": "tenant-a"})
    record_audit({"record_id": "cgov-b", "operation": "cortex.x", "tenant_id": "tenant-b"})

    conn = get_connection()
    try:
        conn.set_security_context(SecurityContext(tenant_id="tenant-a", classification="CUI"))
        cur = conn.cursor()
        cur.execute("SELECT id FROM cortex_audit")
        ids = {row[0] for row in cur.fetchall()}
    finally:
        conn.close()
    assert "cgov-a" in ids
    assert "cgov-b" not in ids


# ---------------------------------------------------------------------------
# Append-only hook enforcement
# ---------------------------------------------------------------------------
def _load_hook():
    hook_path = _REPO_ROOT / ".claude" / "hooks" / "pre_tool_use.py"
    spec = importlib.util.spec_from_file_location("pre_tool_use_hook", str(hook_path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_hook_blocks_update_and_delete_on_cortex_audit():
    hook = _load_hook()
    assert hook.is_append_only_table_modification(
        "Bash", {"command": "sqlite3 x.db 'DELETE FROM cortex_audit WHERE id = 1'"}
    )
    assert hook.is_append_only_table_modification(
        "Bash", {"command": "psql -c 'UPDATE cortex_audit SET outcome=1'"}
    )


def test_hook_allows_mutating_cortex_sessions():
    hook = _load_hook()
    # cortex_sessions has a mutable lifecycle — it is deliberately NOT append-only.
    assert not hook.is_append_only_table_modification(
        "Bash", {"command": "psql -c 'UPDATE cortex_sessions SET status = 2'"}
    )


# ---------------------------------------------------------------------------
# End-to-end: one audit row per governed pipeline call
# ---------------------------------------------------------------------------
def test_pipeline_run_writes_one_cortex_audit_row(cortex_db, monkeypatch):
    from tools.cortex import governance
    from tools.cortex.governance import GovernancePipeline

    # Patch the heavy gate seams (gateway, anonymizer, provenance registry) but
    # leave _gate_record_audit REAL so the DB write is exercised end-to-end.
    monkeypatch.setattr(
        governance, "_gate_check_text",
        lambda text: {"allowed": True, "warnings": [], "blocked_reason": None},
    )
    monkeypatch.setattr(governance, "_gate_redact_input", lambda text, cls: (text, 0))
    monkeypatch.setattr(governance, "_gate_redact_output", lambda text: (text, []))
    monkeypatch.setattr(
        governance, "_gate_register_provenance",
        lambda output_text, ctx, operation, record_id: "scr-e2e",
    )

    ctx = CortexContext(session_id="sess-e2e", tenant_id="tenant-a", user_id="u1")
    result, report = GovernancePipeline(operation="cortex.complete").wrap(
        lambda p: "plain completion", ctx, prompt="q", retrieval=False
    )
    assert result == "plain completion"
    assert not report.blocked

    rows = _fetch_audit_rows()
    assert len(rows) == 1, "exactly one audit row per governed call"
    row = rows[0]
    assert row["function"] == "cortex.complete"
    assert row["session_id"] == "sess-e2e"
    assert row["tenant_id"] == "tenant-a"
    assert row["provenance_id"] == "scr-e2e"
    assert row["outcome"] == "pass"

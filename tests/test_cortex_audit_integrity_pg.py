# CUI // SP-CTI — cortex_audit chain and Cortex provenance on the AMBIENT backend (icx-cnf-06)
"""The same guarantees as tests/test_cortex_audit_integrity.py, on whatever the
process database is. In the PG tier (ICDEV_PYTEST_PG=1, tests/pg_tier_allowlist.txt)
that is PostgreSQL: JSONB ``gates_json``, TIMESTAMP ``created_at``, BOOLEAN
``blocked``, PL/pgSQL triggers and the advisory chain lock. In the normal suite it
is the conftest SQLite database.

No fixture here redirects the backend or opens a raw connection. The database
may be shared with other tests, so nothing asserts the whole chain is intact:
what is asserted is about the rows this file writes.
"""
from __future__ import annotations

import json
import uuid

import pytest

from tools.cortex import governance
from tools.cortex.db import audit_chain
from tools.cortex.db.init_db import init_db
from tools.cortex.schemas import CortexContext, CortexResult
from tools.db.storage import get_connection, is_pg


@pytest.fixture
def chained():
    """The cortex tables, the registry and the chain on the ambient database."""
    conn = get_connection()
    try:
        init_db(conn)
        audit_chain.ensure_chain(conn)
    finally:
        conn.close()


@pytest.fixture
def govern(monkeypatch, chained):
    monkeypatch.setattr(governance, "_gate_check_text",
                        lambda text: {"allowed": True, "warnings": []})
    monkeypatch.setattr(governance, "_gate_redact_input", lambda text, c: (text, 0))
    monkeypatch.setattr(governance, "_gate_redact_output", lambda text: (text, []))
    tenant = f"cnf06-{uuid.uuid4().hex[:8]}"

    def _run():
        _result, report = governance.GovernancePipeline(operation="cortex.complete").wrap(
            lambda prompt: CortexResult(text="Accounts are disabled after 90 days."),
            CortexContext(tenant_id=tenant), prompt="when?", retrieval=False)
        return report

    _run.tenant = tenant
    return _run


def _rows(tenant):
    cols = audit_chain.HASH_FIELDS + ("chain_seq", "prev_hash", "row_hash")
    conn = get_connection()
    try:
        cur = audit_chain._cursor(conn)
        cur.execute(f"SELECT {', '.join(cols)} FROM cortex_audit WHERE tenant_id = %s "
                    "ORDER BY chain_seq", (tenant,))
        return [dict(zip(cols, tuple(r))) for r in cur.fetchall()]
    finally:
        conn.close()


def test_governed_calls_land_provenance_and_chain_on_this_backend(govern):
    for _ in range(3):
        assert govern().outcomes["provenance"] == "pass"
    rows = _rows(govern.tenant)
    assert len(rows) == 3
    for r in rows:
        assert r["chain_seq"] is not None
        # read back from the database, the row still hashes to what was written
        assert audit_chain.link_hash(r, int(r["chain_seq"]), r["prev_hash"]) == r["row_hash"]
        gates = r["gates_json"] if isinstance(r["gates_json"], dict) else json.loads(
            r["gates_json"])
        assert gates["outcomes"]["provenance"] == "pass"
    seqs = [int(r["chain_seq"]) for r in rows]
    assert seqs == sorted(seqs) and len(set(seqs)) == 3
    conn = get_connection()
    try:
        cur = audit_chain._cursor(conn)
        for r in rows:
            cur.execute("SELECT source_record_id, citation_type FROM source_citation_registry "
                        "WHERE id = %s", (r["provenance_id"],))
            assert tuple(cur.fetchone()) == (r["id"], "cortex")
    finally:
        conn.close()


@pytest.mark.parametrize("sql", [
    "UPDATE cortex_audit SET outcome = 'pass' WHERE tenant_id = %s",
    "DELETE FROM cortex_audit WHERE tenant_id = %s",
])
def test_the_database_refuses_update_and_delete(govern, sql):
    govern()
    conn = get_connection()
    try:
        with pytest.raises(Exception, match="append-only"):
            conn.execute(sql, (govern.tenant,))
            conn.commit()
        conn.rollback()
    finally:
        conn.close()
    assert len(_rows(govern.tenant)) == 1


def test_ensure_chain_is_idempotent(chained):
    conn = get_connection()
    try:
        before = audit_chain.genesis(conn)
        out = audit_chain.ensure_chain(conn)
        assert out["columns_added"] == [] and out["triggers_installed"] == []
        assert audit_chain.genesis(conn)["id"] == before["id"]
    finally:
        conn.close()


def test_verify_sees_a_row_altered_inside_a_rolled_back_transaction(govern):
    """DDL is transactional on both backends: drop the trigger, alter a row, verify, roll back."""
    govern()
    govern()
    target = _rows(govern.tenant)[0]["id"]
    conn = get_connection()
    try:
        before = audit_chain.verify(conn)["break_count"]
        if is_pg(conn):
            conn.execute("DROP TRIGGER cortex_audit_no_update ON cortex_audit")
        else:
            conn.execute("BEGIN")  # sqlite3 runs bare DDL outside a transaction
            conn.execute("DROP TRIGGER cortex_audit_no_update")
        conn.execute("UPDATE cortex_audit SET outcome = 'fail' WHERE id = %s", (target,))
        v = audit_chain.verify(conn)
        assert v["status"] == "broken" and v["break_count"] == before + 1
    finally:
        conn.rollback()
        conn.close()
    conn = get_connection()
    try:  # the rollback restored the trigger and the row
        assert "cortex_audit_no_update" in audit_chain._triggers_present(conn, "cortex_audit")
        assert audit_chain.verify(conn)["break_count"] == before
    finally:
        conn.close()

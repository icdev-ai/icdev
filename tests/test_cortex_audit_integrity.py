# CUI // SP-CTI — cortex_audit integrity and Cortex provenance (icx-cnf-06)
"""Every governed Cortex call lands its provenance; cortex_audit is chained and guarded.

The cortex governance twin's first reading on a scratch copy of a real database
found 52 of 57 governed calls with no provenance record, no hash chain and no
database trigger on ``cortex_audit``. What is pinned here, against throwaway
SQLite files only:

* the root cause: the registry wrote to this checkout's ``data/icdev.db``
  whatever ``ICDEV_DB_PATH`` said, and that database had no registry table. A
  governed call now lands its row in the SAME database as its audit row, the
  two join both ways, and when the write does fail the reason is on the row;
* the chain: rows link (seq, prev_hash, row_hash), the old rows are never
  rewritten and a genesis covers them, and ``verify`` catches an altered row,
  a removed row, an altered pre-chain row and a row written around the chain;
* the triggers refuse UPDATE and DELETE;
* the twin's "no provenance", "unchained" and "DB mutable" findings clear once
  fixed, and come back when broken.
"""
from __future__ import annotations

import importlib.util
import json
import sqlite3
from pathlib import Path

import pytest

from tools.cortex import governance
from tools.cortex.db import audit_chain
from tools.cortex.schemas import CortexContext, CortexResult

REPO = Path(__file__).resolve().parents[1]
MIGRATION = REPO / "tools/db/migrations/20261114175314_cortex_audit_hash_chain"
cortex_db = importlib.import_module("tools.cortex.db.init_db")
registry = importlib.import_module("tools.provenance.registry")


@pytest.fixture
def db(tmp_path, monkeypatch):
    """An empty SQLite file as the process database; the schema latch reset."""
    path = tmp_path / "icdev.db"
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("ICDEV_DB_PATH", str(path))
    monkeypatch.delenv("ICDEV_DATABASE_URL", raising=False)
    monkeypatch.setattr(cortex_db, "_SCHEMA_ENSURED", False)
    monkeypatch.setattr(audit_chain, "_HAS_CHAIN", {})
    from tools.twin_core.registry import TwinRegistry

    TwinRegistry.discover(force=True)
    return path


def _sql(path, sql, params=()):
    conn = sqlite3.connect(str(path))
    try:
        rows = conn.execute(sql, params).fetchall()
        conn.commit()
        return rows
    finally:
        conn.close()


def _conn():
    from tools.db.storage import get_connection

    return get_connection()


def _legacy_table(path, n=3):
    """cortex_audit as it was before this card (no chain, no triggers), with rows."""
    conn = sqlite3.connect(str(path))
    try:
        conn.executescript(cortex_db.SCHEMA_SQLITE)
        for i in range(n):
            conn.execute(
                "INSERT INTO cortex_audit (id, outcome, gates_json, created_at) "
                "VALUES (?, 'warn', ?, ?)",
                (f"cgov-old{i}", json.dumps({"outcomes": {"operation": "pass",
                                                          "provenance": "warn"}}),
                 f"2026-10-12 09:0{i}:00"))
        conn.commit()
    finally:
        conn.close()


def _migrate():
    spec = importlib.util.spec_from_file_location("m_up", str(MIGRATION / "up.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    conn = _conn()
    try:
        return mod.up(conn)
    finally:
        conn.close()


def _verify():
    conn = _conn()
    try:
        return audit_chain.verify(conn)
    finally:
        conn.close()


@pytest.fixture
def govern(monkeypatch):
    """One real governed call: gates 1-6 faked, gates 7a and 7b real."""
    monkeypatch.setattr(governance, "_gate_check_text",
                        lambda text: {"allowed": True, "warnings": []})
    monkeypatch.setattr(governance, "_gate_redact_input", lambda text, c: (text, 0))
    monkeypatch.setattr(governance, "_gate_redact_output", lambda text: (text, []))

    def _run(text="Accounts are disabled after 90 days."):
        _result, report = governance.GovernancePipeline(operation="cortex.complete").wrap(
            lambda prompt: CortexResult(text=text), CortexContext(tenant_id="default"),
            prompt="when?", retrieval=False)
        return report

    return _run


def _audit():
    cols = ("id", "provenance_id", "gates_json", "chain_seq", "prev_hash", "row_hash")
    conn = _conn()
    try:
        cur = conn.cursor()
        cur.execute(f"SELECT {', '.join(cols)} FROM cortex_audit "
                    "WHERE chain_seq IS NOT NULL ORDER BY chain_seq")
        return [dict(zip(cols, tuple(r))) for r in cur.fetchall()]
    finally:
        conn.close()


def _cortex_rules():
    from tools.twin_core import governance as gov

    env = gov.observe(gov.adapter_for("cortex"), record=False)
    return {v["rule_id"]: v for v in env["violations"]}


# ── provenance: the root cause ───────────────────────────────────────────────


def test_provenance_lands_in_the_process_database_not_the_checkout_file(
        db, govern, tmp_path, monkeypatch):
    # The pre-fix default: a different file from ICDEV_DB_PATH, with no table.
    elsewhere = tmp_path / "checkout" / "icdev.db"
    monkeypatch.setattr(registry, "DB_PATH", elsewhere)
    report = govern()
    assert report.outcomes["provenance"] == "pass"
    assert not elsewhere.exists(), "the registry opened a database the process does not use"
    (row,) = _audit()
    reg = _sql(db, "SELECT id, source_record_id, citation_type FROM source_citation_registry")
    assert reg == [(row["provenance_id"], row["id"], "cortex")], \
        "the citation and the audit row must join both ways, in one database"


def test_a_database_without_the_registry_table_gets_one(db, govern):
    cortex_db.init_db()
    _sql(db, "DROP TABLE source_citation_registry")
    cortex_db._SCHEMA_ENSURED = False
    assert govern().outcomes["provenance"] == "pass"
    assert _sql(db, "SELECT COUNT(*) FROM source_citation_registry") == [(1,)]


def test_a_failed_registry_write_says_why_on_the_audit_row(db, govern, monkeypatch):
    def boom(**kwargs):
        raise RuntimeError("registry offline")

    monkeypatch.setattr(registry, "register_citation", boom)
    report = govern()
    assert report.outcomes["provenance"] == "warn"
    (row,) = _audit()
    assert row["provenance_id"] is None
    assert json.loads(row["gates_json"])["provenance_detail"] == \
        "RuntimeError: registry offline"


def test_no_provenance_finding_clears_when_fixed_and_refires_when_broken(
        db, govern, monkeypatch):
    for _ in range(2):
        govern()
    assert "cortex.audit.no_provenance" not in _cortex_rules()

    monkeypatch.setattr(registry, "register_citation",
                        lambda **kw: (_ for _ in ()).throw(RuntimeError("disk full")))
    govern()
    v = _cortex_rules()["cortex.audit.no_provenance"]
    assert v["title"].startswith("1 unblocked governed call")
    newest = _audit()[-1]["id"]
    assert v["thread_id"] == f"cortex_audit:{newest}"
    assert "RuntimeError: disk full" in v["detail"]


# ── the chain ────────────────────────────────────────────────────────────────


def test_migration_starts_the_chain_after_the_old_rows_without_rewriting_them(db, govern):
    _legacy_table(db)
    before = _sql(db, "SELECT id, outcome, gates_json, created_at FROM cortex_audit ORDER BY id")
    out = _migrate()
    assert out["columns_added"] == ["chain_seq", "prev_hash", "row_hash"]
    assert set(out["triggers_installed"]) == {"cortex_audit", "cortex_audit_chain_genesis"}
    assert _sql(db, "SELECT id, outcome, gates_json, created_at FROM cortex_audit "
                    "ORDER BY id") == before
    assert _sql(db, "SELECT COUNT(*) FROM cortex_audit WHERE chain_seq IS NOT NULL") == [(0,)]
    ((prior, gid),) = _sql(db, "SELECT prior_rows, id FROM cortex_audit_chain_genesis")
    assert prior == 3
    assert _migrate()["columns_added"] == []  # idempotent
    assert _sql(db, "SELECT COUNT(*) FROM cortex_audit_chain_genesis") == [(1,)]

    for _ in range(3):
        govern()
    rows = _audit()
    assert [r["chain_seq"] for r in rows] == [1, 2, 3]
    ((ghash,),) = _sql(db, "SELECT genesis_hash FROM cortex_audit_chain_genesis")
    assert rows[0]["prev_hash"] == ghash
    assert [r["prev_hash"] for r in rows[1:]] == [r["row_hash"] for r in rows[:-1]]
    v = _verify()
    assert v["status"] == "intact" and v["genesis_id"] == gid
    assert v["chained_rows"] == 3 and v["prior"] == {"recorded_rows": 3, "found_rows": 3,
                                                     "intact": True}


def test_a_fresh_database_chains_from_the_first_write(db, govern):
    govern()
    v = _verify()
    assert v["status"] == "intact" and v["chained_rows"] == 1 and v["unchained_rows"] == 0


def test_triggers_refuse_update_and_delete(db, govern):
    govern()
    for sql in ("UPDATE cortex_audit SET outcome = 'pass'", "DELETE FROM cortex_audit",
                "UPDATE cortex_audit_chain_genesis SET prior_rows = 0",
                "DELETE FROM cortex_audit_chain_genesis"):
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            _sql(db, sql)


@pytest.mark.parametrize("tamper, reason", [
    ("UPDATE cortex_audit SET outcome = 'fail' WHERE chain_seq = 2", "altered"),
    ("DELETE FROM cortex_audit WHERE chain_seq = 2", "a row is missing"),
    ("UPDATE cortex_audit SET outcome = 'fail' WHERE id = 'cgov-old1'",
     "rows written before the chain were altered or removed"),
    ("INSERT INTO cortex_audit (id, outcome) VALUES ('cgov-around', 'pass')",
     "written without the chain"),
])
def test_verify_catches_every_kind_of_tampering(db, govern, tamper, reason):
    _legacy_table(db)
    _migrate()
    for _ in range(3):
        govern()
    assert _verify()["status"] == "intact"
    _sql(db, "DROP TRIGGER cortex_audit_no_update")
    _sql(db, "DROP TRIGGER cortex_audit_no_delete")
    _sql(db, tamper)
    v = _verify()
    assert v["status"] == "broken"
    assert any(reason in b["reason"] for b in v["breaks"]), v["breaks"]


def test_link_digest_is_the_same_on_both_backends_row_shapes():
    """gates_json comes back as text (SQLite) or a dict (JSONB); blocked as 0/1 or bool."""
    from datetime import datetime

    text = {"id": "a", "gates_json": '{"b": 1, "a": [2]}', "blocked": 0,
            "created_at": "2026-11-14 12:00:00"}
    pg = {"id": "a", "gates_json": {"a": [2], "b": 1}, "blocked": False,
          "created_at": datetime(2026, 11, 14, 12, 0, 0)}
    assert audit_chain.link_hash(text, 1, "x") == audit_chain.link_hash(pg, 1, "x")
    assert audit_chain.link_hash(text, 1, "x") != audit_chain.link_hash(text, 2, "x")


def test_pg_triggers_are_one_statement_each():
    stmts = audit_chain._pg_triggers("cortex_audit")
    assert any("BEFORE UPDATE ON cortex_audit" in s for s in stmts)
    assert any("BEFORE DELETE ON cortex_audit" in s for s in stmts)
    assert all("$$" not in s or s.count("$$") == 2 for s in stmts)


# ── the twin clears and re-fires ─────────────────────────────────────────────


def test_twin_chain_findings_clear_when_fixed_and_refire_when_broken(db, govern):
    _legacy_table(db)
    rules = _cortex_rules()
    assert {"cortex.audit.unchained", "cortex.audit.db_mutable"} <= set(rules)

    _migrate()
    govern()
    govern()
    rules = _cortex_rules()
    assert not {"cortex.audit.unchained", "cortex.audit.db_mutable",
                "cortex.audit.chain_broken", "cortex.audit.chain_unverified"} & set(rules)

    _sql(db, "DROP TRIGGER cortex_audit_no_update")
    target = _audit()[0]["id"]
    _sql(db, "UPDATE cortex_audit SET outcome = 'fail' WHERE id = ?", (target,))
    rules = _cortex_rules()
    assert "cortex.audit.db_mutable" in rules
    broken = rules["cortex.audit.chain_broken"]
    assert broken["severity"] == "critical"
    assert broken["thread_id"] == f"cortex_audit:{target}"


def test_twin_replays_an_observation_with_the_chain_identically(db, govern):
    from tools.twin_core import governance as gov

    govern()
    adapter = gov.adapter_for("cortex")
    env = gov.observe(adapter, record=True)
    assert env["extra"]["recorded"]
    result = adapter.replay(env["extra"]["observation_id"])
    assert result["match"] and result["evidence_intact"]


def test_each_chain_takes_its_own_advisory_lock():
    """tools.audit.chain.serialize_chain_writes: audit_trail keeps its lock, cortex_audit has its own."""
    from tools.audit import chain
    from tools.coordination.dblock import lock_key

    class _Cur:
        def __init__(self, log):
            self.log = log

        def execute(self, sql, params=None):
            self.log.append((sql, params))

    class _PG:
        _backend = "postgresql"

        def __init__(self):
            self.log = []

        def cursor(self):
            return _Cur(self.log)

    default, cortex = _PG(), _PG()
    chain.serialize_chain_writes(default)
    chain.serialize_chain_writes(cortex, audit_chain.LOCK_NAME)
    assert default.log == [("SELECT pg_advisory_xact_lock(%s)",
                            (lock_key(chain.CHAIN_LOCK_NAME),))]
    assert cortex.log == [("SELECT pg_advisory_xact_lock(%s)",
                           (lock_key("cortex_audit:chain"),))]
    assert lock_key(chain.CHAIN_LOCK_NAME) != lock_key(audit_chain.LOCK_NAME)

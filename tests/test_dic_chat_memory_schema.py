#!/usr/bin/env python3
"""dic_chat_memory schema-shape tests — CUI // SP-CTI.

DIC conversational memory sat at 0 rows on the live database while looking
merely idle. Three independent causes, each sufficient on its own:

1. `_ensure_table` used `CREATE TABLE IF NOT EXISTS`, which silently no-ops
   against a table that exists with the WRONG columns. It could never repair
   the legacy migration-191 message-log shape it was supposed to heal.
2. Migration 264 (the reconcile) was never applied, and the PostgreSQL
   consolidation squash captured the pre-264 state — so fresh bootstraps got
   the broken shape too.
3. `record_turn` swallows write failures, so the resulting breakage was
   indistinguishable from "nobody has chatted yet".

These tests pin the shape check and the health probe so a wrong-shaped table
reports itself instead of failing silently.
"""
from __future__ import annotations

import sqlite3

import pytest

from tools.db.storage import StorageConnection
from tools.document_intelligence import chat_memory as cm


# The legacy shape migration 191 created and no code ever wrote to.
_LEGACY_DDL = """
CREATE TABLE dic_chat_memory (
    memory_id      TEXT PRIMARY KEY,
    session_id     TEXT NOT NULL,
    collection_id  TEXT NOT NULL DEFAULT 'default',
    user_id        TEXT NOT NULL DEFAULT '',
    role           TEXT NOT NULL DEFAULT 'user',
    content        TEXT NOT NULL DEFAULT '',
    citations_json TEXT NOT NULL DEFAULT '[]',
    token_count    INTEGER NOT NULL DEFAULT 0,
    tenant_id      TEXT NOT NULL DEFAULT 'default',
    classification TEXT NOT NULL DEFAULT 'CUI',
    created_at     TEXT
)
"""


@pytest.fixture(autouse=True)
def _reset_module_state():
    """The shape probe caches per process; isolate each test."""
    cm._schema_ensured = False
    cm._schema_error = ""
    yield
    cm._schema_ensured = False
    cm._schema_error = ""


def _conn(ddl: str | None = None) -> StorageConnection:
    raw = sqlite3.connect(":memory:")
    if ddl:
        raw.execute(ddl)
        raw.commit()
    # Wrap so %s translation applies, per tests-raw-sqlite-bypasses-translate.
    return StorageConnection(raw, "sqlite")


# --------------------------------------------------------------------------- #
# Shape detection
# --------------------------------------------------------------------------- #


def test_absent_table_reports_missing():
    assert cm._table_shape(_conn()) == (False, False)
    assert cm.memory_health(_conn()) == {"available": False, "reason": "table_missing"}


def test_legacy_shape_is_detected_not_mistaken_for_healthy():
    """The core bug: the legacy table EXISTS, so existence alone said 'fine'."""
    conn = _conn(_LEGACY_DDL)
    exists, correct = cm._table_shape(conn)
    assert exists is True
    assert correct is False, "legacy message-log shape must not read as correct"

    health = cm.memory_health(conn)
    assert health["available"] is False
    assert "schema_mismatch" in health["reason"]


def test_turn_shape_reports_healthy():
    conn = _conn()
    cm._ensure_table(conn)
    exists, correct = cm._table_shape(conn)
    assert (exists, correct) == (True, True)
    assert cm.memory_health(conn) == {"available": True, "reason": ""}


# --------------------------------------------------------------------------- #
# _ensure_table must not pretend to have healed a wrong-shaped table
# --------------------------------------------------------------------------- #


def test_ensure_table_creates_turn_schema_on_empty_db():
    conn = _conn()
    cm._ensure_table(conn)
    assert cm._schema_ensured is True
    cur = conn.cursor()
    cur.execute("PRAGMA table_info(dic_chat_memory)")  # pg-ok: sqlite test fixture
    cols = {r[1] for r in cur.fetchall()}
    assert "turn_id" in cols
    assert "memory_id" not in cols


def test_ensure_table_refuses_to_claim_success_on_legacy_shape(caplog):
    """`CREATE TABLE IF NOT EXISTS` no-ops here — it must not report success."""
    conn = _conn(_LEGACY_DDL)
    # icdev_logger sets propagate=False, so caplog cannot see records unless we
    # re-enable propagation for the duration of the assertion.
    prior = cm.logger.propagate
    cm.logger.propagate = True
    try:
        with caplog.at_level("ERROR", logger=cm.logger.name):
            cm._ensure_table(conn)
    finally:
        cm.logger.propagate = prior

    assert cm._schema_ensured is False, "must not mark a wrong-shaped table as ensured"
    assert cm._schema_error == "schema_mismatch"
    errors = [r for r in caplog.records if r.levelname == "ERROR"]
    assert errors, "the mismatch must be logged at ERROR, not swallowed at debug"
    assert any("legacy message-log schema" in r.getMessage() for r in errors)


def test_ensure_table_does_not_destroy_a_populated_legacy_table():
    """Detection only. Dropping data is the migration's job, under its own guard."""
    conn = _conn(_LEGACY_DDL)
    conn.execute(
        "INSERT INTO dic_chat_memory (memory_id, session_id) VALUES ('m1', 's1')"
    )
    conn.commit()
    cm._ensure_table(conn)
    cur = conn.cursor()
    cur.execute("SELECT count(*) FROM dic_chat_memory")
    assert cur.fetchone()[0] == 1


# --------------------------------------------------------------------------- #
# The DDL this module writes must match what the schema ships
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("schema_rel", [
    "tools/db/schema/pg_consolidated.sql",
    "icdev/tools/db/schema/pg_consolidated.sql",
], ids=["canonical", "icdev_mirror"])
def test_turn_ddl_and_consolidated_schema_agree(schema_rel):
    """pg_consolidated.sql shipped the legacy shape; a fresh bootstrap was broken.

    BOTH copies are checked. The original version of this test looked only at
    the canonical file, so when the fix was applied there and not mirrored, the
    packaged ``icdev/`` schema kept shipping the broken migration-191 shape —
    a fresh bootstrap from the wheel would recreate the very bug the fix
    removed, and nothing failed.

    A mirror that is only half-tested is not mirrored.
    """
    import pathlib
    import re

    root = pathlib.Path(__file__).resolve().parents[1]
    path = root / schema_rel
    assert path.is_file(), f"{schema_rel} missing"
    sql = path.read_text(encoding="utf-8", errors="replace")
    block = re.search(r"CREATE TABLE public\.dic_chat_memory \((.*?)\);", sql, re.S)
    assert block, f"dic_chat_memory not found in {schema_rel}"
    consolidated_cols = set(re.findall(r"^\s{4}(\w+)\s", block.group(1), re.M))
    module_cols = set(re.findall(r"^\s{4}(\w+)\s", cm._TURN_TABLE_DDL, re.M))

    assert "turn_id" in consolidated_cols, f"{schema_rel} still ships the legacy shape"
    assert "memory_id" not in consolidated_cols
    assert module_cols == consolidated_cols, (
        f"drift in {schema_rel}: only in module={module_cols - consolidated_cols}, "
        f"only in schema={consolidated_cols - module_cols}"
    )


@pytest.mark.parametrize("schema_rel", [
    "tools/db/schema/pg_consolidated.sql",
    "icdev/tools/db/schema/pg_consolidated.sql",
], ids=["canonical", "icdev_mirror"])
def test_primary_key_follows_the_turn_shape(schema_rel):
    """The pkey must move with the columns, or the CREATE fails on bootstrap."""
    import pathlib
    import re

    root = pathlib.Path(__file__).resolve().parents[1]
    sql = (root / schema_rel).read_text(encoding="utf-8", errors="replace")
    m = re.search(r"ADD CONSTRAINT dic_chat_memory_pkey PRIMARY KEY \((\w+)\)", sql)
    assert m, f"dic_chat_memory_pkey not found in {schema_rel}"
    assert m.group(1) == "turn_id", (
        f"{schema_rel} keys dic_chat_memory on {m.group(1)!r}, which the turn "
        "schema no longer has"
    )

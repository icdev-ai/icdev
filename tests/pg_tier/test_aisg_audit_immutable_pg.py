# CUI // SP-CTI
"""aisg_audit REFUSES UPDATE and DELETE on a live PostgreSQL — and accepts INSERT.

``tests/test_aisg_audit_pg_immutability.py`` pins the DDL that init_db and
migration 20261008003850 send. Only a real server can say whether that DDL
actually makes the table append-only, so this file runs it against one.

Isolation: every test works inside its OWN throwaway schema
(``aisg_imm_<hex>``), reached through ``search_path`` on dedicated psycopg2
connections, and the schema is dropped CASCADE afterwards. Audit rows are, by
design, impossible to delete — so nothing here may ever touch the shared
``public.aisg_audit``.

Excluded from CI gating and run by Test (PostgreSQL) via
tests/pg_tier_allowlist.txt, for the reason recorded against it in
args/test_gating_gate.yaml (a module-level skipif in a gated file would breach
the closed skip_census).
"""
from __future__ import annotations

import importlib
import importlib.util
import os
import uuid
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("ICDEV_PYTEST_PG", "").lower() not in ("1", "true", "yes"),
    reason="PG tier only — set ICDEV_PYTEST_PG=1 with a live PostgreSQL service",
)

_MIGRATION_DIR = (
    Path(__file__).resolve().parents[2]
    / "tools" / "db" / "migrations" / "20261008003850_aisg_audit_pg_immutability"
)

# The table as it exists on a PG database initialised BEFORE the triggers:
# same columns, no triggers. The migration's job is to fix exactly this.
_LEGACY_AISG_AUDIT = """
CREATE TABLE aisg_audit (
    id              SERIAL PRIMARY KEY,
    roadmap_id      TEXT,
    actor           TEXT DEFAULT '',
    action          TEXT NOT NULL,
    detail          TEXT,
    classification  TEXT DEFAULT 'CUI // SP-CTI',
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP
)
"""


def _connect_kwargs() -> dict:
    import tools.db.storage  # noqa: F401 — loads .env for a workstation run

    return dict(
        host=os.environ.get("ICDEV_PG_HOST", "localhost"),
        port=int(os.environ.get("ICDEV_PG_PORT", "5432")),
        user=os.environ.get("ICDEV_PG_USER", "icdev"),
        password=os.environ.get("ICDEV_PG_PASSWORD", ""),
        dbname=os.environ.get("ICDEV_PG_DATABASE", "icdev"),
        connect_timeout=10,
    )


@pytest.fixture()
def scratch_schema():
    import psycopg2

    schema = f"aisg_imm_{uuid.uuid4().hex[:12]}"
    # NOT a skip: reaching this line means the tier was asked for, and an
    # unreachable server must fail loudly rather than read as covered.
    admin = psycopg2.connect(**_connect_kwargs())
    admin.autocommit = True
    admin.cursor().execute(f"CREATE SCHEMA {schema}")
    try:
        yield schema
    finally:
        admin.cursor().execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
        admin.close()


def _conn(schema):
    """A StorageConnection whose unqualified names resolve inside ``schema``."""
    import psycopg2
    import psycopg2.extras

    from tools.db.storage import StorageConnection

    raw = psycopg2.connect(
        **_connect_kwargs(),
        options=f"-c search_path={schema}",
        cursor_factory=psycopg2.extras.RealDictCursor,
    )
    return StorageConnection(raw, "postgresql")


def _load(name):
    spec = importlib.util.spec_from_file_location(f"_aisg_mig_pg_{name}", str(_MIGRATION_DIR / f"{name}.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _probe(schema):
    """Insert one row, then try to UPDATE it and DELETE it.

    Returns (inserted, update_refused, delete_refused), each attempt in its own
    transaction so one refusal cannot mask the next.
    """
    import psycopg2

    conn = _conn(schema)
    try:
        conn.execute("INSERT INTO aisg_audit (roadmap_id, action) VALUES (%s, %s)", ("rm-probe", "probe"))
        conn.commit()
        inserted = conn.execute("SELECT COUNT(*) AS n FROM aisg_audit").fetchone()["n"]

        refused = []
        for sql in ("UPDATE aisg_audit SET action = 'tampered'", "DELETE FROM aisg_audit"):
            try:
                conn.execute(sql)
                conn.commit()
                refused.append(False)
            except psycopg2.Error as exc:
                conn.rollback()
                assert "immutable" in str(exc), f"refused for the wrong reason: {exc}"
                refused.append(True)
        return inserted, refused[0], refused[1]
    finally:
        conn.close()


def test_fresh_init_db_makes_aisg_audit_append_only(scratch_schema, monkeypatch):
    mod = importlib.import_module("tools.aisg.db.init_db")
    monkeypatch.setattr(mod, "_AISG_BACKEND", "postgresql")
    monkeypatch.setattr(mod, "get_connection", lambda *a, **k: _conn(scratch_schema))

    mod.init_db()

    assert _probe(scratch_schema) == (1, True, True)


def test_migration_closes_the_gap_on_an_existing_table_and_is_rerunnable(scratch_schema):
    conn = _conn(scratch_schema)
    conn.execute(_LEGACY_AISG_AUDIT)
    conn.commit()
    conn.close()

    # Negative control: before the migration the table is MUTABLE on PG —
    # the defect this migration exists to fix.
    assert _probe(scratch_schema) == (1, False, False)

    up = _load("up")
    for _ in range(2):  # idempotent: a second run must converge, not fail
        conn = _conn(scratch_schema)
        try:
            assert up.up(conn)["status"] == "applied"
        finally:
            conn.close()

    _inserted, update_refused, delete_refused = _probe(scratch_schema)
    assert (update_refused, delete_refused) == (True, True)


def test_migration_down_restores_the_prior_state(scratch_schema):
    conn = _conn(scratch_schema)
    conn.execute(_LEGACY_AISG_AUDIT)
    conn.commit()
    _load("up").up(conn)
    _load("down").down(conn)
    conn.close()

    assert _probe(scratch_schema) == (1, False, False)


def test_migration_skips_a_database_without_aisg_audit(scratch_schema):
    conn = _conn(scratch_schema)
    try:
        assert _load("up").up(conn)["status"] == "skipped"
    finally:
        conn.close()

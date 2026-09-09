# CUI // SP-CTI
"""The local_database connector's POSTGRES path, against a live server.

WHY THIS FILE EXISTS. `tests/databridge/test_content_connectors.py` shipped 66
tests for this connector and every one of them runs SQLite. The `postgresql`
branch of `_open()` had never been executed by anything, which meant four
behaviours were asserted only by the module docstring:

  * `psycopg2.connect` with a `RealDictCursor` — rows come back as dicts, a
    different code path in `_row_to_dict` than sqlite3.Row takes;
  * `set_session(readonly=True)` — the Postgres MIRROR of sqlite's `mode=ro`.
    The sqlite half is proven by a test that a DELETE through the handle
    raises. Until this file the Postgres half was a line of code and a claim;
  * the `%s` placeholder — `_placeholder()` branches on the driver, and a
    parameter that does not bind is an injection hole, not a syntax error;
  * schema-qualified table names — `valid_identifier(qualified=True)` admits
    `schema.table`, which is a Postgres shape SQLite has no equivalent for and
    which therefore no existing test could reach.

The connector opens its OWN connection from a DSN reference rather than going
through `get_connection`, so this does not use the ambient backend. It needs a
live server: the module SKIPS when the tier was not asked for, and FAILS when it
was and no server answered -- "nobody ran the PG tier" and "the PG tier could
not reach PostgreSQL" are different facts and only one of them is fine.

Excluded from CI gating rather than listed in args/ci_test_files (the reason is
in args/test_gating_gate.yaml): gating a file whose module-level skipif fires
everywhere but one job would put a skip into a CI-gated file, and skip_census is
closed. tests/pg_tier_allowlist.txt is what runs it, in Test (PostgreSQL).
"""

from __future__ import annotations

import os
import uuid

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("ICDEV_PYTEST_PG", "").lower() not in ("1", "true", "yes"),
    reason="PG tier only — set ICDEV_PYTEST_PG=1 with a live PostgreSQL service",
)

from tools.databridge.connector import ConnectorRequest  # noqa: E402
from tools.databridge.connectors.local_database_connector import (  # noqa: E402
    LocalDatabaseConnector,
)

SCHEMA = "dbridge_local_pg"
TABLE = "policy_register"
DSN_ENV = "DBRIDGE_PG_TEST_DSN"


def _dsn() -> str:
    """The live server, from whatever the tier provides.

    CI sets the `ICDEV_PG_*` parts; a workstation usually has the whole URL.
    """
    url = os.environ.get("ICDEV_DATABASE_URL", "").strip()
    if url:
        return url
    host = os.environ.get("ICDEV_PG_HOST", "localhost")
    port = os.environ.get("ICDEV_PG_PORT", "5432")
    user = os.environ.get("ICDEV_PG_USER", "icdev")
    pwd = os.environ.get("ICDEV_PG_PASSWORD", "")
    db = os.environ.get("ICDEV_PG_DATABASE", "icdev")
    auth = f"{user}:{pwd}@" if pwd else f"{user}@"
    return f"postgresql://{auth}{host}:{port}/{db}"


@pytest.fixture(scope="module")
def live_table():
    """A real table in its own schema, created and dropped by a WRITABLE handle.

    Deliberately not `tools.db.storage`: this connector never touches the
    platform connection, and setting the fixture up through it would test a
    seam the connector does not use.
    """
    import psycopg2

    dsn = _dsn()
    # NOT a skip. The module-level guard above means we only reach this line
    # when the tier was asked for, and the tier runs with
    # ICDEV_PG_NO_FALLBACK=1 so that an absent backend is loud rather than
    # quietly green. A skip here would report "covered" for a server nobody
    # ever reached.
    setup = psycopg2.connect(dsn, connect_timeout=10)

    setup.autocommit = True
    cur = setup.cursor()
    cur.execute(f"CREATE SCHEMA IF NOT EXISTS {SCHEMA}")
    cur.execute(f"DROP TABLE IF EXISTS {SCHEMA}.{TABLE}")
    cur.execute(
        f"CREATE TABLE {SCHEMA}.{TABLE} ("
        " policy_id text, title text, body text,"
        " effective_date text, secret_note text)"
    )
    cur.executemany(
        f"INSERT INTO {SCHEMA}.{TABLE} VALUES (%s,%s,%s,%s,%s)",
        [
            ("POL-1", "Key rotation", "Rotate signing keys quarterly.",
             "2026-01-01", "internal only"),
            ("POL-2", "Peering policy", "Open peering at any IXP.",
             "2026-06-01", "internal only"),
        ],
    )
    cur.execute(f"DROP TABLE IF EXISTS {SCHEMA}.hr_salaries")
    cur.execute(f"CREATE TABLE {SCHEMA}.hr_salaries (person text, amount int)")
    cur.execute(f"INSERT INTO {SCHEMA}.hr_salaries VALUES ('Dana', 1)")

    os.environ[DSN_ENV] = dsn
    try:
        yield f"{SCHEMA}.{TABLE}"
    finally:
        cur.execute(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE")
        setup.close()
        os.environ.pop(DSN_ENV, None)


def _connect(tables):
    c = LocalDatabaseConnector()
    assert c.connect({
        "driver": "postgresql",
        "auth_secret_ref": f"env:{DSN_ENV}",
        "tables": tables,
    }), "the connector refused a live PostgreSQL DSN"
    return c


@pytest.fixture
def connector(live_table):
    c = _connect([{"name": live_table,
                   "columns": ["policy_id", "title", "body"],
                   "order_by": "policy_id"}])
    yield c
    c.disconnect()


# ── the driver actually opens ─────────────────────────────────────────────

def test_it_connects_and_reports_the_postgres_driver(connector, live_table):
    health = connector.health_check()
    assert health["status"] == "healthy"
    assert health["driver"] == "postgresql"
    assert health["tables_declared"] == [live_table]


def test_rows_come_back_as_dicts_not_tuples(connector):
    """`RealDictCursor` takes the `isinstance(row, dict)` branch of
    `_row_to_dict`; sqlite3.Row takes the one below it, so this branch had no
    coverage at all."""
    r = connector.read(
        ConnectorRequest(table_name=connector.list_tables()[0], limit=10))
    assert r.status == "ok", r.errors
    assert r.row_count == 2
    assert isinstance(r.data[0], dict)
    assert set(r.data[0]) == {"policy_id", "title", "body"}


def test_the_declared_order_by_is_applied(connector):
    r = connector.read(ConnectorRequest(table_name=connector.list_tables()[0]))
    assert [row["policy_id"] for row in r.data] == ["POL-2", "POL-1"]


def test_an_undeclared_column_never_leaves_the_server(connector):
    r = connector.read(ConnectorRequest(table_name=connector.list_tables()[0]))
    assert "secret_note" not in r.data[0]
    assert "internal only" not in str(r.data)


# ── the %s placeholder, which sqlite cannot exercise ──────────────────────

def test_the_placeholder_is_the_postgres_one(connector):
    sql, params = connector.build_select(
        connector.list_tables()[0], {"policy_id": "POL-1"}, 10)
    assert "= %s" in sql, "a `?` here binds nothing on psycopg2"
    assert "?" not in sql
    assert params == ["POL-1"]


def test_a_bound_filter_selects_one_row_on_the_server(connector):
    r = connector.read(ConnectorRequest(
        table_name=connector.list_tables()[0], filters={"policy_id": "POL-1"}))
    assert r.status == "ok", r.errors
    assert r.row_count == 1
    assert r.data[0]["title"] == "Key rotation"


def test_an_injection_in_a_filter_value_matches_nothing_on_the_server(connector):
    """The value is a PARAMETER. If `%s` ever stopped binding this would either
    error or return both rows; matching zero is the proof it bound."""
    r = connector.read(ConnectorRequest(
        table_name=connector.list_tables()[0],
        filters={"policy_id": "POL-1' OR '1'='1"}))
    assert r.status == "ok", r.errors
    assert r.row_count == 0


def test_a_percent_sign_in_a_value_is_not_a_format_specifier(connector):
    """psycopg2 interpolates client-side, so a literal `%` in a bound value is
    the classic way a parameterised query still blows up."""
    r = connector.read(ConnectorRequest(
        table_name=connector.list_tables()[0],
        filters={"title": "100% coverage %s"}))
    assert r.status == "ok", r.errors
    assert r.row_count == 0


# ── read-only, enforced by the SERVER and not only by this module ─────────

def test_the_postgres_session_itself_refuses_a_write(connector):
    """The mirror of the sqlite `mode=ro` test. `set_session(readonly=True)` is
    what makes 'read-only' something the server enforces rather than a promise
    this module makes about itself; without this assertion that line could be
    deleted and every other test would still pass."""
    import psycopg2

    cur = connector._conn.cursor()
    with pytest.raises(psycopg2.errors.ReadOnlySqlTransaction):
        cur.execute(f"CREATE TABLE {SCHEMA}.should_not_exist (x int)")


def test_write_refuses_before_it_reaches_the_server(connector):
    r = connector.write(ConnectorRequest(table_name=connector.list_tables()[0]),
                        {"policy_id": "POL-9"})
    assert r.status == "error" and "READ-ONLY" in r.errors[0]


# ── the allowlist, on a server that has other tables ──────────────────────

def test_a_table_that_exists_on_the_server_but_was_not_declared_is_refused(
    connector,
):
    r = connector.read(ConnectorRequest(table_name=f"{SCHEMA}.hr_salaries"))
    assert r.status == "error"
    assert "not declared" in r.errors[0]


def test_list_tables_does_not_enumerate_the_servers_catalogue(connector,
                                                              live_table):
    """A live server has thousands of tables. The connector must report the
    DECLARATION, because naming what exists is the first half of reading it."""
    assert connector.list_tables() == [live_table]


def test_a_filter_on_an_unexposed_column_is_refused_by_name(connector):
    r = connector.read(ConnectorRequest(
        table_name=connector.list_tables()[0], filters={"secret_note": "x"}))
    assert r.status == "error"
    assert "secret_note" in r.errors[0] and "not exposed" in r.errors[0]


def test_no_allowlist_means_no_connection_even_with_a_valid_dsn(live_table):
    c = LocalDatabaseConnector()
    assert c.connect({
        "driver": "postgresql",
        "auth_secret_ref": f"env:{DSN_ENV}",
        "tables": [],
    }) is False


# ── schema qualification: a Postgres shape sqlite cannot reach ────────────

def test_a_schema_qualified_name_reaches_the_right_table(live_table):
    """`valid_identifier(qualified=True)` admits `schema.table`. Nothing in the
    sqlite suite could prove that the qualified name is emitted intact and
    resolves — sqlite has no schemas."""
    c = _connect([{"name": live_table, "columns": ["policy_id"]}])
    try:
        sql, _ = c.build_select(live_table, {}, 5)
        assert f"FROM {SCHEMA}.{TABLE}" in sql
        r = c.read(ConnectorRequest(table_name=live_table))
        assert r.status == "ok" and r.row_count == 2, r.errors
    finally:
        c.disconnect()


def test_an_unqualified_name_does_not_silently_resolve_through_search_path(
    live_table,
):
    """Declaring `policy_register` without its schema must not quietly work via
    whatever `search_path` happens to be — the operator named a table that is
    not on the default path, and a read that succeeds anyway is reading
    something they did not declare."""
    c = _connect([{"name": TABLE, "columns": ["policy_id"]}])
    try:
        r = c.read(ConnectorRequest(table_name=TABLE))
        assert r.status == "error"
        assert "policy_register" in r.errors[0]
    finally:
        c.disconnect()


# ── the DSN is a reference, on the live path too ──────────────────────────

def test_a_literal_dsn_is_refused_even_when_it_would_work(live_table):
    """The seeder refuses a literal in YAML; this closes the programmatic path.
    The DSN passed here is a REAL, working one — the refusal is about its being
    a value rather than a reference, not about its being wrong."""
    c = LocalDatabaseConnector()
    assert c.connect({
        "driver": "postgresql",
        "dsn": os.environ[DSN_ENV],
        "tables": [live_table],
    }) is False


def test_an_unresolvable_reference_is_a_refusal_not_a_fallback(live_table):
    c = LocalDatabaseConnector()
    assert c.connect({
        "driver": "postgresql",
        "auth_secret_ref": f"env:NO_SUCH_DSN_{uuid.uuid4().hex[:8]}",
        "tables": [live_table],
    }) is False

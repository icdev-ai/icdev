# CUI // SP-CTI
"""ctx-perf-05 — the PLANNER must actually choose the composite, on real PG.

The sibling file ``tests/cortex/test_rls_index_coverage.py`` proves the migration
creates the right indexes. It cannot prove they are USED: index choice is a
cost-based decision no SQLite assertion and no mock can stand in for. This is the
only place that claim is tested, so it lives in the PG tier where a live
PostgreSQL is guaranteed rather than in the SQLite suite where it would skip
every run and prove nothing — a test that always skips is a declared capability
with no consumer.

It connects through ``get_connection()`` on the AMBIENT backend, per the tier's
rule — never a hand-rolled psycopg2 call with its own copy of the credential
defaults, which is how a test ends up authenticating against a database nobody
configured. Outside a Flask request there is no ``g.security_context``, so
``_inject_rls`` adds nothing and the RLS predicate below stays literal SQL this
test controls — which is what it needs, that predicate being the subject.

It seeds its own data because a plan assertion is only meaningful when a
sequential scan is genuinely the wrong answer: the dev database held 176 rows in
``cortex_audit``, and at that size PostgreSQL correctly ignores every index.

It creates a private schema and drops it in ``finally``. It never reads or writes
``public``.
"""
from __future__ import annotations

import os

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("ICDEV_PYTEST_PG", "").lower() not in ("1", "true", "yes"),
    reason="PG tier only — set ICDEV_PYTEST_PG=1 with a live PostgreSQL service",
)

SCHEMA = "ctx_perf_05_plan"

#: tools/cortex/metrics.py::_scan's rollup read, as StorageCursor._inject_rls
#: rewrites it: the call site's created_at filter plus the RLS row predicate
#: (tenant equality + Bell-LaPadula read-down IN-list).
WINDOW_QUERY = f"""
    SELECT function, tenant_id, outcome, blocked, COUNT(*) AS n
    FROM {SCHEMA}.cortex_audit
    WHERE created_at >= %s AND tenant_id = %s AND classification IN (%s, %s)
    GROUP BY function, tenant_id, outcome, blocked
"""
PARAMS = ("2026-08-07 00:00:00", "tenant-7", "CUI", "UNCLASSIFIED")


@pytest.fixture()
def pg_conn():
    from tools.db.storage import get_connection, is_pg

    if not is_pg():
        # The tier's whole premise is a live PostgreSQL; going green against the
        # SQLite fallback would be false confidence, so fail rather than skip.
        pytest.fail("PG tier requested but the ambient backend is not PostgreSQL")

    conn = get_connection()
    try:
        yield conn
    finally:
        try:
            conn.execute(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE")
            conn.commit()
        finally:
            conn.close()


def _seed(conn, rows: int = 200_000) -> None:
    conn.execute(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE")
    conn.execute(f"CREATE SCHEMA {SCHEMA}")
    conn.execute(
        f"""
        CREATE TABLE {SCHEMA}.cortex_audit (
            id              TEXT PRIMARY KEY,
            session_id      TEXT,
            tenant_id       TEXT NOT NULL DEFAULT 'default',
            classification  TEXT NOT NULL DEFAULT 'CUI',
            function        TEXT NOT NULL DEFAULT 'cortex',
            gates_json      JSONB,
            outcome         TEXT NOT NULL DEFAULT 'pass',
            blocked         BOOLEAN NOT NULL DEFAULT FALSE,
            created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    # Exactly the indexes migration 262 ships — the state this migration improves
    # on. Seeding with the composite already present would test nothing.
    conn.execute(f"CREATE INDEX ON {SCHEMA}.cortex_audit(tenant_id)")
    conn.execute(f"CREATE INDEX ON {SCHEMA}.cortex_audit(created_at)")
    conn.execute(
        f"""
        INSERT INTO {SCHEMA}.cortex_audit
            (id, tenant_id, classification, function, outcome, blocked, created_at)
        SELECT md5(g::text), 'tenant-' || mod(g, 40),
               (ARRAY['CUI','UNCLASSIFIED','SECRET'])[1 + mod(g, 3)],
               (ARRAY['ask','search','reason'])[1 + mod(g, 3)],
               (ARRAY['pass','warn','fail','blocked'])[1 + mod(g, 4)],
               (mod(g, 17) = 0),
               NOW() - (mod(g, 129600) * INTERVAL '1 minute')
        FROM generate_series(1, {rows}) g
        """
    )
    conn.execute(f"ANALYZE {SCHEMA}.cortex_audit")


def _explain(conn, sql, params) -> str:
    cur = conn.execute("EXPLAIN " + sql, params)
    # RealDictCursor gives one single-key mapping per plan line; take the value
    # rather than index [0], which on a mapping row would be a KeyError.
    return "\n".join(str(next(iter(dict(r).values()))) for r in cur.fetchall())


def test_composite_replaces_the_two_single_column_index_scans(pg_conn):
    """The BEFORE plan is the negative control: without it this asserts nothing.

    A test that only checked the AFTER plan would pass just as happily if the
    planner had been using the composite's NAME for some unrelated reason, or if
    the query had been rewritten to something trivially indexable.
    """
    _seed(pg_conn)

    before = _explain(pg_conn, WINDOW_QUERY, PARAMS)
    assert "idx_cortex_audit_tenant_created" not in before

    pg_conn.execute(
        f"CREATE INDEX idx_cortex_audit_tenant_created "
        f"ON {SCHEMA}.cortex_audit(tenant_id, created_at)"
    )
    pg_conn.execute(f"ANALYZE {SCHEMA}.cortex_audit")

    after = _explain(pg_conn, WINDOW_QUERY, PARAMS)
    assert "idx_cortex_audit_tenant_created" in after, (
        f"planner did not choose the composite.\nBEFORE:\n{before}\nAFTER:\n{after}"
    )

    # Both halves of the RLS-rewritten predicate are served by the index itself
    # rather than rechecked against the heap — that is the difference from the
    # BitmapAnd of two single-column indexes it replaces.
    assert "Index Cond" in after, after
    assert "tenant_id" in after and "created_at" in after, after


def test_the_composite_is_cheaper_than_the_bitmap_and_it_replaces(pg_conn):
    """Cost, not just index name: the point is that it is a better plan.

    Asserting only "the composite appears" would still pass if the planner had
    picked it for a plan that was somehow worse — which is not a fix.
    """
    _seed(pg_conn)

    def total_cost(plan: str) -> float:
        # Top plan node carries the total: "... (cost=start..total rows=N ...)".
        head = plan.splitlines()[0]
        return float(head.split("cost=")[1].split("..")[1].split(" ")[0])

    before_cost = total_cost(_explain(pg_conn, WINDOW_QUERY, PARAMS))

    pg_conn.execute(
        f"CREATE INDEX idx_cortex_audit_tenant_created "
        f"ON {SCHEMA}.cortex_audit(tenant_id, created_at)"
    )
    pg_conn.execute(f"ANALYZE {SCHEMA}.cortex_audit")

    after_cost = total_cost(_explain(pg_conn, WINDOW_QUERY, PARAMS))

    assert after_cost < before_cost, (
        f"composite did not lower the estimated cost: {before_cost} -> {after_cost}"
    )

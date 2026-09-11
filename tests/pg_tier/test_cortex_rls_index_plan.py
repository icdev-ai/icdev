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

THE SEED HAS ITS OWN CLOCK, AND THE WINDOW IS DERIVED FROM IT (qa-fail-55f53c3df846c1a0).
The first version stamped ``created_at = NOW() - mod(g, 129600) minutes`` -- a 90-day
spread ending at the moment the test ran -- against a window fixed at 2026-08-07.
On the day it landed (2026-08-14) that window covered 7 of the 90 days; every day
after, one more. By 2026-09-11 it covered 35 -- and the newest ~49 days carry DOUBLE
density (200,000 rows over a 129,600-minute modulus wrap), so the composite's range
had grown from ~500 rows (10%) to ~2,600 (52%) of the 5,000-row tenant slice, and the BEFORE plan was no longer
even the BitmapAnd the test describes -- the planner had already dropped the
created_at index as useless. MEASURED that day on PostgreSQL 16.13, 200,000 rows,
default_statistics_target 100, five ANALYZE trials each:

    seed clock      rows in range   before -> after total cost   margin      composite chosen
    NOW()           2,584 / 5,000   2955-2983 -> 2933-2944       0.46-1.29%  3 of 5 trials
    2026-08-14      504 / 5,000     1745-1798 -> 1243-1355       23.6-30.9%  5 of 5 trials

and the unfixed test run ten times against that PG: 6 passed, 4 failed. A margin
under 1.3% is inside what ANALYZE's 30,000-row random sample moves the row
estimates by (rows=1735 vs 1765 for the same predicate in ONE CI run), so the
planner's choice was a coin toss the calendar had been loading a little more each
day -- and it came up tails on a docs-only PR (#2226, run 34647774935). Pinning
the seed's clock to ``SEED_CLOCK`` makes the window a fixed 7 of 90 days forever,
and the geometry guard in ``_seed`` refuses a future edit that widens it back.
After the pin: 10 of 10 runs pass. The mutation proof for args/red_first_gate.yaml
is the same file with the composite CREATE INDEX removed: the plan test is RED on
every run, and the cost test -- which under a bare ``after < before`` PASSED the
mutation, comparing two ANALYZE estimates of the same plan -- is RED on every run
once it demands ``MIN_COST_MARGIN``.
"""
from __future__ import annotations

import os

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("ICDEV_PYTEST_PG", "").lower() not in ("1", "true", "yes"),
    reason="PG tier only — set ICDEV_PYTEST_PG=1 with a live PostgreSQL service",
)

SCHEMA = "ctx_perf_05_plan"

#: The seed's clock. NEVER NOW(): a window measured back from the moment the
#: test runs is a different fraction of the seed every day it is run.
SEED_CLOCK = "2026-08-14 00:00:00"
#: The seed spreads rows evenly over this many minutes BEFORE SEED_CLOCK.
SEED_SPAN_MINUTES = 129_600  # 90 days
#: The window the query asks for: the newest 7 days of the 90.
WINDOW_DAYS = 7
#: A hard ceiling on the window's share of one tenant's rows. At 7/90 the true
#: share is ~10%; the composite's advantage is the difference between scanning
#: this share and the whole tenant slice, and it was measured to vanish into
#: sampling noise by ~52% (see the module docstring). The guard is what turns a
#: future "let me widen the window" edit into a named failure instead of a
#: plan assertion that flips one run in three.
MAX_WINDOW_SHARE = 0.15
#: The cost test's floor. Measured 2026-09-11 at the pinned geometry the composite
#: lowers the total estimate by 23.6-30.9% (five trials); ANALYZE resampling
#: alone moves a plan's estimate by ~1%. A bare `after < before` is a coin toss
#: whenever the planner ignores the composite -- proven by mutation: with the
#: CREATE INDEX removed it still PASSED, because it was comparing two noisy
#: estimates of the SAME plan. Ten percent is above the noise and under half the
#: measured effect.
MIN_COST_MARGIN = 0.10

#: tools/cortex/metrics.py::_scan's rollup read, as StorageCursor._inject_rls
#: rewrites it: the call site's created_at filter plus the RLS row predicate
#: (tenant equality + Bell-LaPadula read-down IN-list).
WINDOW_QUERY = f"""
    SELECT function, tenant_id, outcome, blocked, COUNT(*) AS n
    FROM {SCHEMA}.cortex_audit
    WHERE created_at >= %s AND tenant_id = %s AND classification IN (%s, %s)
    GROUP BY function, tenant_id, outcome, blocked
"""

def _window_start() -> str:
    from datetime import datetime, timedelta

    clock = datetime.strptime(SEED_CLOCK, "%Y-%m-%d %H:%M:%S")
    return (clock - timedelta(days=WINDOW_DAYS)).strftime("%Y-%m-%d %H:%M:%S")


PARAMS = (_window_start(), "tenant-7", "CUI", "UNCLASSIFIED")


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
               TIMESTAMP '{SEED_CLOCK}' - (mod(g, {SEED_SPAN_MINUTES}) * INTERVAL '1 minute')
        FROM generate_series(1, {rows}) g
        """
    )
    conn.execute(f"ANALYZE {SCHEMA}.cortex_audit")

    # Geometry guard: the plan assertions below are only meaningful while the
    # window is a SMALL share of the tenant slice. Asked of the seeded rows
    # themselves, never of the planner's estimate, so it cannot flip on ANALYZE.
    cur = conn.execute(
        f"""
        SELECT COUNT(*) FILTER (WHERE created_at >= %s) AS in_window, COUNT(*) AS in_tenant
        FROM {SCHEMA}.cortex_audit WHERE tenant_id = %s
        """,
        PARAMS[:2],
    )
    row = dict(cur.fetchone())
    share = row["in_window"] / row["in_tenant"]
    assert share <= MAX_WINDOW_SHARE, (
        f"window covers {row['in_window']} of {row['in_tenant']} tenant rows "
        f"({share:.1%}); above {MAX_WINDOW_SHARE:.0%} the composite and the "
        f"single-column bitmap cost within sampling noise of each other and the "
        f"plan assertion becomes a coin toss (measured 0.46-1.29% margin at 52%)"
    )


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

    assert after_cost < before_cost * (1 - MIN_COST_MARGIN), (
        f"composite did not lower the estimated cost by at least "
        f"{MIN_COST_MARGIN:.0%}: {before_cost} -> {after_cost} "
        f"({(before_cost - after_cost) / before_cost:.1%})"
    )

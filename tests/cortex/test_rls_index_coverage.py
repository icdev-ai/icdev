# CUI // SP-CTI
"""ctx-perf-05 — the Cortex tables must be indexed for the query the DB RUNS.

``get_connection()`` attaches ``flask.g.security_context`` and
``StorageCursor._inject_rls`` then rewrites every read to carry
``AND tenant_id = ? AND classification IN (...)``. So the shape that reaches the
planner is never the shape written at the call site: ``metrics._scan``'s
``WHERE created_at >= ?`` is really ``tenant_id`` + ``created_at``, and the IQE
adapters' bare ``ORDER BY created_at DESC LIMIT ?`` is really a per-tenant top-n.

Migration ``20260814192722_cortex_rls_composite_indexes`` adds the composites for
that shape. These tests hold three properties:

1. **The migration is idempotent and lands on a fresh DB.** Applied twice over a
   database bootstrapped by 262/263, it creates every index and raises nothing.
2. **Ordering is safe.** ``MigrationRunner.discover_migrations`` sorts on
   (digit-count, digits), so the 3-digit 262/263 that CREATE these tables always
   precede this 14-digit timestamp. If that ever inverted, the migration would
   run against tables that do not exist yet — on a FRESH database only, which is
   exactly the environment nobody notices.
3. **init_db.py does not drift from the migration.** Fresh databases run
   ``tools/cortex/db/init_db.py`` DIRECTLY rather than the migration, so an index
   that exists only in the migration is absent on every new deployment — the
   failure mode migration 262's own header warns about.

The PG plan assertion (that the composite is actually CHOSEN) is a separate test
below, skipped when no PostgreSQL is reachable.
"""
from __future__ import annotations

import os
import re
import sqlite3
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS = REPO_ROOT / "tools" / "db" / "migrations"
MIGRATION_ID = "20260814192722_cortex_rls_composite_indexes"
MIGRATION_DIR = MIGRATIONS / MIGRATION_ID

#: index name -> (table, ordered columns). The column ORDER is the whole point:
#: tenant_id leads because the RLS predicate is an equality on it.
EXPECTED = {
    "idx_cortex_audit_tenant_created": ("cortex_audit", ["tenant_id", "created_at"]),
    "idx_cortex_search_history_tenant_created": (
        "cortex_search_history",
        ["tenant_id", "created_at"],
    ),
    "idx_cortex_chat_sessions_tenant_created": (
        "cortex_chat_sessions",
        ["tenant_id", "created_at"],
    ),
    "idx_cortex_messages_tenant_session_turn": (
        "cortex_messages",
        ["tenant_id", "session_id", "turn_number"],
    ),
}


def _sqlite_ddl(sql: str) -> str:
    """Reduce the PG-authored DDL to what SQLite accepts verbatim."""
    return sql.replace("JSONB", "TEXT").replace("BOOLEAN", "INTEGER")


def _fresh_db(tmp_path: Path) -> sqlite3.Connection:
    """A database bootstrapped by migrations 262 and 263 and nothing else."""
    conn = sqlite3.connect(str(tmp_path / "fresh.db"))
    for name in ("262_cortex_tables.sql", "263_cortex_canvas_tables.sql"):
        conn.executescript(_sqlite_ddl((MIGRATIONS / name).read_text(encoding="utf-8")))
    return conn


def _indexes(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'index'"
    ).fetchall()
    return {r[0] for r in rows}


def _index_columns(conn: sqlite3.Connection, index: str) -> list[str]:
    return [r[2] for r in conn.execute(f"PRAGMA index_info('{index}')").fetchall()]


# ── 1. Idempotent, clean on a fresh DB ───────────────────────────────────────

def test_migration_applies_to_a_fresh_database(tmp_path):
    conn = _fresh_db(tmp_path)
    before = _indexes(conn)
    assert not (before & set(EXPECTED)), "composites must not pre-exist in 262/263"

    up = _sqlite_ddl((MIGRATION_DIR / "up.sql").read_text(encoding="utf-8"))
    conn.executescript(up)

    assert set(EXPECTED) <= _indexes(conn)


def test_migration_is_idempotent(tmp_path):
    conn = _fresh_db(tmp_path)
    up = _sqlite_ddl((MIGRATION_DIR / "up.sql").read_text(encoding="utf-8"))
    conn.executescript(up)
    # Second application must be a silent no-op, not an "index already exists".
    conn.executescript(up)
    assert set(EXPECTED) <= _indexes(conn)


def test_down_removes_exactly_what_up_added(tmp_path):
    conn = _fresh_db(tmp_path)
    baseline = _indexes(conn)

    conn.executescript(_sqlite_ddl((MIGRATION_DIR / "up.sql").read_text(encoding="utf-8")))
    conn.executescript((MIGRATION_DIR / "down.sql").read_text(encoding="utf-8"))

    # Rollback restores the pre-migration index set exactly: it must not have
    # dropped the single-column indexes from 262/263 that it never created.
    assert _indexes(conn) == baseline


def test_composite_columns_lead_with_tenant_id(tmp_path):
    """The RLS predicate is an equality on tenant_id — it must be the prefix.

    An index on (created_at, tenant_id) would satisfy a naive "is there a
    composite?" check while being unable to serve the equality at all.
    """
    conn = _fresh_db(tmp_path)
    conn.executescript(_sqlite_ddl((MIGRATION_DIR / "up.sql").read_text(encoding="utf-8")))

    for index, (_table, columns) in EXPECTED.items():
        assert _index_columns(conn, index) == columns, index


# ── 2. Ordering: 262/263 must precede this migration ─────────────────────────

def test_migration_runs_after_the_262_263_that_create_the_tables():
    from tools.db.migration_runner import MigrationRunner

    runner = MigrationRunner.__new__(MigrationRunner)
    runner.migrations_dir = MIGRATIONS
    versions = [m["version"] for m in runner.discover_migrations()]

    assert "20260814192722" in versions, "migration not discovered"
    ours = versions.index("20260814192722")
    for creator in ("262", "263"):
        assert creator in versions
        assert versions.index(creator) < ours, (
            f"migration {creator} CREATEs the table this migration indexes; "
            "running out of order would fail on a fresh database only"
        )


# ── 3. init_db.py must not drift from the migration ──────────────────────────

@pytest.mark.parametrize(
    "schema_path",
    [
        "tools/cortex/db/init_db.py",
        "icdev/tools/cortex/db/init_db.py",
    ],
)
def test_fresh_bootstrap_schema_declares_the_same_composites(schema_path):
    """Fresh DBs run init_db.py directly, never this migration.

    Both copies are checked: ``tools/`` is the shim and ``icdev/tools/`` is
    canonical, and only one of them being updated is how these two silently
    diverge.
    """
    source = (REPO_ROOT / schema_path).read_text(encoding="utf-8")
    for index, (table, columns) in EXPECTED.items():
        pattern = (
            rf"CREATE INDEX IF NOT EXISTS\s+{index}\s+ON\s+{table}\s*\(\s*"
            + r"\s*,\s*".join(columns)
            + r"\s*\)"
        )
        assert re.search(pattern, source), f"{schema_path} is missing {index}"


# ── 4. PostgreSQL: the planner actually CHOOSES the composite ────────────────

_BENCH_SCHEMA = "ctx_perf_05_test"


def _pg_conn():
    """A raw PG connection, or None when PostgreSQL is not reachable."""
    try:
        import psycopg2
    except ImportError:  # pragma: no cover - psycopg2 optional
        return None
    try:
        conn = psycopg2.connect(
            host=os.environ.get("ICDEV_PG_HOST", "localhost"),
            port=int(os.environ.get("ICDEV_PG_PORT", "5432")),
            user=os.environ.get("ICDEV_PG_USER", "icdev"),
            password=os.environ.get("ICDEV_PG_PASSWORD", "icdev_dev_2026"),
            dbname=os.environ.get("ICDEV_PG_DATABASE", "icdev"),
            connect_timeout=5,
        )
    except Exception:  # noqa: BLE001 - no PG in this environment
        return None
    conn.autocommit = True
    return conn


def test_pg_planner_picks_the_composite_for_the_metrics_window_query():
    """EXPLAIN the real _scan query shape against a seeded, isolated schema.

    A plan assertion needs enough rows that a sequential scan is genuinely the
    wrong answer — on the ~200 rows a dev database holds, the planner correctly
    ignores every index and the test would assert nothing. So this seeds its own
    schema and drops it again; it never reads or writes ``public``.
    """
    conn = _pg_conn()
    if conn is None:
        pytest.skip("PostgreSQL not reachable")

    cur = conn.cursor()
    try:
        cur.execute(f"DROP SCHEMA IF EXISTS {_BENCH_SCHEMA} CASCADE")
        cur.execute(f"CREATE SCHEMA {_BENCH_SCHEMA}")
        cur.execute(
            f"""
            CREATE TABLE {_BENCH_SCHEMA}.cortex_audit (
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
        # The 262 indexes, i.e. the state this migration improves on.
        cur.execute(f"CREATE INDEX ON {_BENCH_SCHEMA}.cortex_audit(tenant_id)")
        cur.execute(f"CREATE INDEX ON {_BENCH_SCHEMA}.cortex_audit(created_at)")
        cur.execute(
            f"""
            INSERT INTO {_BENCH_SCHEMA}.cortex_audit
                (id, tenant_id, classification, function, outcome, blocked, created_at)
            SELECT md5(g::text), 'tenant-' || mod(g, 40),
                   (ARRAY['CUI','UNCLASSIFIED','SECRET'])[1 + mod(g, 3)],
                   (ARRAY['ask','search','reason'])[1 + mod(g, 3)],
                   (ARRAY['pass','warn','fail','blocked'])[1 + mod(g, 4)],
                   (mod(g, 17) = 0),
                   NOW() - (mod(g, 129600) * INTERVAL '1 minute')
            FROM generate_series(1, 200000) g
            """
        )
        cur.execute(f"ANALYZE {_BENCH_SCHEMA}.cortex_audit")

        # tools/cortex/metrics.py::_scan, as _inject_rls rewrites it.
        window_query = f"""
            SELECT function, tenant_id, outcome, blocked, COUNT(*) AS n
            FROM {_BENCH_SCHEMA}.cortex_audit
            WHERE created_at >= %s AND tenant_id = %s AND classification IN (%s, %s)
            GROUP BY function, tenant_id, outcome, blocked
        """
        params = ("2026-08-07 00:00:00", "tenant-7", "CUI", "UNCLASSIFIED")

        cur.execute("EXPLAIN " + window_query, params)
        before = "\n".join(r[0] for r in cur.fetchall())

        cur.execute(
            f"CREATE INDEX idx_cortex_audit_tenant_created "
            f"ON {_BENCH_SCHEMA}.cortex_audit(tenant_id, created_at)"
        )
        cur.execute(f"ANALYZE {_BENCH_SCHEMA}.cortex_audit")

        cur.execute("EXPLAIN " + window_query, params)
        after = "\n".join(r[0] for r in cur.fetchall())
    finally:
        cur.execute(f"DROP SCHEMA IF EXISTS {_BENCH_SCHEMA} CASCADE")
        cur.close()
        conn.close()

    assert "idx_cortex_audit_tenant_created" not in before
    assert "idx_cortex_audit_tenant_created" in after, (
        f"planner did not choose the composite.\nBEFORE:\n{before}\nAFTER:\n{after}"
    )
    # Both halves of the RLS-rewritten predicate are served by the index itself,
    # not rechecked against the heap — that is the difference from the BitmapAnd
    # of two single-column indexes it replaces.
    assert "Index Cond" in after
    assert "tenant_id" in after and "created_at" in after

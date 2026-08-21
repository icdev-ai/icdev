#!/usr/bin/env python3
# CUI // SP-CTI
"""Bootstrap a fresh PostgreSQL database from the consolidated schema snapshot.

The historical 212-migration chain is not cleanly replayable on a fresh
PostgreSQL database (inconsistent directive conventions, unsupported flat-file
.py migrations, and severe create-after-alter ordering). For fresh PG installs
(CI E2E, new deployments) this "squash" loads the authoritative consolidated
schema — a pg_dump of the canonical production schema at
``tools/db/schema/pg_consolidated.sql``.

The snapshot is a point-in-time dump, so it contains the schema only *through*
the migration that was newest when it was taken. That version is recorded in
``pg_consolidated.meta.json`` and it is the pivot for everything below:

* versions **<= through_version** are marked applied without running — their
  DDL is already in the snapshot and the historical chain is not replayable;
* versions **> through_version** are *executed*, because nothing has created
  their objects yet.

Bootstrap previously marked *every* discovered migration applied, including the
ones the snapshot predates. Their DDL therefore never ran anywhere, while
``schema_migrations`` claimed otherwise, so ``migrate.py --up`` had no pending
work to report and nothing anywhere said the database was incomplete. On
2026-07-29 that silently cost the CI E2E database every migration merged since
the snapshot (302-310) — including 306, whose absent ``inputs_json`` column made
every workflow-run POST return 500 and the DWO V&V specs fail in CI while
passing locally against a fully-migrated database.

Regenerate the snapshot with ``tools/db/regen_pg_snapshot.py`` -- NEVER with a
bare ``pg_dump > pg_consolidated.sql``. The snapshot is not only a dump: it
carries tables the canonical database does not have, a hand-maintained
``ICDEV ADDITIVE SECTION`` tail, and columns that tail used to add; a straight
re-dump drops all three silently (it dropped twelve tables once). The runbook
is ``docs/database/pg-snapshot-regeneration.md``.

Regenerate it whenever the canonical schema moves -- left stale, the file is
wrong in a way nothing in CI can see: the CI database is built by init_db and
only MARKED here, so the snapshot's contents are exercised by nothing. Measured
2026-08-21, four weeks stale, a fresh database was short 173 columns across
102 tables, 128 of them with no ALTER anywhere in the tree. Then bump
``through_version`` in ``pg_consolidated.meta.json`` to the highest LEGACY
migration on disk. Leaving it stale-low is safe (the surplus migrations simply
re-run, and every post-snapshot migration is idempotent on PostgreSQL); raising
it past what the dump actually contains is the failure mode, and is what
``tests/db/test_pg_bootstrap_baseline.py`` guards.

Usage:
    python tools/db/bootstrap_pg.py            # load schema + mark migrations applied
    python tools/db/bootstrap_pg.py --check    # report bootstrap state, do nothing
    python tools/db/bootstrap_pg.py --json
"""
import argparse
import json
import os
import re
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

SCHEMA_FILE = BASE_DIR / "tools" / "db" / "schema" / "pg_consolidated.sql"
META_FILE = SCHEMA_FILE.with_suffix(".meta.json")


def snapshot_through_version() -> str:
    """Highest migration version whose DDL the consolidated snapshot contains.

    Read from the sidecar rather than inferred: the snapshot is a dump of a live
    database, so its contents cannot be derived from the migration files alone
    (a table created at runtime by ``init_db.py`` appears in the dump without any
    migration ever having created it).
    """
    if not META_FILE.exists():
        raise SystemExit(
            f"snapshot baseline marker not found: {META_FILE}\n"
            "It records which migrations pg_consolidated.sql already contains. "
            "Without it bootstrap cannot tell which migrations still need to run."
        )
    raw = str(json.loads(META_FILE.read_text(encoding="utf-8")).get("through_version", "")).strip()
    if not raw.isdigit():
        raise SystemExit(f"through_version in {META_FILE} must be a version number, got {raw!r}")
    return raw.zfill(3)


def _version_order_key(version: str) -> tuple:
    """Ordering used by ``MigrationRunner.discover_migrations``.

    Shorter id family first, then numerically within the family, so every
    3-digit legacy version precedes every 14-digit timestamp whatever the
    digits are.
    """
    digits = str(version).strip()
    return (len(digits), digits)


def baseline_versions(all_versions, through: str) -> list:
    """The versions to record as applied without executing them.

    Split out as a pure function so the rule — and only versions the snapshot
    actually covers are ever marked — is testable without a database.

    Ordered by ``_version_order_key``, NOT by plain string comparison. The
    timestamp id family (``YYYYMMDDHHMMSS``, used for everything created from
    2026-08 onward) starts with '2', and ``"20260803204235" <= "301"`` is True
    as strings — so a lexicographic test silently swept EVERY timestamp
    migration into the baseline and marked it applied without running it. That
    is precisely the "value too HIGH" failure this module's docstring says it
    exists to prevent, arriving through the comparison instead of through the
    marker. It cost the 8 timestamp migrations then on main their DDL on every
    fresh PostgreSQL bootstrap, including this one (mvs-audit-03).
    """
    cutoff = _version_order_key(through)
    return sorted(
        (v for v in set(all_versions) if _version_order_key(v) <= cutoff),
        key=_version_order_key,
    )


def _raw_pg_conn(retries: int = 10, backoff: float = 1.5):
    """Raw psycopg2 connection — NOT the StorageConnection wrapper, so the
    already-PostgreSQL dump is executed verbatim without SQL translation.

    Connects with a bounded retry/backoff loop. In CI, PostgreSQL runs in a
    freshly-launched container: ``pg_isready`` can report ready moments before
    the backend truly accepts connections, and a shared-memory spike during
    heavy schema/index work can briefly bounce the backend. A single-shot
    connect turns any of those transient blips into a hard job failure, so we
    retry with capped exponential backoff (and a ``connect_timeout`` so a wedged
    server fails fast instead of hanging) before giving up.
    """
    import time

    import psycopg2

    url = os.environ.get("ICDEV_DATABASE_URL")

    def _connect():
        if url:
            return psycopg2.connect(url, connect_timeout=10)
        host = os.environ.get("ICDEV_PG_HOST", "127.0.0.1")
        if host == "localhost":
            host = "127.0.0.1"
        return psycopg2.connect(
            host=host,
            port=int(os.environ.get("ICDEV_PG_PORT", "5432")),
            user=os.environ.get("ICDEV_PG_USER", "icdev"),
            password=os.environ.get("ICDEV_PG_PASSWORD", ""),
            dbname=os.environ.get("ICDEV_PG_DATABASE", "icdev"),
            connect_timeout=10,
        )

    last_exc = None
    for attempt in range(1, retries + 1):
        try:
            return _connect()
        except psycopg2.OperationalError as exc:
            last_exc = exc
            if attempt >= retries:
                break
            wait = min(backoff ** attempt, 15.0)
            print(
                f"[bootstrap_pg] PG connect attempt {attempt}/{retries} failed: "
                f"{exc}; retrying in {wait:.1f}s",
                file=sys.stderr,
            )
            time.sleep(wait)
    raise last_exc


def _strip_psql_meta(sql: str) -> str:
    """Drop psql-only backslash meta-commands (\\restrict, \\unrestrict, …) that
    pg_dump (PG16+) emits — they are not valid SQL for the wire protocol."""
    return "\n".join(ln for ln in sql.splitlines() if not ln.lstrip().startswith("\\"))


def _table_count(cur) -> int:
    cur.execute("SELECT count(*) FROM information_schema.tables WHERE table_schema='public'")
    return cur.fetchone()[0]


#: Statements per transaction while loading the snapshot. Bounds the locks one
#: transaction holds well under a stock server's 6,400-slot lock table.
LOAD_BATCH = 200
#: Present from the first committed batch until the bookkeeping is written.
PARTIAL_SENTINEL = "schema_bootstrap_partial"

_DOLLAR_TAG_RE = re.compile(r"\$[A-Za-z_][A-Za-z0-9_]*\$|\$\$")


def split_statements(sql: str) -> list:
    """Top-level statements of a pg_dump, in order.

    A statement ends at a ';' that is outside parentheses, outside a
    single-quoted string, outside a ``--`` comment and outside a dollar-quoted
    body (``$$ ... $$`` / ``$tag$ ... $tag$``) -- the function bodies pg_dump
    emits contain ';' on their own lines. Comments between statements travel
    with the statement that follows them.
    """
    out, buf = [], []
    depth = 0
    in_quote = False
    dollar = None
    i, n = 0, len(sql)
    while i < n:
        ch = sql[i]
        if dollar is not None:
            if sql.startswith(dollar, i):
                buf.append(dollar)
                i += len(dollar)
                dollar = None
            else:
                buf.append(ch)
                i += 1
            continue
        if in_quote:
            buf.append(ch)
            if ch == "'":
                if i + 1 < n and sql[i + 1] == "'":
                    buf.append("'")
                    i += 2
                    continue
                in_quote = False
            i += 1
            continue
        if ch == "-" and sql.startswith("--", i):
            j = sql.find("\n", i)
            j = n if j < 0 else j
            buf.append(sql[i:j])
            i = j
            continue
        if ch == "$":
            m = _DOLLAR_TAG_RE.match(sql, i)
            if m:
                dollar = m.group(0)
                buf.append(dollar)
                i += len(dollar)
                continue
        if ch == "'":
            in_quote = True
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        elif ch == ";" and depth <= 0:
            buf.append(ch)
            stmt = "".join(buf).strip()
            if stmt:
                out.append(stmt)
            buf = []
            i += 1
            continue
        buf.append(ch)
        i += 1
    tail = "".join(buf).strip()
    if tail and any(line.strip() and not line.strip().startswith("--") for line in tail.splitlines()):
        out.append(tail)
    return out


def check() -> dict:
    conn = _raw_pg_conn()
    try:
        cur = conn.cursor()
        n = _table_count(cur)
        applied = 0
        cur.execute("SELECT to_regclass('public.schema_migrations')")
        if cur.fetchone()[0]:
            cur.execute("SELECT count(*) FROM public.schema_migrations")
            applied = cur.fetchone()[0]
        cur.execute(f"SELECT to_regclass('public.{PARTIAL_SENTINEL}')")
        partial = cur.fetchone()[0] is not None
        return {"tables": n, "migrations_applied": applied, "bootstrapped": n > 100 and not partial,
                "partial": partial}
    finally:
        conn.close()


def bootstrap() -> dict:
    if not SCHEMA_FILE.exists():
        raise SystemExit(f"consolidated schema not found: {SCHEMA_FILE}")

    # Idempotency guard: if another job in the same CI run already bootstrapped
    # (Test job creates tables via init_icdev_db.py before E2E runs), skip the
    # schema load and only ensure migrations are marked. DuplicateTable errors
    # were caused by unconditional CREATE TABLE execution on a non-empty DB.
    state = check()
    if state["bootstrapped"]:
        # NOTE the asymmetry with the fresh path below, which runs every
        # post-snapshot migration. Here the tables were built by the init_db
        # modules, which declare the *current* shape directly — so the newer
        # migrations' columns already exist and marking them applied is correct
        # by construction. Running them instead would fail: several (e.g. 308)
        # use bare ADD COLUMN, which errors on a column that is already there.
        conn = _raw_pg_conn()
        try:
            cur = conn.cursor()
            cur.execute("SET search_path TO public, pg_catalog")
            from tools.db.migration_runner import MigrationRunner
            runner = MigrationRunner(engine="postgresql")
            versions = sorted({m["version"] for m in runner.discover_migrations()})
            for v in versions:
                cur.execute(
                    "INSERT INTO public.schema_migrations (version, name, checksum, execution_time_ms) "
                    "VALUES (%s, %s, '', 0) ON CONFLICT (version) DO NOTHING",
                    (v, f"squashed-{v}"),
                )
            conn.commit()
            return {
                "status": "already_bootstrapped",
                "tables": state["tables"],
                "migrations_marked": len(versions),
            }
        finally:
            conn.close()

    if state.get("partial"):
        raise SystemExit(
            "a previous bootstrap of this database stopped part-way (the "
            f"{PARTIAL_SENTINEL} sentinel is present). It holds some of the schema and none "
            "of the migration bookkeeping, so marking it would leave a database that claims "
            "to be current and is not. Drop and recreate it, then bootstrap again."
        )

    sql = _strip_psql_meta(SCHEMA_FILE.read_text(encoding="utf-8-sig"))  # utf-8-sig strips any BOM

    conn = _raw_pg_conn()
    try:
        cur = conn.cursor()
        # Loaded in BATCHES, each its own transaction -- NOT as one statement.
        # One transaction locks every object it creates until commit, and
        # PostgreSQL's lock table is max_locks_per_transaction x max_connections
        # (64 x 100 = 6,400 slots on a stock server). The regenerated snapshot
        # (1,818 tables, 2,732 indexes, 3,032 ALTERs) overflowed that on the CI
        # service container with `out of shared memory / increase
        # max_locks_per_transaction` while loading fine on a developer instance
        # with max_connections=300 -- exactly the kind of "works here" a fresh
        # deployment on a stock server would hit first. The sentinel table is
        # committed BEFORE the first batch and dropped after the bookkeeping, so
        # a load that dies between them is detectable (see check()).
        cur.execute(f"CREATE TABLE IF NOT EXISTS public.{PARTIAL_SENTINEL} (started_at TIMESTAMPTZ DEFAULT now())")
        cur.execute(f"INSERT INTO public.{PARTIAL_SENTINEL} DEFAULT VALUES")
        conn.commit()
        statements = split_statements(sql)
        for start in range(0, len(statements), LOAD_BATCH):
            batch = statements[start:start + LOAD_BATCH]
            try:
                cur.execute("\n".join(batch))
                conn.commit()
            except Exception as exc:
                conn.rollback()
                raise SystemExit(
                    f"[bootstrap_pg] snapshot load failed in batch {start // LOAD_BATCH + 1} "
                    f"(statements {start + 1}-{start + len(batch)} of {len(statements)}): {exc}\n"
                    f"The database is PARTIAL and is marked so; drop and recreate it before retrying."
                ) from exc

        # The dump sets search_path='' for safety; restore it so unqualified
        # names resolve for the migration bookkeeping below.
        cur.execute("SET search_path TO public, pg_catalog")

        # Mark only the migrations the snapshot already contains. Anything newer
        # is left pending on purpose — the snapshot predates it, so its objects
        # do not exist yet and it has to actually run (see module docstring).
        from tools.db.migration_runner import MigrationRunner

        runner = MigrationRunner(engine="postgresql")
        through = snapshot_through_version()
        versions = baseline_versions(
            (m["version"] for m in runner.discover_migrations()), through
        )
        for v in versions:
            cur.execute(
                "INSERT INTO public.schema_migrations (version, name, checksum, execution_time_ms) "
                "VALUES (%s, %s, '', 0) ON CONFLICT (version) DO NOTHING",
                (v, f"squashed-{v}"),
            )
        conn.commit()
        # Bookkeeping written: the load is whole. Only now does the sentinel go.
        cur.execute(f"DROP TABLE IF EXISTS public.{PARTIAL_SENTINEL}")
        conn.commit()
        table_count = _table_count(cur)
    finally:
        conn.close()

    # Apply the post-snapshot migrations. Runs on its own connection (the
    # runner manages its own), after the baseline rows are committed, so
    # get_pending_migrations sees exactly the tail.
    applied, failures = _apply_post_snapshot_migrations(runner)

    return {
        "status": "bootstrapped",
        "tables": table_count,
        "migrations_marked": len(versions),
        "baseline_through": through,
        "migrations_applied": applied,
        "failed": failures,
    }


def _apply_post_snapshot_migrations(runner) -> tuple:
    """Run every migration newer than the snapshot, loudest failure first.

    A failure here must not be swallowed: a half-migrated database that reports
    success is precisely the state that let the missing ``inputs_json`` column
    reach CI disguised as a test bug.
    """
    results = runner.migrate_up()
    applied = [r.get("version") for r in results if r.get("success")]
    failures = [
        {"version": r.get("version"), "error": str(r.get("error"))[:400]}
        for r in results
        if not r.get("success")
    ]
    for f in failures:
        print(
            f"[bootstrap_pg] post-snapshot migration {f['version']} FAILED: {f['error']}",
            file=sys.stderr,
        )
    if failures:
        raise SystemExit(
            f"{len(failures)} post-snapshot migration(s) failed; the database is "
            f"incomplete. Failing loudly rather than leaving a schema that "
            f"claims to be current: {[f['version'] for f in failures]}"
        )
    return applied, failures


def main():
    ap = argparse.ArgumentParser(description="Bootstrap fresh PostgreSQL from consolidated schema")
    ap.add_argument("--check", action="store_true", help="Report state only; do not modify")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    result = check() if args.check else bootstrap()
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        for k, v in result.items():
            print(f"{k}: {v}")


if __name__ == "__main__":
    main()

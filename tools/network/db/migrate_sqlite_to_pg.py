# [CUI // SP-CTI]
"""Migrate the Network Design Canvas dataset from SQLite → PostgreSQL.

PostgreSQL is the primary store for ICDEV™ (NC_STORAGE_BACKEND=postgresql).
Historically the network canvas seeded into ``data/network_canvas.db`` (SQLite);
this one-shot, idempotent migrator lifts that data into the PG ``network_canvas``
database so the dashboard (which runs on PG) serves the real topologies, device
configs, migration phases, SOPs, and STIG findings.

Usage:
    NC_STORAGE_BACKEND=postgresql python -m tools.network.db.migrate_sqlite_to_pg
    NC_STORAGE_BACKEND=postgresql python -m tools.network.db.migrate_sqlite_to_pg --tables topologies,ni_devices

Idempotent: rows are inserted with ON CONFLICT DO NOTHING. Immutable audit
tables are skipped. Only columns present in BOTH schemas are copied.
"""

from __future__ import annotations

import argparse
import os
import sqlite3
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[3]
_SQLITE_PATH = _ROOT / "data" / "network_canvas.db"

# Never touch these (append-only/immutable or internal).
_SKIP = {"nc_audit", "sqlite_sequence"}


def _pg_columns(pg, table: str) -> list[str]:
    rows = pg.execute(
        "SELECT column_name FROM information_schema.columns WHERE table_name=?",
        (table,),
    ).fetchall()
    out = []
    for r in rows:
        out.append(r[0] if not hasattr(r, "keys") else r["column_name"])
    return out


def _sqlite_tables(sq: sqlite3.Connection) -> list[str]:
    return [r[0] for r in sq.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()]


# SQLite affinity → PostgreSQL type (permissive; preserves data, not constraints).
def _pg_type(sqlite_type: str) -> str:
    t = (sqlite_type or "").upper()
    if "INT" in t:
        return "BIGINT"
    if any(x in t for x in ("REAL", "FLOA", "DOUB")):
        return "DOUBLE PRECISION"
    if "BOOL" in t:
        return "BOOLEAN"
    if "BLOB" in t:
        return "BYTEA"
    return "TEXT"


def _ensure_table_pg(pg, sq: sqlite3.Connection, table: str) -> bool:
    """Create `table` in PG from its SQLite definition if it doesn't exist.

    Generic column-type mapping; declares the SQLite primary key so ON CONFLICT
    DO NOTHING works for idempotent re-runs. Returns True if the table exists
    (already or newly created).
    """
    if _pg_columns(pg, table):
        return True
    cols = sq.execute(f'PRAGMA table_info("{table}")').fetchall()
    if not cols:
        return False
    defs, pk = [], []
    for c in cols:
        name = c["name"]
        defs.append(f'"{name}" {_pg_type(c["type"])}')
        if c["pk"]:
            pk.append(f'"{name}"')
    if pk:
        defs.append(f'PRIMARY KEY ({", ".join(pk)})')
    ddl = f'CREATE TABLE IF NOT EXISTS "{table}" ({", ".join(defs)})'
    try:
        pg.execute(ddl)
        pg.commit()
        return bool(_pg_columns(pg, table))
    except Exception:
        try:
            pg.rollback()
        except Exception:
            pass
        return False


def migrate(only: list[str] | None = None, verbose: bool = True) -> dict:
    if os.environ.get("NC_STORAGE_BACKEND", "").lower() != "postgresql":
        raise SystemExit("Refusing to run: set NC_STORAGE_BACKEND=postgresql first.")

    # Ensure the PG schema exists. NOTE: init_db()'s PG path runs every DDL
    # statement in ONE transaction, so the first failure aborts the rest and
    # most tables are never created. We instead run each statement in its own
    # committed transaction so every CREATE TABLE is independent.
    from tools.network.db import init_db as _init
    from tools.db.storage import get_connection as _pg_conn0
    schema = getattr(_init, "SCHEMA", "")
    created = 0
    for stmt in schema.split(";"):
        stmt = stmt.strip()
        if not stmt or stmt.startswith("--"):
            continue
        c = _pg_conn0(db_path="network_canvas")
        try:
            c.execute(stmt)
            c.commit()
            created += 1
        except Exception:
            try:
                c.rollback()
            except Exception:
                pass
        finally:
            try:
                c.close()
            except Exception:
                pass
    print(f"[schema] executed {created} DDL statement(s) independently")

    from tools.db.storage import get_connection as _pg_conn

    sq = sqlite3.connect(str(_SQLITE_PATH))
    sq.row_factory = sqlite3.Row

    tables = only or [t for t in _sqlite_tables(sq) if t not in _SKIP]
    report: dict[str, str] = {}

    # Ensure EVERY network table exists in PG up-front — including empty ones.
    # Routes query tables regardless of whether they hold demo data; a missing
    # table raises UndefinedTable and 500s the page. (Data copy below only
    # touches non-empty tables, so empty tables would otherwise never be created.)
    ensured = 0
    for table in tables:
        if table in _SKIP:
            continue
        pg = _pg_conn(db_path="network_canvas")
        try:
            if _ensure_table_pg(pg, sq, table):
                ensured += 1
        except Exception:
            pass
        finally:
            try:
                pg.close()
            except Exception:
                pass
    print(f"[schema] ensured {ensured} table(s) exist in PG")

    for table in tables:
        if table in _SKIP:
            report[table] = "skipped (protected)"
            continue
        try:
            src_rows = sq.execute(f'SELECT * FROM "{table}"').fetchall()
        except Exception as exc:
            report[table] = f"read-error: {exc}"
            continue
        if not src_rows:
            report[table] = "empty (0)"
            continue

        # Fresh PG connection per table so a failure can't poison others.
        pg = _pg_conn(db_path="network_canvas")
        try:
            if not _ensure_table_pg(pg, sq, table):
                report[table] = "no PG table (create failed) — skipped"
                pg.close()
                if verbose:
                    print(f"  {table:30s} {report[table]}")
                continue
            pg_cols = set(_pg_columns(pg, table))
            src_cols = [c for c in src_rows[0].keys() if c in pg_cols]
            if not src_cols:
                report[table] = "no shared columns — skipped"
                pg.close()
                continue

            placeholders = ",".join(["?"] * len(src_cols))
            collist = ",".join(f'"{c}"' for c in src_cols)
            sql = f'INSERT INTO "{table}" ({collist}) VALUES ({placeholders}) ON CONFLICT DO NOTHING'

            inserted = 0
            errors = 0
            for row in src_rows:
                vals = [row[c] for c in src_cols]
                try:
                    pg.execute(sql, vals)
                    pg.commit()  # per-row so one bad row can't roll back good ones
                    inserted += 1
                except Exception:
                    errors += 1
                    try:
                        pg.rollback()
                    except Exception:
                        pass
            report[table] = f"copied {inserted}/{len(src_rows)}" + (f" ({errors} err)" if errors else "")
        except Exception as exc:
            report[table] = f"error: {exc}"
        finally:
            try:
                pg.close()
            except Exception:
                pass
        if verbose:
            print(f"  {table:30s} {report[table]}")

    sq.close()
    return report


def main():
    ap = argparse.ArgumentParser(description="Migrate network_canvas SQLite → PostgreSQL")
    ap.add_argument("--tables", help="comma-separated subset of tables")
    args = ap.parse_args()
    only = [t.strip() for t in args.tables.split(",")] if args.tables else None
    print(f"Migrating network_canvas SQLite → PostgreSQL (source: {_SQLITE_PATH})")
    report = migrate(only=only)
    ok = sum(1 for v in report.values() if v.startswith("copied"))
    print(f"\nDone. {ok} table(s) copied.")


if __name__ == "__main__":
    main()

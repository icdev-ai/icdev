#!/usr/bin/env python3
# CUI // SP-CTI
"""Regenerate ``tools/db/schema/pg_consolidated.sql`` without losing what pg_dump cannot see.

The consolidated snapshot is MOSTLY a ``pg_dump --schema-only`` of the canonical
database -- and not only that. Two kinds of content live in it that a straight
re-dump silently drops, and both have been dropped before:

* **carried-forward tables** -- declared by init_db / a migration, absent from
  the canonical database, so pg_dump never emits them (the first regeneration
  lost twelve of them with no error anywhere);
* **the hand-maintained tail** -- the ``ICDEV ADDITIVE SECTION`` blocks
  (Security Design Canvas, Pipeline Design Canvas, FedRAMP ...), all
  ``CREATE ... IF NOT EXISTS``, appended after the dump.

And one kind of content that a re-dump silently *shrinks*: a table the tail
declares with a NEWER shape than the canonical database has. ``CREATE TABLE IF
NOT EXISTS`` is a no-op once the dump region creates the table, so the tail's
extra columns stop arriving. Measured 2026-08-21: 28 columns on 10 tables.

The other way the snapshot goes wrong is staleness. It was taken 2026-07-26
(``through_version`` 301) and hand-extended for four weeks; measured against
the canonical database on 2026-08-21 a fresh bootstrap was short **173 columns
across 102 tables**, 128 of them with no ALTER anywhere in the tree (RLS
``tenant_id`` columns, ``kanban_tasks`` runtime columns, ...), because bootstrap
MARKS every version <= through_version as applied without running it. Nothing
in CI could see that: the CI database is built by init_db first and only marked
by bootstrap, so the snapshot's own contents had been exercised by nothing since
July. ICDEV[FT]'s ``icdev_ft`` -- the first genuinely fresh database in weeks --
failed on its first document-intelligence INSERT (``dic_chunk_links.chunk_hash``,
migration 267, marked applied, never present).

Procedure (each step is a subcommand; the runbook is
``docs/database/pg-snapshot-regeneration.md``):

    # 1. fresh dump of the canonical database (native pg_dump or docker exec)
    python tools/db/regen_pg_snapshot.py dump --out .tmp/canonical.sql
    # 2. replay the stamped-but-unrun legacy migrations on a SCRATCH database
    #    (bootstrap it with through_version temporarily at the old value), then
    #    dump THAT -- the canonical database carries 302,303,305-309,322-328 as
    #    squashed-* stamps whose DDL never ran there, so its dump alone can never
    #    honestly claim through_version 341
    # 3. what did the previous snapshot build that the fresh dump lacks?
    python tools/db/regen_pg_snapshot.py diff --reference <old-path dsn> --candidate <scratch dsn> --emit-alters .tmp/carry.sql
    # 4. compose: fresh dump + carried tables + carried columns + the old tail
    python tools/db/regen_pg_snapshot.py compose --dump .tmp/scratch.sql --previous tools/db/schema/pg_consolidated.sql --carry-columns .tmp/carry.sql --out tools/db/schema/pg_consolidated.sql
    # 5. bootstrap ANOTHER scratch database from the result and prove it:
    python tools/db/regen_pg_snapshot.py diff --reference <canonical dsn> --candidate <scratch3 dsn>   # must be empty
    # 6. bump through_version in pg_consolidated.meta.json, run tests/db/test_pg_bootstrap_baseline.py

``compose`` and ``diff`` never connect to anything they are not given; ``diff``
is read-only (information_schema + pg_catalog). Nothing here writes
``schema_migrations``.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

SNAPSHOT = BASE_DIR / "tools" / "db" / "schema" / "pg_consolidated.sql"
TAIL_MARK = "-- ICDEV ADDITIVE SECTION"
RULE = "-- " + "=" * 76
CREATE_TABLE_RE = re.compile(r"^CREATE TABLE (?:IF NOT EXISTS )?(?:public\.)?\"?(\w+)\"?\s*\(", re.M)
DEFAULT_CONTAINER = "icdev-postgres"


# ── parsing ───────────────────────────────────────────────────────────────────

def split_previous(text: str) -> tuple[str, str]:
    """(dump region, hand-maintained tail). The tail starts at the rule line
    preceding the first ``ICDEV ADDITIVE SECTION`` header; no header, no tail."""
    i = text.find(TAIL_MARK)
    if i < 0:
        return text, ""
    j = text.rfind(RULE[:6], 0, i)
    return text[:j], text[j:]


def statements(sql: str) -> list[str]:
    """Top-level statements: one ends at a line ending in ';' at paren depth 0.
    Comments and psql meta-commands between statements are dropped."""
    out, buf, depth = [], [], 0
    for line in sql.splitlines(keepends=True):
        s = line.strip()
        if not buf and (not s or s.startswith("--") or s.startswith("\\")):
            continue
        buf.append(line)
        depth += line.count("(") - line.count(")")
        if s.endswith(";") and depth <= 0:
            out.append("".join(buf))
            buf, depth = [], 0
    return out


def tables_in(sql: str) -> list[str]:
    return CREATE_TABLE_RE.findall(sql)


def _refers(stmt: str, table: str) -> bool:
    t = re.escape(table)
    return any(re.search(p, stmt) for p in (
        rf"^CREATE TABLE (?:IF NOT EXISTS )?public\.{t}\s*\(",
        rf"^ALTER TABLE (?:ONLY )?public\.{t}\b",
        rf"^CREATE (?:UNIQUE )?INDEX (?:IF NOT EXISTS )?\S+ ON public\.{t}\b",
        rf"^CREATE SEQUENCE (?:IF NOT EXISTS )?public\.{t}_\w+_seq\b",
        rf"^ALTER SEQUENCE public\.{t}_\w+_seq\b",
    ))


def _idempotent(stmt: str) -> str:
    stmt = re.sub(r"^CREATE TABLE public\.", "CREATE TABLE IF NOT EXISTS public.", stmt)
    stmt = re.sub(r"^CREATE SEQUENCE public\.", "CREATE SEQUENCE IF NOT EXISTS public.", stmt)
    return re.sub(r"^CREATE (UNIQUE )?INDEX (\S+) ON", r"CREATE \1INDEX IF NOT EXISTS \2 ON", stmt)


def carried_statements(previous_dump: str, fresh_dump: str) -> tuple[list[str], list[str]]:
    """Every statement of the previous dump region that builds a table the
    fresh dump does not contain, in the previous order (REFERENCES targets
    precede referrers there), made idempotent. Returns (tables, statements)."""
    fresh = set(tables_in(fresh_dump))
    missing = [t for t in tables_in(previous_dump) if t not in fresh]
    carried = []
    for st in statements(previous_dump):
        if any(_refers(st, t) for t in missing):
            carried.append(_idempotent(st))
    return missing, carried


def compose(fresh_dump: str, previous: str, carry_columns: str = "", generated: str = "") -> dict:
    previous_dump, tail = split_previous(previous)
    missing, carried = carried_statements(previous_dump, fresh_dump)
    parts = [fresh_dump.rstrip("\n"), "\n"]
    stamp = f" (regenerated {generated})" if generated else ""
    parts.append(
        f"\n{RULE}\n-- CARRIED-FORWARD SECTION{stamp} -- APPEND ONLY\n"
        "-- Tables declared by init_db/migrations but ABSENT from the canonical database,\n"
        "-- so pg_dump cannot emit them. Taken verbatim from the previous snapshot in its\n"
        "-- original order (REFERENCES targets precede referrers) and made idempotent.\n"
        "-- See tests/db/test_pg_bootstrap_baseline.py and tools/db/regen_pg_snapshot.py.\n"
        f"{RULE}\n\n"
    )
    parts.append("\n".join(carried) + ("\n\n" if carried else ""))
    if carry_columns.strip():
        parts.append(
            "-- Columns the previous snapshot's additive sections declared on tables the\n"
            "-- canonical database ALSO has (with an older shape), so the idempotent\n"
            "-- declarations below can no longer add them. Typed from a database built by\n"
            "-- the previous snapshot; idempotent.\n" + carry_columns.strip() + "\n\n"
        )
    parts.append(tail)
    text = "".join(parts)
    return {"text": text, "carried_tables": missing, "carried_statements": len(carried),
            "tables": len(set(tables_in(text))), "tail_bytes": len(tail)}


# ── database side ─────────────────────────────────────────────────────────────

def _columns(dsn: str) -> dict:
    import psycopg2

    conn = psycopg2.connect(dsn)
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT c.table_name, c.column_name, format_type(a.atttypid, a.atttypmod), c.column_default "
            "FROM information_schema.columns c "
            "JOIN pg_attribute a ON a.attrelid = ('public.' || quote_ident(c.table_name))::regclass "
            "AND a.attname = c.column_name WHERE c.table_schema = 'public'"
        )
        out: dict = {}
        for table, col, typ, default in cur.fetchall():
            out.setdefault(table, {})[col] = (typ, default)
        return out
    finally:
        conn.close()


def diff(reference_dsn: str, candidate_dsn: str) -> dict:
    """What the reference has that the candidate lacks. Empty == the candidate
    is a superset, which is the only acceptable verdict for a regenerated snapshot."""
    ref, cand = _columns(reference_dsn), _columns(candidate_dsn)
    tables = sorted(set(ref) - set(cand))
    columns, alters = {}, []
    for table in sorted(set(ref) & set(cand)):
        missing = sorted(set(ref[table]) - set(cand[table]))
        if not missing:
            continue
        columns[table] = missing
        for col in missing:
            typ, default = ref[table][col]
            stmt = f"ALTER TABLE public.{table} ADD COLUMN IF NOT EXISTS {col} {typ}"
            if default and "nextval" not in default:
                stmt += f" DEFAULT {default}"
            alters.append(stmt + ";")
    return {"missing_tables": tables, "missing_columns": columns,
            "missing_column_count": sum(len(v) for v in columns.values()),
            "alters": alters, "superset": not tables and not columns}


def pg_dump_command(container: str | None) -> list[str] | None:
    if shutil.which("pg_dump"):
        return ["pg_dump"]
    if container and shutil.which("docker"):
        probe = subprocess.run(["docker", "ps", "--format", "{{.Names}}"], capture_output=True, text=True, check=False)
        if container in probe.stdout.split():
            return ["docker", "exec", container, "pg_dump"]
    return None


def dump(dsn: str, out: Path, container: str | None = DEFAULT_CONTAINER) -> dict:
    m = re.match(r"postgres(?:ql)?://([^:]+):([^@]*)@([^:/]+)(?::(\d+))?/([^?]+)", dsn)
    if not m:
        raise SystemExit("dump needs a postgresql://user:pass@host[:port]/db DSN")
    user, pw, host, port, db = m.groups()
    cmd = pg_dump_command(container)
    if cmd is None:
        raise SystemExit("no pg_dump reachable (native or via docker container)")
    args = ["--schema-only", "--no-owner", "--no-privileges", "--no-comments", "-U", user, "-d", db]
    env = {**os.environ, "PGPASSWORD": pw}
    if cmd[0] == "docker":
        cmd = cmd[:2] + ["-e", f"PGPASSWORD={pw}"] + cmd[2:]
    else:
        args += ["-h", host, "-p", port or "5432"]
    res = subprocess.run(cmd + args, capture_output=True, env=env, check=False)
    if res.returncode != 0:
        raise SystemExit("pg_dump failed: " + res.stderr.decode(errors="replace")[-600:])
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(res.stdout)
    text = res.stdout.decode("utf-8", errors="replace")
    return {"out": str(out), "bytes": len(res.stdout), "tables": len(tables_in(text)), "via": cmd[0]}


# ── CLI ───────────────────────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)

    d = sub.add_parser("dump", help="pg_dump --schema-only of one database")
    d.add_argument("--dsn", default=os.environ.get("ICDEV_DATABASE_URL"))
    d.add_argument("--out", required=True, type=Path)
    d.add_argument("--container", default=DEFAULT_CONTAINER)

    c = sub.add_parser("compose", help="fresh dump + carried tables/columns + previous tail")
    c.add_argument("--dump", required=True, type=Path, help="fresh pg_dump --schema-only output")
    c.add_argument("--previous", type=Path, default=SNAPSHOT)
    c.add_argument("--carry-columns", type=Path, help="SQL file of ALTER ... ADD COLUMN IF NOT EXISTS (from `diff --emit-alters`)")
    c.add_argument("--out", required=True, type=Path)
    c.add_argument("--generated", default="", help="date stamp written into the section header")

    f = sub.add_parser("diff", help="what the reference database has that the candidate lacks")
    f.add_argument("--reference", required=True)
    f.add_argument("--candidate", required=True)
    f.add_argument("--emit-alters", type=Path, help="write typed ADD COLUMN IF NOT EXISTS statements here")
    f.add_argument("--json", action="store_true")

    args = ap.parse_args(argv)
    if args.cmd == "dump":
        if not args.dsn:
            raise SystemExit("--dsn or ICDEV_DATABASE_URL is required")
        print(json.dumps(dump(args.dsn, args.out, args.container), indent=2))
        return 0
    if args.cmd == "compose":
        carry = args.carry_columns.read_text(encoding="utf-8") if args.carry_columns else ""
        res = compose(args.dump.read_text(encoding="utf-8-sig"), args.previous.read_text(encoding="utf-8-sig"),
                      carry, args.generated)
        args.out.write_text(res.pop("text"), encoding="utf-8", newline="\n")
        res["out"] = str(args.out)
        print(json.dumps(res, indent=2))
        return 0
    res = diff(args.reference, args.candidate)
    if args.emit_alters:
        args.emit_alters.write_text("\n".join(res["alters"]) + ("\n" if res["alters"] else ""), encoding="utf-8", newline="\n")
    if args.json:
        print(json.dumps(res, indent=2))
    else:
        print(f"missing tables: {len(res['missing_tables'])} {res['missing_tables'][:10]}")
        print(f"missing columns: {res['missing_column_count']} across {len(res['missing_columns'])} tables")
        for t, cols in list(res["missing_columns"].items())[:30]:
            print(f"  {t}: {cols}")
        print("superset" if res["superset"] else "NOT a superset")
    return 0 if res["superset"] else 1


if __name__ == "__main__":
    sys.exit(main())

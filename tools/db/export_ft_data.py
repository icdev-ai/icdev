# CUI // SP-CTI
"""FathomDesk data export for the ICDEV[FT] cutover (xit-cut-01) -- READ-ONLY.

WHY THE TABLE LIST IS ENUMERATED FROM THE DDL, NOT FROM MIGRATIONS OR A GLOB
----------------------------------------------------------------------------
The live ICDEV[IT] PostgreSQL database holds 137 ad_* tables, but only 11 of
them came from migrations: 156 `CREATE TABLE IF NOT EXISTS` sites in code
(tools/trading/db.py alone has 51) create the rest at runtime. An export keyed
on the migration list misses most of them; an `ad_*` glob misses the tables
FathomDesk owns under other names (trading_daemon_audit,
trading_daemon_reflex_state) and would happily take the shared knowledge-graph
tables (kg_nodes, kg_edges, kg_graphs) that tools/trading/db.py ALSO declares
but which belong to the core. So the list is the set of tables DECLARED BY A
FATHOMDESK SOURCE and by nothing else -- measured, then cross-checked against
the live catalog: a declared table that does not exist live is reported
(`declared_but_absent`), never silently skipped, and a shared table is
excluded by name (`excluded_shared`).

WHAT IT DOES (and what it never does)
-------------------------------------
* --dry-run   list the tables with live existence and row counts. Read-only.
* --export D  for every table that exists: `COPY (SELECT * FROM t) TO STDOUT
              WITH CSV HEADER` into D/data/<t>.csv, a per-table row count and
              sha256 into D/manifest.json, and the schema into D/schema.sql --
              via `pg_dump --schema-only` when a binary is reachable (natively
              or `docker exec <container> pg_dump`), otherwise RECONSTRUCTED
              from information_schema (columns, types, defaults, nullability,
              primary keys) and labelled as such. Read-only on the source.
* --verify M  count every table in M against a target database (default: the
              same DSN, a self-check) and exit 1 on any difference. The
              cutover's "manifest diff == 0" gate (docs/programmes/
              icdev-domain-split.md, P3). Read-only on the target.

It never DROPs, DELETEs, TRUNCATEs or writes to either database. The import
into icdev_ft is xft-data-01, in the ICDEV[FT] repository. This module does
not call assert_identity on purpose: --verify is meant to be pointed at the
OTHER parent's database. It lives under tools/db rather than tools/trading
because .gitignore:243 ignores new files under tools/trading/ and the leak gate
will deny that path after the removal -- cutover tooling must outlive the code
it exports.

    python tools/db/export_ft_data.py --dry-run
    python tools/db/export_ft_data.py --export <dir> [--tables t1 t2]
    python tools/db/export_ft_data.py --verify <dir>/manifest.json [--target-dsn ...]
    python tools/db/export_ft_data.py --list --json
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


def _find_repo_root(start: Path) -> Path:
    # By MARKER, never by hop count: the icdev/tools/db/ mirror copy of this
    # file sits one level deeper, and a parents[n] root there is <repo>/icdev.
    for candidate in (start, *start.parents):
        if (candidate / "pyproject.toml").exists() or (candidate / ".git").exists():
            return candidate
    return start


REPO = _find_repo_root(Path(__file__).resolve().parent)
DSN_ENV = "ICDEV_DATABASE_URL"
DEFAULT_PG_CONTAINER = "icdev-postgres"

#: Sources that make a table FathomDesk's. A table declared here AND elsewhere
#: is shared and is excluded.
FATHOMDESK_SOURCES = (
    "tools/trading/",
    "tools/fathomdesk/",
    "tools/genesis/reflexes/fathomdesk_",
    "tools/genesis/reflexes/pmo_",
    "tools/db/migrate_durable_compounders.py",
)
_MIGRATION_MARKERS = ("_ad_", "/ad_", "options_coach", "fathomdesk", "pmo_")
_CREATE_RE = re.compile(r"CREATE\s+(?:VIRTUAL\s+)?TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?(?:\"?\w+\"?\.)?\"?(\w+)\"?", re.I)
_NOISE = {"if", "not", "exists", "table", "is", "as", "select", "from", "into", "like"}
_SKIP_DIRS = {"__pycache__", ".git", "node_modules"}


# ── enumeration ──────────────────────────────────────────────────────────────
def _is_fathomdesk_source(rel: str) -> bool:
    if rel.startswith(FATHOMDESK_SOURCES):
        return True
    if rel.startswith("tools/db/migrations/") and any(m in rel for m in _MIGRATION_MARKERS):
        return True
    return False


def enumerate_tables(repo: Path = REPO) -> dict:
    """Tables declared by FathomDesk sources. Returns {tables, excluded_shared, declared_in}."""
    declared: dict[str, set[str]] = defaultdict(set)
    for base in ("tools",):
        for p in (repo / base).rglob("*"):
            if p.suffix not in (".py", ".sql") or any(part in _SKIP_DIRS for part in p.parts):
                continue
            try:
                text = p.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            rel = p.relative_to(repo).as_posix()
            if rel.startswith("icdev/"):
                continue
            for m in _CREATE_RE.finditer(text):
                name = m.group(1)
                # prose ("CREATE TABLE from ...") and f-string templates
                # ("CREATE TABLE IF NOT EXISTS ad_{suffix}") are not tables
                if name.lower() in _NOISE or name.startswith("{") or name.endswith("_") or len(name) < 4:
                    continue
                declared[name].add(rel)
    tables: dict[str, list[str]] = {}
    excluded: dict[str, list[str]] = {}
    for name, files in declared.items():
        fd = sorted(f for f in files if _is_fathomdesk_source(f))
        other = sorted(f for f in files if not _is_fathomdesk_source(f))
        if name.startswith("ad_"):
            # The ad_ namespace is FathomDesk's wherever the CREATE happens to
            # live -- 017_orphan_tables/up.py and other unmarked migrations
            # declare dozens of them.
            tables[name] = fd or other
        elif not fd:
            continue
        elif other:
            excluded[name] = other  # declared by a FathomDesk source AND the core (kg_nodes ...)
        else:
            tables[name] = fd
    return {
        "tables": sorted(tables),
        "declared_in": tables,
        "excluded_shared": {k: v for k, v in sorted(excluded.items())},
    }


# ── database ────────────────────────────────────────────────────────────────
def redact_dsn(dsn: str) -> str:
    try:
        parts = urlsplit(dsn)
    except ValueError:
        return "<unparseable>"
    netloc = parts.netloc
    if "@" in netloc:
        creds, host = netloc.rsplit("@", 1)
        user = creds.split(":", 1)[0]
        netloc = f"{user}:***@{host}"
    return urlunsplit((parts.scheme, netloc, parts.path, "", ""))


def connect(dsn: str):
    import psycopg2  # noqa: PLC0415 -- declared; PG is the primary backend

    conn = psycopg2.connect(dsn)
    conn.set_session(readonly=True, autocommit=True)
    return conn


def live_tables(conn, candidates: list[str]) -> dict[str, int | None]:
    """{table: row_count or None when absent} -- read-only."""
    cur = conn.cursor()
    cur.execute("SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'")
    present = {r[0] for r in cur.fetchall()}
    out: dict[str, int | None] = {}
    for t in candidates:
        if t not in present:
            out[t] = None
            continue
        cur.execute(f'SELECT count(*) FROM "{t}"')
        out[t] = int(cur.fetchone()[0])
    return out


def _columns(conn, table: str) -> list[dict]:
    cur = conn.cursor()
    cur.execute(
        """SELECT column_name, data_type, character_maximum_length, is_nullable, column_default
           FROM information_schema.columns WHERE table_schema='public' AND table_name=%s
           ORDER BY ordinal_position""",
        (table,),
    )
    return [{"name": r[0], "type": r[1], "length": r[2], "nullable": r[3] == "YES", "default": r[4]} for r in cur.fetchall()]


def _primary_key(conn, table: str) -> list[str]:
    cur = conn.cursor()
    cur.execute(
        """SELECT kcu.column_name FROM information_schema.table_constraints tc
           JOIN information_schema.key_column_usage kcu
             ON tc.constraint_name = kcu.constraint_name AND tc.table_schema = kcu.table_schema
           WHERE tc.table_schema='public' AND tc.table_name=%s AND tc.constraint_type='PRIMARY KEY'
           ORDER BY kcu.ordinal_position""",
        (table,),
    )
    return [r[0] for r in cur.fetchall()]


def reconstruct_ddl(table: str, columns: list[dict], pk: list[str]) -> str:
    """Best-effort CREATE TABLE from the catalog -- labelled RECONSTRUCTED."""
    cols = []
    for c in columns:
        typ = c["type"]
        if c.get("length") and typ in ("character varying", "character"):
            typ = f"{typ}({c['length']})"
        line = f'    "{c["name"]}" {typ}'
        if c.get("default"):
            line += f" DEFAULT {c['default']}"
        if not c.get("nullable", True):
            line += " NOT NULL"
        cols.append(line)
    if pk:
        cols.append("    PRIMARY KEY (" + ", ".join(f'"{k}"' for k in pk) + ")")
    return f'CREATE TABLE IF NOT EXISTS "{table}" (\n' + ",\n".join(cols) + "\n);\n"


def pg_dump_command(container: str | None) -> list[str] | None:
    if shutil.which("pg_dump"):
        return ["pg_dump"]
    if container and shutil.which("docker"):
        probe = subprocess.run(["docker", "ps", "--format", "{{.Names}}"], capture_output=True, text=True, check=False)
        if container in probe.stdout.split():
            return ["docker", "exec", container, "pg_dump"]
    return None


def dump_schema(dsn: str, tables: list[str], out: Path, container: str | None, conn) -> str:
    """Write schema.sql; return 'pg_dump' or 'reconstructed'."""
    cmd = pg_dump_command(container)
    if cmd:
        args = cmd + ["--schema-only", "--no-owner", "--no-acl"]
        for t in tables:
            args += ["-t", f"public.{t}"]
        # inside the container the server is local; the DSN's host may not resolve there
        dsn_for_dump = dsn
        if cmd[0] == "docker":
            p = urlsplit(dsn)
            dsn_for_dump = urlunsplit((p.scheme, (p.netloc.rsplit("@", 1)[0] + "@localhost:5432") if "@" in p.netloc else "localhost:5432", p.path, "", ""))
        args.append(dsn_for_dump)
        res = subprocess.run(args, capture_output=True, text=True, check=False)
        if res.returncode == 0 and res.stdout.strip():
            out.write_text(res.stdout, encoding="utf-8", newline="\n")
            return "pg_dump"
    lines = ["-- RECONSTRUCTED from information_schema (pg_dump was not reachable).",
             "-- Column types, defaults, nullability and primary keys only; indexes,",
             "-- CHECK constraints and foreign keys are NOT represented. Prefer pg_dump.", ""]
    for t in tables:
        lines.append(reconstruct_ddl(t, _columns(conn, t), _primary_key(conn, t)))
    out.write_text("\n".join(lines), encoding="utf-8", newline="\n")
    return "reconstructed"


def export(dsn: str, out_dir: Path, only: list[str] | None = None, container: str | None = DEFAULT_PG_CONTAINER,
           repo: Path = REPO) -> dict:
    enum = enumerate_tables(repo)
    candidates = [t for t in enum["tables"] if not only or t in only]
    conn = connect(dsn)
    try:
        counts = live_tables(conn, candidates)
        present = [t for t in candidates if counts[t] is not None]
        absent = [t for t in candidates if counts[t] is None]
        data_dir = out_dir / "data"
        data_dir.mkdir(parents=True, exist_ok=True)
        manifest_tables: dict[str, dict] = {}
        cur = conn.cursor()
        for t in present:
            target = data_dir / f"{t}.csv"
            with target.open("w", encoding="utf-8", newline="") as fh:
                cur.copy_expert(f'COPY (SELECT * FROM "{t}") TO STDOUT WITH CSV HEADER', fh)
            digest = hashlib.sha256(target.read_bytes()).hexdigest()
            manifest_tables[t] = {
                "rows": counts[t],
                "columns": [c["name"] for c in _columns(conn, t)],
                "csv": f"data/{t}.csv",
                "csv_sha256": digest,
                "declared_in": enum["declared_in"].get(t, []),
            }
        schema_method = dump_schema(dsn, present, out_dir / "schema.sql", container, conn)
    finally:
        conn.close()
    manifest = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": redact_dsn(dsn),
        "schema_method": schema_method,
        "tables": manifest_tables,
        "declared_but_absent": absent,
        "excluded_shared": enum["excluded_shared"],
        "total_rows": sum(v["rows"] for v in manifest_tables.values()),
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8", newline="\n")
    return manifest


# ── verify ───────────────────────────────────────────────────────────────────
def diff_counts(expected: dict[str, int], observed: dict[str, int | None]) -> dict:
    """Pure: the row-count differences between a manifest and a target."""
    missing = sorted(t for t, n in observed.items() if n is None)
    mismatched = {t: {"expected": expected[t], "observed": observed[t]}
                  for t in expected if observed.get(t) is not None and observed[t] != expected[t]}
    return {
        "tables": len(expected),
        "matched": len([t for t in expected if observed.get(t) == expected[t]]),
        "missing_on_target": missing,
        "row_count_mismatch": mismatched,
        "ok": not missing and not mismatched,
    }


def verify(manifest_path: Path, target_dsn: str) -> dict:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    expected = {t: int(v["rows"]) for t, v in manifest["tables"].items()}
    conn = connect(target_dsn)
    try:
        observed = live_tables(conn, sorted(expected))
    finally:
        conn.close()
    report = diff_counts(expected, observed)
    report["target"] = redact_dsn(target_dsn)
    report["manifest"] = str(manifest_path)
    return report


# ── CLI ──────────────────────────────────────────────────────────────────────
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--list", action="store_true", help="enumerate the declared tables (no database)")
    ap.add_argument("--dry-run", action="store_true", help="list tables with live existence and row counts")
    ap.add_argument("--export", metavar="DIR")
    ap.add_argument("--verify", metavar="MANIFEST")
    ap.add_argument("--tables", nargs="*", help="restrict --export / --dry-run to these tables")
    ap.add_argument("--dsn", default=None, help=f"source DSN (default ${DSN_ENV})")
    ap.add_argument("--target-dsn", default=None, help="database to verify against (default: the source DSN)")
    ap.add_argument("--pg-container", default=DEFAULT_PG_CONTAINER, help="docker container holding pg_dump")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    if args.list:
        enum = enumerate_tables()
        if args.json:
            print(json.dumps(enum, indent=2))
        else:
            print(f"{len(enum['tables'])} FathomDesk table(s) declared; {len(enum['excluded_shared'])} shared excluded: "
                  f"{sorted(enum['excluded_shared'])}")
            for t in enum["tables"]:
                print(f"  {t}")
        return 0

    dsn = args.dsn or os.environ.get(DSN_ENV, "").strip()
    if not dsn:
        print(f"export_ft_data: no source DSN -- pass --dsn or set {DSN_ENV} (load the repo .env)", file=sys.stderr)
        return 2

    if args.verify:
        report = verify(Path(args.verify), args.target_dsn or dsn)
        print(json.dumps(report, indent=2) if args.json else
              f"Verify {report['manifest']} against {report['target']}: {report['matched']}/{report['tables']} matched, "
              f"{len(report['missing_on_target'])} missing, {len(report['row_count_mismatch'])} mismatched -> "
              f"{'OK' if report['ok'] else 'DIFF'}")
        return 0 if report["ok"] else 1

    if args.dry_run:
        enum = enumerate_tables()
        cands = [t for t in enum["tables"] if not args.tables or t in args.tables]
        conn = connect(dsn)
        try:
            counts = live_tables(conn, cands)
        finally:
            conn.close()
        present = {t: n for t, n in counts.items() if n is not None}
        absent = [t for t, n in counts.items() if n is None]
        if args.json:
            print(json.dumps({"source": redact_dsn(dsn), "present": present, "declared_but_absent": absent,
                              "excluded_shared": enum["excluded_shared"]}, indent=2))
        else:
            print(f"Dry run against {redact_dsn(dsn)}: {len(present)} table(s) present "
                  f"({sum(present.values())} rows), {len(absent)} declared but absent, "
                  f"{len(enum['excluded_shared'])} shared excluded")
            for t, n in present.items():
                print(f"  {t:<48} {n:>10}")
            for t in absent:
                print(f"  {t:<48}     absent")
        return 0

    if args.export:
        manifest = export(dsn, Path(args.export), args.tables, args.pg_container)
        print(json.dumps(manifest, indent=2) if args.json else
              f"Exported {len(manifest['tables'])} table(s), {manifest['total_rows']} rows, schema via "
              f"{manifest['schema_method']} -> {args.export}; {len(manifest['declared_but_absent'])} declared but absent")
        return 0

    ap.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())

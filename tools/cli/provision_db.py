#!/usr/bin/env python3
# CUI // SP-CTI
"""Create the database and the vector store when they do not exist yet.

`icdev setup` wrote a DSN and `icdev-init-db` created tables — but both assumed
something was already listening and that a database with that name existed.
Neither is true on a fresh machine, so the first honest failure a new user hit
was a connection refused, or a `CREATE EXTENSION vector` that could not work
because the running image did not ship pgvector at all.

Four things have to exist before RAG works, and they fail in different ways:

    1. a PostgreSQL SERVER      — connection refused
    2. a DATABASE + ROLE on it  — FATAL: database "icdev" does not exist
    3. the pgvector EXTENSION   — ERROR: type "vector" does not exist
    4. the SCHEMA               — relation "rag_chunks" does not exist

(3) is the one that bites hardest. The extension is only *installable* if the
server image ships the pgvector library; on stock `postgres:16` the
`CREATE EXTENSION` in migration 044 fails and every embedding write raises
afterwards. So this checks installability, not just whether it is enabled.

SQLite needs none of this: the file is created on first connect and the vector
store lives in `rag_chunks` with a BLOB column. It is offered as the zero-install
path precisely because it always works.

SAFETY

Provisioning is opt-in and never destructive. It will CREATE a database, a role
and an extension; it will never drop, alter or overwrite one that exists. Every
statement is `IF NOT EXISTS` or guarded by a catalog lookup, so re-running is a
no-op rather than an error. `--dry-run` reports the plan and touches nothing.

CLI::

    python -m tools.cli.provision_db --check              # report only
    python -m tools.cli.provision_db --provision          # create what is missing
    python -m tools.cli.provision_db --provision --docker # start a container first
    python -m tools.cli.provision_db --sqlite             # zero-install path
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

from tools.compat.subprocess_utils import runnable_module

DEFAULT_DSN = "postgresql://icdev:icdev@localhost:5432/icdev"

#: Postgres' own bootstrap database. Always present, so it is where a
#: connection is made to ask "does MY database exist yet?" — you cannot connect
#: to a database in order to find out whether it exists.
MAINTENANCE_DB = "postgres"


@dataclass
class DbStatus:
    """What is actually there, layer by layer.

    Each field answers one of the four failure modes above, so a caller can say
    precisely which step is missing rather than reporting "database error".
    """

    backend: str = "postgresql"
    server_reachable: bool = False
    database_exists: bool = False
    role_exists: bool = False
    vector_installable: bool = False
    vector_enabled: bool = False
    schema_present: bool = False
    detail: str = ""

    @property
    def ready(self) -> bool:
        if self.backend == "sqlite":
            return self.schema_present
        return (self.server_reachable and self.database_exists
                and self.vector_enabled and self.schema_present)

    def missing(self) -> list:
        """Ordered list of what still needs doing — the order it must happen in."""
        if self.backend == "sqlite":
            return [] if self.schema_present else ["schema"]
        out = []
        if not self.server_reachable:
            out.append("server")
        else:
            if not self.database_exists:
                out.append("database")
            if not self.vector_enabled:
                out.append("vector-extension")
            if not self.schema_present:
                out.append("schema")
        return out

    def to_dict(self) -> dict:
        return {
            "backend": self.backend,
            "server_reachable": self.server_reachable,
            "database_exists": self.database_exists,
            "role_exists": self.role_exists,
            "vector_installable": self.vector_installable,
            "vector_enabled": self.vector_enabled,
            "schema_present": self.schema_present,
            "ready": self.ready,
            "missing": self.missing(),
            "detail": self.detail,
        }


def parse_dsn(dsn: str) -> dict:
    """Split a DSN into the parts the provisioning statements need."""
    u = urlparse(dsn)
    return {
        "user": u.username or "icdev",
        "password": u.password or "icdev",
        "host": u.hostname or "localhost",
        "port": u.port or 5432,
        "database": (u.path or "/icdev").lstrip("/") or "icdev",
    }


def _maintenance_dsn(parts: dict) -> str:
    return (f"postgresql://{parts['user']}:{parts['password']}"
            f"@{parts['host']}:{parts['port']}/{MAINTENANCE_DB}")


def _port_open(host: str, port: int, timeout: float = 1.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


# --------------------------------------------------------------------------- #
# Inspection
# --------------------------------------------------------------------------- #

def check_sqlite(db_path: Path) -> DbStatus:
    """SQLite is 'installed' the moment the file has the schema in it."""
    st = DbStatus(backend="sqlite", server_reachable=True, database_exists=db_path.is_file(),
                  role_exists=True, vector_installable=True, vector_enabled=True)
    if not db_path.is_file():
        st.detail = f"{db_path} does not exist yet"
        return st
    try:
        import sqlite3

        con = sqlite3.connect(str(db_path))
        try:
            row = con.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='rag_chunks'"
            ).fetchone()
            st.schema_present = bool(row)
        finally:
            con.close()
    except Exception as exc:  # noqa: BLE001
        st.detail = f"could not read {db_path}: {exc}"
    return st


def check_postgres(dsn: str, timeout: float = 3.0) -> DbStatus:
    """Probe each layer independently so the report says WHICH one is missing."""
    parts = parse_dsn(dsn)
    st = DbStatus(backend="postgresql")
    st.server_reachable = _port_open(parts["host"], parts["port"], timeout=timeout)
    if not st.server_reachable:
        st.detail = f"nothing listening on {parts['host']}:{parts['port']}"
        return st

    try:
        import psycopg2
    except ImportError:
        st.detail = "psycopg2 not installed — cannot inspect PostgreSQL"
        return st

    # Connect to the maintenance DB: you cannot connect to a database to ask
    # whether that database exists.
    try:
        con = psycopg2.connect(_maintenance_dsn(parts), connect_timeout=int(timeout))
        con.autocommit = True
        with con.cursor() as cur:
            cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (parts["database"],))
            st.database_exists = cur.fetchone() is not None
            cur.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (parts["user"],))
            st.role_exists = cur.fetchone() is not None
            # Installable != enabled. On a stock postgres image the extension is
            # simply absent from pg_available_extensions, and no amount of
            # CREATE EXTENSION will help — the image is wrong.
            cur.execute("SELECT 1 FROM pg_available_extensions WHERE name = 'vector'")
            st.vector_installable = cur.fetchone() is not None
        con.close()
    except Exception as exc:  # noqa: BLE001
        st.detail = f"maintenance connection failed: {str(exc)[:120]}"
        return st

    if not st.database_exists:
        return st

    try:
        con = psycopg2.connect(dsn, connect_timeout=int(timeout))
        con.autocommit = True
        with con.cursor() as cur:
            cur.execute("SELECT 1 FROM pg_extension WHERE extname = 'vector'")
            st.vector_enabled = cur.fetchone() is not None
            cur.execute("SELECT to_regclass('public.rag_chunks')")
            st.schema_present = cur.fetchone()[0] is not None
        con.close()
    except Exception as exc:  # noqa: BLE001
        st.detail = f"connection to {parts['database']} failed: {str(exc)[:120]}"
    return st


# --------------------------------------------------------------------------- #
# Provisioning
# --------------------------------------------------------------------------- #

@dataclass
class ProvisionResult:
    actions: list = field(default_factory=list)
    ok: bool = True
    error: str = ""

    def add(self, what: str, done: bool, detail: str = "") -> None:
        self.actions.append({"action": what, "done": done, "detail": detail})

    def to_dict(self) -> dict:
        return {"ok": self.ok, "error": self.error, "actions": self.actions}


def create_database(dsn: str, *, dry_run: bool = False) -> ProvisionResult:
    """Create the role and database if absent. Never drops or alters.

    CREATE DATABASE cannot run inside a transaction block, hence autocommit.
    Both statements are guarded by a catalog lookup rather than relying on
    IF NOT EXISTS, which CREATE DATABASE does not support.
    """
    res = ProvisionResult()
    parts = parse_dsn(dsn)
    if dry_run:
        res.add("create-role", False, f"would ensure role {parts['user']}")
        res.add("create-database", False, f"would ensure database {parts['database']}")
        return res

    try:
        import psycopg2
        from psycopg2 import sql
    except ImportError:
        res.ok = False
        res.error = "psycopg2 not installed — cannot provision PostgreSQL"
        return res

    try:
        con = psycopg2.connect(_maintenance_dsn(parts), connect_timeout=5)
        con.autocommit = True
        with con.cursor() as cur:
            cur.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (parts["user"],))
            if cur.fetchone():
                res.add("create-role", False, f"role {parts['user']} already exists")
            else:
                cur.execute(
                    sql.SQL("CREATE ROLE {} LOGIN PASSWORD %s").format(
                        sql.Identifier(parts["user"])), (parts["password"],))
                res.add("create-role", True, f"created role {parts['user']}")

            cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (parts["database"],))
            if cur.fetchone():
                res.add("create-database", False,
                        f"database {parts['database']} already exists")
            else:
                cur.execute(sql.SQL("CREATE DATABASE {} OWNER {}").format(
                    sql.Identifier(parts["database"]), sql.Identifier(parts["user"])))
                res.add("create-database", True, f"created database {parts['database']}")
        con.close()
    except Exception as exc:  # noqa: BLE001
        res.ok = False
        res.error = str(exc)[:200]
    return res


def enable_vector_extension(dsn: str, *, dry_run: bool = False) -> ProvisionResult:
    """Enable pgvector — the vector store itself.

    Fails with a specific, actionable message when the extension is not
    *available* on the server, because that is an IMAGE problem: stock
    `postgres:16` cannot host embeddings no matter how the statement is written,
    and the generic "extension vector is not available" gives no hint of that.
    """
    res = ProvisionResult()
    if dry_run:
        res.add("create-extension", False, "would CREATE EXTENSION IF NOT EXISTS vector")
        return res
    try:
        import psycopg2
    except ImportError:
        res.ok = False
        res.error = "psycopg2 not installed"
        return res
    try:
        con = psycopg2.connect(dsn, connect_timeout=5)
        con.autocommit = True
        with con.cursor() as cur:
            cur.execute("SELECT 1 FROM pg_available_extensions WHERE name = 'vector'")
            if not cur.fetchone():
                res.ok = False
                res.error = (
                    "pgvector is not available on this server. The image does not "
                    "ship it — use pgvector/pgvector:pg16 (or install postgresql-16-pgvector). "
                    "Stock postgres cannot store embeddings.")
                con.close()
                return res
            cur.execute("CREATE EXTENSION IF NOT EXISTS vector")
            res.add("create-extension", True, "pgvector enabled")
        con.close()
    except Exception as exc:  # noqa: BLE001
        res.ok = False
        res.error = str(exc)[:200]
    return res


# --------------------------------------------------------------------------- #
# Port conflicts
# --------------------------------------------------------------------------- #

#: Ports tried when the preferred one is taken. Deliberately adjacent to 5432
#: so the choice is recognisable to anyone reading `docker ps` later.
_PORT_FALLBACKS = (5433, 5434, 5435, 5436, 5440)


def inspect_port(host: str, port: int, timeout: float = 1.5) -> dict:
    """Who owns this port — nobody, a PostgreSQL, or something else?

    The distinction decides the whole strategy and the three cases need
    completely different handling:

      free              -> start a container here
      a PostgreSQL      -> do NOT start a container; provision INTO the server
                           that is already running (a second one would be a
                           silent second source of truth)
      something else    -> the port is unusable; publish on another one

    Without this, `docker compose up` fails to bind and reports a generic
    "port is already allocated", which reads as a Docker problem rather than
    "you already have PostgreSQL running".
    """
    if not _port_open(host, port, timeout=timeout):
        return {"occupied": False, "is_postgres": False, "detail": "free"}

    # Speaks the PostgreSQL wire protocol? psycopg2 gets far enough to tell us:
    # an auth failure or an unknown-database error both PROVE it is PostgreSQL.
    try:
        import psycopg2
    except ImportError:
        return {"occupied": True, "is_postgres": False,
                "detail": "in use (psycopg2 unavailable — cannot identify)"}

    try:
        psycopg2.connect(host=host, port=port, dbname=MAINTENANCE_DB,
                         user="icdev", password="icdev", connect_timeout=int(timeout)).close()
        return {"occupied": True, "is_postgres": True, "detail": "PostgreSQL (connected)"}
    except Exception as exc:  # noqa: BLE001
        msg = str(exc).lower()
        pg_signals = ("password authentication", "role ", "database ",
                      "pg_hba.conf", "authentication failed")
        if any(s in msg for s in pg_signals):
            return {"occupied": True, "is_postgres": True,
                    "detail": "PostgreSQL (rejected our credentials)"}
        return {"occupied": True, "is_postgres": False,
                "detail": f"in use by a non-PostgreSQL service ({str(exc)[:60]})"}


def find_free_port(host: str = "127.0.0.1", preferred: int = 5432) -> int | None:
    """First usable port: the preferred one, else a known fallback."""
    for candidate in (preferred, *_PORT_FALLBACKS):
        if not _port_open(host, candidate, timeout=0.6):
            return candidate
    return None


def with_port(dsn: str, port: int) -> str:
    """Rewrite a DSN's port, leaving every other component untouched.

    The published port and the DSN must move together — changing one without
    the other produces a container nobody can reach.
    """
    parts = parse_dsn(dsn)
    return (f"postgresql://{parts['user']}:{parts['password']}"
            f"@{parts['host']}:{port}/{parts['database']}")


def start_postgres_container(compose_file: Path, *, service: str = "postgres",
                             wait_seconds: int = 60,
                             dry_run: bool = False) -> ProvisionResult:
    """Bring up the DB service from the generated compose file, then wait.

    Waiting matters: `docker compose up -d` returns as soon as the container is
    created, not when PostgreSQL accepts connections. Provisioning immediately
    after would fail on a server that is seconds from being ready.
    """
    res = ProvisionResult()
    if not shutil.which("docker"):
        res.ok = False
        res.error = "docker not found on PATH"
        return res
    if not compose_file.is_file():
        res.ok = False
        res.error = (f"{compose_file.name} not found — run `icdev setup` first "
                     "to generate one for this OS")
        return res
    if dry_run:
        res.add("docker-up", False, f"would run: docker compose up -d {service}")
        return res

    proc = subprocess.run(
        ["docker", "compose", "-f", str(compose_file), "up", "-d", service],
        capture_output=True, text=True, timeout=300)
    if proc.returncode != 0:
        res.ok = False
        res.error = (proc.stderr or proc.stdout)[-300:]
        return res
    res.add("docker-up", True, f"started service {service}")

    deadline = time.time() + wait_seconds
    while time.time() < deadline:
        if _port_open("127.0.0.1", 5432, timeout=1.0):
            res.add("wait-ready", True, "server accepting connections")
            return res
        time.sleep(1.5)
    res.ok = False
    res.error = f"container started but nothing on :5432 after {wait_seconds}s"
    return res


def init_schema(*, dry_run: bool = False, db_path: Path | None = None) -> ProvisionResult:
    """Run the platform schema initialiser (the same one `icdev-init-db` runs).

    The module name is resolved through ``runnable_module`` rather than
    hardcoded: a child process cannot see the ``tools`` → ``icdev.tools`` alias
    that ``icdev/__init__.py`` installs, so ``-m tools.db.init_icdev_db`` died
    with ``ModuleNotFoundError`` on every pip-installed machine while working
    on every source checkout.

    ``db_path`` is passed explicitly for SQLite. The child does not load the
    project's ``.env``, so without it the initialiser falls back to its own
    default and can create the schema somewhere nothing else is looking.
    """
    res = ProvisionResult()
    target = runnable_module("tools.db.init_icdev_db")
    if dry_run:
        res.add("init-schema", False, f"would run python -m {target}")
        return res
    cmd = [sys.executable, "-m", target]
    if db_path is not None:
        cmd += ["--db-path", str(db_path)]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
        if proc.returncode != 0:
            res.ok = False
            res.error = (proc.stderr or proc.stdout)[-300:]
        else:
            res.add("init-schema", True, "tables created")
    except Exception as exc:  # noqa: BLE001
        res.ok = False
        res.error = str(exc)[:200]
    return res


def provision_sqlite(db_path: Path, *, dry_run: bool = False) -> dict:
    """Bring a SQLite database up to `ready`, creating the schema if absent.

    The SQLite branch used to call :func:`check_sqlite` and stop, so
    ``icdev setup --provision-db`` printed ``database ready : False (missing:
    schema)`` and created nothing — a diagnosis presented as an action. SQLite
    is the default zero-install backend, so that was the path most first-time
    installs took.
    """
    status = check_sqlite(db_path)
    steps: list = []
    if not status.schema_present:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        r = init_schema(dry_run=dry_run, db_path=db_path)
        steps.append({"step": "schema", **r.to_dict()})
        if not r.ok:
            return {"ok": False, "steps": steps, "status": status.to_dict(),
                    "hint": "schema init failed — run `icdev-init-db` and read its output"}
    final = status if dry_run else check_sqlite(db_path)
    return {"ok": final.ready or dry_run, "steps": steps, "status": final.to_dict()}


def install_guidance(system: str) -> list:
    """Per-OS commands for installing a pgvector-capable PostgreSQL natively.

    Deliberately printed rather than executed. Installing system packages needs
    elevation and changes the machine outside the project directory — that is
    the user's call, not a setup wizard's.
    """
    if system == "Windows":
        return [
            "winget install PostgreSQL.PostgreSQL.16",
            "# then install pgvector: https://github.com/pgvector/pgvector#windows",
            "# simpler: use Docker — `icdev setup` already generated a compose file",
        ]
    if system == "Darwin":
        return [
            "brew install postgresql@16 pgvector",
            "brew services start postgresql@16",
        ]
    return [
        "sudo apt-get install -y postgresql-16 postgresql-16-pgvector",
        "sudo systemctl enable --now postgresql",
        "# RHEL/Fedora: sudo dnf install postgresql-server pgvector",
    ]


def _rewrite_compose_port(compose_file: Path, port: int, *, dry_run: bool = False) -> bool:
    """Repoint the published PostgreSQL port in an existing compose file.

    Only the HOST side of `"host:container"` is rewritten — postgres inside the
    container always listens on 5432 regardless of what the host publishes.
    """
    if dry_run or not compose_file.is_file():
        return False
    import re as _re

    text = compose_file.read_text(encoding="utf-8")
    new = _re.sub(r'^(\s*-\s*")\d+(:5432")', rf'\g<1>{port}\g<2>', text, count=1, flags=_re.M)
    if new != text:
        compose_file.write_text(new, encoding="utf-8")
        return True
    return False


def provision(dsn: str, *, use_docker: bool = False, compose_file: Path | None = None,
              dry_run: bool = False) -> dict:
    """Bring the database and vector store up to `ready`, in dependency order."""
    steps: list = []
    status = check_postgres(dsn)

    if not status.server_reachable and use_docker:
        # Who owns the port decides everything. `check_postgres` said the
        # SERVER is not reachable, but that only means nothing answered on the
        # DSN's port — it does not mean the port is free.
        parts = parse_dsn(dsn)
        owner = inspect_port(parts["host"], parts["port"])

        if owner["is_postgres"]:
            # A PostgreSQL is already here. Starting a container would bind-fail,
            # and forcing it onto another port would leave two servers and a
            # silent second source of truth. Provision into the existing one.
            steps.append({"step": "server", "ok": True, "actions": [
                {"action": "reuse-existing", "done": True,
                 "detail": f"{owner['detail']} on {parts['host']}:{parts['port']} "
                           "— provisioning into it instead of starting a container"}]})
        else:
            if owner["occupied"]:
                free = find_free_port(parts["host"], parts["port"])
                if free is None:
                    return {
                        "ok": False, "steps": steps, "status": status.to_dict(),
                        "hint": f"port {parts['port']} is {owner['detail']} and no "
                                f"fallback port is free. Free one, or use --sqlite.",
                    }
                steps.append({"step": "port-conflict", "ok": True, "actions": [
                    {"action": "relocate", "done": True,
                     "detail": f"port {parts['port']} is {owner['detail']}; "
                               f"publishing PostgreSQL on {free} instead"}]})
                dsn = with_port(dsn, free)
                # The compose file must publish the SAME port the DSN now names,
                # or the container comes up somewhere nothing is looking.
                _rewrite_compose_port(compose_file or Path("docker-compose.yml"),
                                      free, dry_run=dry_run)

            r = start_postgres_container(
                compose_file or Path("docker-compose.yml"), dry_run=dry_run)
            steps.append({"step": "server", **r.to_dict()})
            if not r.ok and not dry_run:
                return {"ok": False, "steps": steps, "status": status.to_dict(),
                        "dsn": dsn}
        if not dry_run:
            status = check_postgres(dsn)

    if not status.server_reachable and not dry_run:
        return {
            "ok": False,
            "steps": steps,
            "status": status.to_dict(),
            "hint": "no PostgreSQL server. Re-run with --docker, install one "
                    "natively, or use --sqlite for a zero-install setup.",
        }

    if not status.database_exists:
        r = create_database(dsn, dry_run=dry_run)
        steps.append({"step": "database", **r.to_dict()})
        if not r.ok:
            return {"ok": False, "steps": steps, "status": status.to_dict()}

    if not status.vector_enabled:
        r = enable_vector_extension(dsn, dry_run=dry_run)
        steps.append({"step": "vector-store", **r.to_dict()})
        if not r.ok:
            return {"ok": False, "steps": steps, "status": status.to_dict()}

    if not status.schema_present:
        r = init_schema(dry_run=dry_run)
        steps.append({"step": "schema", **r.to_dict()})
        if not r.ok:
            return {"ok": False, "steps": steps, "status": status.to_dict()}

    final = status if dry_run else check_postgres(dsn)
    return {"ok": True, "steps": steps, "status": final.to_dict(), "dsn": dsn}


def main(argv: list | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="icdev setup --provision-db",
        description="Create the database and vector store when they do not exist.")
    ap.add_argument("--dsn", default=os.environ.get("ICDEV_DATABASE_URL", DEFAULT_DSN))
    ap.add_argument("--sqlite", action="store_true", help="Zero-install SQLite path.")
    ap.add_argument("--db-path", default="data/icdev.db")
    ap.add_argument("--check", action="store_true", help="Report only; change nothing.")
    ap.add_argument("--provision", action="store_true", help="Create what is missing.")
    ap.add_argument("--docker", action="store_true",
                    help="Start PostgreSQL from the generated compose file first.")
    ap.add_argument("--compose-file", default="docker-compose.yml")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    if args.sqlite:
        st = check_sqlite(Path(args.db_path))
        if args.provision and not st.schema_present:
            r = init_schema(dry_run=args.dry_run)
            payload = {"ok": r.ok, "steps": [{"step": "schema", **r.to_dict()}],
                       "status": check_sqlite(Path(args.db_path)).to_dict()}
        else:
            payload = {"ok": st.ready, "steps": [], "status": st.to_dict()}
    elif args.provision:
        payload = provision(args.dsn, use_docker=args.docker,
                            compose_file=Path(args.compose_file), dry_run=args.dry_run)
    else:
        st = check_postgres(args.dsn)
        payload = {"ok": st.ready, "steps": [], "status": st.to_dict()}

    if args.json:
        print(json.dumps(payload, indent=2))
        return 0 if payload["ok"] else 1

    st = payload["status"]
    print(f"backend        : {st['backend']}")
    print(f"server reachable: {st['server_reachable']}")
    print(f"database exists : {st['database_exists']}")
    print(f"pgvector avail  : {st['vector_installable']}   enabled: {st['vector_enabled']}")
    print(f"schema present  : {st['schema_present']}")
    print(f"READY           : {st['ready']}")
    if st["missing"]:
        print(f"missing         : {', '.join(st['missing'])}")
    for s in payload.get("steps", []):
        for a in s.get("actions", []):
            print(f"  [{'x' if a['done'] else ' '}] {a['action']}: {a['detail']}")
        if s.get("error"):
            print(f"  ! {s['step']}: {s['error']}")
    if payload.get("hint"):
        print(f"\n{payload['hint']}")
        import platform as _p

        print("\nNative install:")
        for line in install_guidance(_p.system()):
            print(f"  {line}")
    return 0 if payload["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

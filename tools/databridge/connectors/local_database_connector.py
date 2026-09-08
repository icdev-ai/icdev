#!/usr/bin/env python3
# CUI // SP-CTI
"""Local / operator-owned database DataBridge connector — read-only, allowlisted.

The other three connectors added with this one reach a vendor's API. This one
reaches a database the operator runs: the SharePoint of organisations that never
bought SharePoint. A policy register in Postgres, an asset inventory in SQLite,
a table of approved terminology -- all perfectly good evidence, and until now
unreachable from DataBridge because every one of the 37 existing connectors
speaks REST or SOAP.

WHAT THIS DELIBERATELY DOES NOT OFFER: arbitrary SQL. There is no `query` table
and no `sql` filter, and adding one is not a small change to this file -- it is
a different security decision. The reasons, in order:

  1. A connector is reachable BY AGENTS through the feeds surface. "The agent
     may read the policy_register table" is a grant a human can review; "the
     agent may run SQL" is not a grant, it is a shell.
  2. Read-only is enforced three ways, not one: the connection is opened
     read-only where the driver supports it, `capabilities.supports_write` is
     False and `write()` refuses, and no statement is ever assembled from
     caller-supplied text.
  3. A SELECT with a caller's WHERE clause is the same hole one indirection
     further away.

So: the operator DECLARES the tables in the connection record, and this
connector emits `SELECT <cols> FROM <table> [WHERE col = %s ...] LIMIT n`.
Filter VALUES are always bound parameters. Filter and column NAMES are matched
against the declared schema of that table -- a name that is not in it is refused
BY NAME rather than quietly dropped, because a filter that silently vanishes
returns more rows than the caller asked for and looks like a successful read.

Table and column identifiers cannot be bound as parameters in any driver, so
they are interpolated -- but only after passing BOTH a strict identifier pattern
and membership in the operator's own allowlist. That is a real control, which is
why the `# nosec` beside the statement names it; a suppression asserting a
control that does not exist is worse than no suppression at all.

NO ALLOWLIST MEANS NO TABLES. An empty or absent `tables:` is not "expose
everything" -- `list_tables()` returns [] and every read refuses. Fail closed.

Drivers: `postgresql` (psycopg2) and `sqlite` (stdlib). A driver named but not
installed is refused BY NAME at connect; it never falls back to another engine.

Config (a connection record in args/databridge_connections.yaml):
    driver           postgresql | sqlite
    auth_secret_ref  env:POLICY_DB_DSN     the DSN, as a REFERENCE (postgresql)
    database_path    ./data/policy.db      the file (sqlite)
    tables:                                the allowlist; no entry, no access
      - name: policy_register
        columns: [policy_id, title, body, effective_date, owner]
        order_by: effective_date

Usage:
    python tools/databridge/connectors/local_database_connector.py \
        --sqlite ./data/policy.db --table policy_register --health
"""

from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.databridge.connector import (  # noqa: E402
    ConnectorCapabilities,
    ConnectorRequest,
    ConnectorResponse,
    ConnectorType,
    DataConnector,
    SchemaDefinition,
    SchemaField,
)
from tools.databridge.registry import register_connector  # noqa: E402
from tools.logging.icdev_logger import get_logger  # noqa: E402

logger = get_logger("databridge.local_database")

# An identifier this connector will interpolate. Deliberately narrower than
# either engine allows: letters, digits and underscore, optionally one schema
# qualifier. A quoted identifier with a space or a dot in it is legal SQL and
# is refused here anyway -- the set of tables worth exposing to an agent does
# not include the ones that need quoting to name.
_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,62}$")
_QUALIFIED = re.compile(
    r"^[A-Za-z_][A-Za-z0-9_]{0,62}(\.[A-Za-z_][A-Za-z0-9_]{0,62})?$"
)

SUPPORTED_DRIVERS = ("postgresql", "sqlite")
DEFAULT_LIMIT = 100
MAX_LIMIT = 1000


class TableNotDeclared(ValueError):
    """The caller named a table the operator did not declare."""


def valid_identifier(name: str, *, qualified: bool = False) -> bool:
    """Is *name* safe to interpolate into a statement?"""
    pattern = _QUALIFIED if qualified else _IDENT
    return bool(name) and bool(pattern.match(str(name)))


@register_connector
class LocalDatabaseConnector(DataConnector):
    """Read-only, allowlisted access to an operator-owned SQL database."""

    _connector_name = "local_database"

    def __init__(self) -> None:
        self._config: Dict[str, Any] = {}
        self._driver: str = ""
        self._conn: Any = None
        self._connected: bool = False
        self._tables: Dict[str, Dict[str, Any]] = {}

    # -- ABC properties -------------------------------------------------------

    @property
    def connector_name(self) -> str:
        return self._connector_name

    @property
    def connector_type(self) -> ConnectorType:
        return ConnectorType.DATABASE

    @property
    def capabilities(self) -> ConnectorCapabilities:
        return ConnectorCapabilities(
            supports_read=True,
            supports_write=False,      # enforced in write(), not just declared
            supports_schema_inference=True,
            supports_incremental=False,
            supports_transactions=False,
            max_batch_size=MAX_LIMIT,
            supported_formats=["json"],
        )

    # -- the allowlist --------------------------------------------------------

    @staticmethod
    def parse_tables(declared: Any) -> Dict[str, Dict[str, Any]]:
        """Turn the connection record's `tables:` into the allowlist.

        Accepts either a list of names or a list of
        `{name, columns?, order_by?}` mappings. A name that fails the identifier
        pattern is DROPPED WITH A LOG LINE rather than silently ignored: the
        operator wrote it intending access, and access they did not get is
        something they need to be able to find out about.
        """
        allowed: Dict[str, Dict[str, Any]] = {}
        for entry in declared or []:
            if isinstance(entry, str):
                name, columns, order_by = entry, None, None
            elif isinstance(entry, dict):
                name = str(entry.get("name", ""))
                columns = entry.get("columns")
                order_by = entry.get("order_by")
            else:
                logger.warning("local_database: ignoring a malformed table "
                               "declaration: %r", entry)
                continue

            if not valid_identifier(name, qualified=True):
                logger.warning("local_database: table %r is not a plain "
                               "identifier and is NOT exposed", name)
                continue

            cols: Optional[List[str]] = None
            if columns:
                cols = [c for c in (str(x) for x in columns)
                        if valid_identifier(c)]
                bad = [str(x) for x in columns if not valid_identifier(str(x))]
                if bad:
                    logger.warning("local_database: table %s — columns %s are "
                                   "not plain identifiers and are NOT exposed",
                                   name, bad)
                if not cols:
                    logger.warning("local_database: table %s declared columns "
                                   "but none survived validation; it is NOT "
                                   "exposed", name)
                    continue

            if order_by is not None and not valid_identifier(str(order_by)):
                logger.warning("local_database: table %s — order_by %r is not a "
                               "plain identifier and is ignored", name, order_by)
                order_by = None

            allowed[name] = {"columns": cols, "order_by": order_by}
        return allowed

    # -- connection lifecycle -------------------------------------------------

    def connect(self, config: Dict[str, Any]) -> bool:
        self._config = dict(config)
        self._driver = str(config.get("driver", "")).lower().strip()
        if self._driver not in SUPPORTED_DRIVERS:
            logger.error("local_database: driver %r is not supported "
                         "(supported: %s)", self._driver,
                         ", ".join(SUPPORTED_DRIVERS))
            return False

        self._tables = self.parse_tables(config.get("tables"))
        if not self._tables:
            # Not a warning. A connector with no allowlist can answer nothing,
            # and reporting it connected would turn every later read into an
            # empty result rather than a configuration error.
            logger.error("local_database: no tables are declared; there is "
                         "nothing this connection may read")
            return False

        try:
            self._conn = self._open()
        except Exception as exc:  # noqa: BLE001
            logger.error("local_database: could not open the database: %s", exc)
            self._conn = None
            return False

        self._connected = True
        return True

    def _open(self) -> Any:
        if self._driver == "sqlite":
            import sqlite3
            raw = self._config.get("database_path") or self._config.get("dsn")
            if not raw:
                raise ValueError("sqlite needs database_path")
            path = Path(str(raw)).expanduser()
            if not path.is_absolute():
                path = (BASE_DIR / path).resolve()
            if not path.exists():
                raise FileNotFoundError(f"no database file at {path}")
            # URI mode: the ONLY way sqlite3 opens a file read-only. A writable
            # handle would make "read-only" a promise this file merely makes.
            conn = sqlite3.connect(
                f"file:{path.as_posix()}?mode=ro", uri=True,
                timeout=float(self._config.get("timeout", 30)),
            )
            conn.row_factory = sqlite3.Row
            return conn

        # postgresql
        try:
            import psycopg2
            import psycopg2.extras
        except ImportError as exc:
            raise RuntimeError(
                "local_database: driver 'postgresql' needs psycopg2, which is "
                "not installed. It is NOT substituted with another engine."
            ) from exc

        dsn = self._resolve_dsn()
        if not dsn:
            raise ValueError(
                "postgresql needs a DSN through auth_secret_ref "
                "(env:/vault:/aws:/file:)"
            )
        conn = psycopg2.connect(
            dsn, connect_timeout=int(self._config.get("timeout", 30)),
            cursor_factory=psycopg2.extras.RealDictCursor,
        )
        conn.set_session(readonly=True, autocommit=True)
        return conn

    def _resolve_dsn(self) -> str:
        ref = self._config.get("auth_secret_ref") or self._config.get("dsn") or ""
        if not ref:
            return ""
        if str(ref).split(":", 1)[0] not in ("env", "vault", "aws", "file"):
            # A literal DSN carries the password. The seeder refuses one in the
            # YAML; refusing it here too closes the programmatic path.
            logger.error("local_database: the DSN must be a reference "
                         "(env:/vault:/aws:/file:), not a literal")
            return ""
        try:
            from tools.databridge.connection_manager import resolve_secret
            return resolve_secret(str(ref))
        except Exception as exc:  # noqa: BLE001
            logger.error("local_database: could not resolve the DSN "
                         "reference: %s", exc)
            return ""

    def disconnect(self) -> None:
        if self._conn is not None:
            try:
                self._conn.close()
            except Exception:  # noqa: BLE001 — closing a dead handle is not news
                pass
        self._conn = None
        self._connected = False

    def health_check(self) -> Dict[str, Any]:
        if not self._connected or self._conn is None:
            return {"status": "unhealthy", "connector": self._connector_name,
                    "error": "not connected"}
        try:
            cur = self._conn.cursor()
            cur.execute("SELECT 1")
            cur.fetchone()
            return {
                "status": "healthy",
                "connector": self._connector_name,
                "driver": self._driver,
                "tables_declared": sorted(self._tables),
            }
        except Exception as exc:  # noqa: BLE001
            return {"status": "unhealthy", "connector": self._connector_name,
                    "error": str(exc)}

    # -- statement construction ----------------------------------------------

    def _placeholder(self) -> str:
        return "?" if self._driver == "sqlite" else "%s"

    def build_select(
        self, table: str, filters: Dict[str, Any], limit: int
    ) -> Tuple[str, List[Any]]:
        """Assemble the one statement shape this connector emits.

        Every identifier here has been through `valid_identifier` AND the
        operator's allowlist; every VALUE is a bound parameter. Raises
        `TableNotDeclared` or `ValueError` naming the offender otherwise.
        """
        spec = self._tables.get(table)
        if spec is None:
            raise TableNotDeclared(
                f"table {table!r} is not declared on this connection. "
                f"Declared: {sorted(self._tables)}"
            )
        if not valid_identifier(table, qualified=True):   # belt and braces
            raise ValueError(f"table {table!r} is not a plain identifier")

        columns = spec.get("columns")
        if columns:
            for col in columns:
                if not valid_identifier(col):
                    raise ValueError(f"column {col!r} is not a plain identifier")
            select_list = ", ".join(columns)
            known = set(columns)
        else:
            select_list = "*"
            known = set()

        where_parts: List[str] = []
        params: List[Any] = []
        for key, value in (filters or {}).items():
            name = str(key)
            if not valid_identifier(name):
                raise ValueError(f"filter {name!r} is not a plain identifier")
            if known and name not in known:
                # Refused BY NAME. Dropping it would widen the result set while
                # the response still read "ok".
                raise ValueError(
                    f"filter column {name!r} is not exposed on {table}. "
                    f"Exposed: {sorted(known)}"
                )
            where_parts.append(f"{name} = {self._placeholder()}")
            params.append(value)

        bounded = max(1, min(int(limit or DEFAULT_LIMIT), MAX_LIMIT))
        sql = f"SELECT {select_list} FROM {table}"  # nosec B608 -- identifiers are
        # validated against _IDENT/_QUALIFIED **and** the operator's declared
        # allowlist above; values are bound parameters, never interpolated.
        if where_parts:
            sql += " WHERE " + " AND ".join(where_parts)
        order_by = spec.get("order_by")
        if order_by and valid_identifier(str(order_by)):
            sql += f" ORDER BY {order_by} DESC"
        sql += f" LIMIT {bounded}"
        return sql, params

    # -- data operations ------------------------------------------------------

    def read(self, request: ConnectorRequest) -> ConnectorResponse:
        t0 = time.time()
        table = request.table_name or request.query
        if not self._connected or self._conn is None:
            return ConnectorResponse(status="error", errors=["not connected"])

        try:
            sql, params = self.build_select(
                str(table), dict(request.filters or {}),
                int(request.limit or DEFAULT_LIMIT),
            )
        except (TableNotDeclared, ValueError) as exc:
            return ConnectorResponse(
                status="error", errors=[str(exc)],
                duration_ms=int((time.time() - t0) * 1000),
            )

        try:
            cur = self._conn.cursor()
            cur.execute(sql, params)
            rows = [self._row_to_dict(r, cur) for r in cur.fetchall()]
            return ConnectorResponse(
                status="ok", data=rows, row_count=len(rows),
                duration_ms=int((time.time() - t0) * 1000),
                metadata={"table": table, "driver": self._driver,
                          "statement": sql},
            )
        except Exception as exc:  # noqa: BLE001
            return ConnectorResponse(
                status="error", errors=[str(exc)],
                duration_ms=int((time.time() - t0) * 1000),
            )

    @staticmethod
    def _row_to_dict(row: Any, cursor: Any) -> Dict[str, Any]:
        if isinstance(row, dict):
            return dict(row)
        try:                                   # sqlite3.Row
            return {k: row[k] for k in row.keys()}
        except Exception:  # noqa: BLE001
            names = [d[0] for d in (cursor.description or [])]
            return dict(zip(names, row))

    def write(self, request: ConnectorRequest, data: Any) -> ConnectorResponse:
        """Refuses. This connector is read-only by construction.

        Not a NotImplementedError: a caller asking to write is asking for
        something this connection is deliberately unable to do, and the answer
        it needs is why, not a traceback.
        """
        return ConnectorResponse(
            status="error",
            errors=[
                "local_database is READ-ONLY. The connection is opened "
                "read-only and no write statement is constructed anywhere in "
                "this connector. Writing to an operator's own database from an "
                "agent-reachable surface is a separate decision."
            ],
        )

    def infer_schema(self, table_name: str) -> SchemaDefinition:
        """Schema from one sampled row, within the declared columns."""
        spec = self._tables.get(table_name)
        if spec is None:
            return SchemaDefinition(
                metadata={"source": self._connector_name, "table": table_name,
                          "status": "not_declared"},
            )
        resp = self.read(ConnectorRequest(table_name=table_name, limit=1))
        if resp.status != "ok" or not resp.data:
            return SchemaDefinition(
                fields=[SchemaField(c, "utf8") for c in (spec.get("columns") or [])],
                metadata={"source": self._connector_name, "table": table_name,
                          "status": "declared_but_unsampled"},
            )
        sample = resp.data[0]
        fields = []
        for key, val in sample.items():
            dt = "utf8"
            if isinstance(val, bool):
                dt = "bool"
            elif isinstance(val, int):
                dt = "int64"
            elif isinstance(val, float):
                dt = "float64"
            elif isinstance(val, (list, dict)):
                dt = "json"
            fields.append(SchemaField(name=str(key), data_type=dt))
        return SchemaDefinition(
            fields=fields,
            metadata={"source": self._connector_name, "table": table_name,
                      "status": "sampled"},
        )

    def list_tables(self) -> List[str]:
        """The DECLARED tables, never the database's own catalogue.

        Enumerating what exists would tell an agent about tables it may not
        read, which is the first half of reading them.
        """
        return sorted(self._tables)


# -- CLI ---------------------------------------------------------------------

def _main() -> int:
    import argparse

    parser = argparse.ArgumentParser(
        description="Local database DataBridge connector (read-only)"
    )
    parser.add_argument("--sqlite", metavar="PATH", default="")
    parser.add_argument("--dsn-ref", metavar="env:VAR", default="",
                        help="a DSN REFERENCE for postgresql, never a literal")
    parser.add_argument("--table", action="append", default=[],
                        help="declare an allowlisted table (repeatable)")
    parser.add_argument("--health", action="store_true")
    parser.add_argument("--read", metavar="TABLE")
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--json", action="store_true")
    ns = parser.parse_args()

    if not ns.table:
        print("no --table declared: this connector exposes nothing by default")
        return 2

    config: Dict[str, Any] = {"tables": ns.table}
    if ns.sqlite:
        config.update({"driver": "sqlite", "database_path": ns.sqlite})
    elif ns.dsn_ref:
        config.update({"driver": "postgresql", "auth_secret_ref": ns.dsn_ref})
    else:
        print("give either --sqlite PATH or --dsn-ref env:VAR")
        return 2

    connector = LocalDatabaseConnector()
    if not connector.connect(config):
        print("not connected (see the log for the refusal)")
        return 2

    if ns.health:
        print(json.dumps(connector.health_check(), indent=2))
        return 0

    if not ns.read:
        parser.print_help()
        return 1

    resp = connector.read(ConnectorRequest(table_name=ns.read, limit=ns.limit))
    if ns.json:
        print(json.dumps(
            {"status": resp.status, "row_count": resp.row_count,
             "errors": resp.errors, "data": resp.data},
            indent=2, default=str,
        ))
    else:
        print(f"{resp.status}  {resp.row_count} row(s)  {resp.errors or ''}")
        for row in resp.data or []:
            print(row)
    connector.disconnect()
    return 0 if resp.status == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(_main())

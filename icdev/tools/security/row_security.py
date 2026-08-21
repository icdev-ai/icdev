#!/usr/bin/env python3
# CUI // SP-CTI
from __future__ import annotations

import sys
from pathlib import Path

# kax-conflict-05: run by path, sys.path[0] is this file's own directory — never
# the import root. Bootstrap it before the first first-party import below.
# parents[N] is whatever holds this file's `tools` package: the repo root in
# tools/, and <repo>/icdev in the icdev/ mirror (which is what a wheel ships).
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tools.logging.icdev_logger import get_logger
"""Row-Level Security (RLS) for ICDEV™.

Dual-backend:
- PostgreSQL: emit ``CREATE POLICY`` DDL and set session variables.
- SQLite: inject ``AND tenant_id = ?`` / ``AND classification IN (...)``
  predicates into SELECT/UPDATE/DELETE queries via regex (no ``sqlparse``
  dependency).

Integration:
    tools.db.storage.StorageConnection.set_security_context(ctx)
    tools.db.storage.StorageCursor.set_security_context(ctx)

Public API:
    inject_row_predicate(sql, tenant_id, classification)
        -> (sql, extra_params, n_params_before)
    generate_rls_policy(table, predicate_expr, roles) -> DDL string
    apply_tenant_rls(conn, table) -> None
"""

import json
import re
from typing import Any, Optional, Set, Tuple

logger = get_logger("security.rls")

# ---------------------------------------------------------------------------
# SQLite predicate injection (regex-based)
# ---------------------------------------------------------------------------

_RE_WHERE = re.compile(r"\bWHERE\b", re.IGNORECASE)
_RE_ORDER_BY = re.compile(r"\bORDER\b\s+\bBY\b", re.IGNORECASE)
_RE_GROUP_BY = re.compile(r"\bGROUP\b\s+\bBY\b", re.IGNORECASE)
_RE_LIMIT = re.compile(r"\bLIMIT\b", re.IGNORECASE)
_RE_SELECT = re.compile(r"^\s*SELECT\b", re.IGNORECASE)
_RE_UPDATE = re.compile(r"^\s*UPDATE\b", re.IGNORECASE)
_RE_DELETE = re.compile(r"^\s*DELETE\b", re.IGNORECASE)
_RE_INSERT = re.compile(r"^\s*INSERT\b", re.IGNORECASE)
_RE_JOIN = re.compile(r"\bJOIN\b", re.IGNORECASE)

# Matches the primary table (and optional alias) in a FROM clause.
# Negative lookahead excludes SQL keywords so "FROM foo JOIN bar" doesn't
# capture "JOIN" as the alias of "foo".
_SQL_KW = (
    r"JOIN|WHERE|ON|GROUP|ORDER|LIMIT|HAVING|UNION|INNER|LEFT|RIGHT"
    r"|FULL|CROSS|OUTER|NATURAL|SELECT|FROM|SET|AS"
)
_RE_FROM_TABLE = re.compile(
    rf"\bFROM\s+(\w+)(?:\s+(?:AS\s+)?(?!(?:{_SQL_KW})\b)(\w+))?",
    re.IGNORECASE,
)


def _depth0_skeleton(sql: str) -> str:
    """Return ``sql`` with every parenthesized group (subqueries, CTE bodies)
    and string literal blanked, leaving only depth-0 (outer query) tokens.

    The RLS predicate is always injected at the *outer* WHERE (see
    ``_find_outer_where``), so JOIN detection and primary-alias resolution must
    also look only at the outer query scope. A naive whole-string scan grabs the
    first ``FROM ... <alias>`` inside a CTE body (e.g. ``WITH x AS (SELECT ...
    FROM t p ...) SELECT * FROM x WHERE ...``) and injects ``p.classification``
    into the outer WHERE where alias ``p`` is out of scope — PostgreSQL then
    raises ``missing FROM-clause entry for table "p"``.
    """
    out = []
    depth = 0
    i = 0
    n = len(sql)
    while i < n:
        ch = sql[i]
        if ch in ("'", '"'):
            quote = ch
            i += 1
            while i < n and sql[i] != quote:
                if sql[i] == '\\':
                    i += 1
                i += 1
            i += 1  # closing quote
            out.append(" ")  # blank the literal
            continue
        if ch == '(':
            depth += 1
            out.append(" ")
            i += 1
            continue
        if ch == ')':
            depth -= 1
            out.append(" ")
            i += 1
            continue
        out.append(ch if depth == 0 else " ")
        i += 1
    return "".join(out)


#: Table-name prefixes that identify a database SYSTEM CATALOG rather than an
#: application table. These carry no `tenant_id`/`classification` columns, so
#: injecting a row predicate into a query against one does not restrict
#: anything — it makes the statement invalid.
#:
#: This is not a hypothetical. `PgVectorStore._has_pgvector()` probes
#: `SELECT 1 FROM pg_extension WHERE extname = 'vector'`. With a security
#: context attached, that became
#: `... WHERE (classification IS NULL OR classification IN (...)) AND extname = ...`,
#: which raises UndefinedColumn on pg_extension. The probe's caller read the
#: failure as "pgvector is unavailable", so vector retrieval silently returned
#: nothing for EVERY authenticated request while working perfectly from a script
#: (no Flask context -> no RLS -> no injection). Nothing logged an error.
#: No trailing dots: `_RE_FROM_TABLE` captures `\w+`, which stops at the schema
#: separator, so a qualified `information_schema.columns` is seen here as the
#: bare token `information_schema`. `pg_catalog.pg_tables` is already covered by
#: the `pg_` prefix.
_SYSTEM_TABLE_PREFIXES = ("information_schema", "sqlite_", "pg_catalog", "pg_toast", "pg_stat", "pg_statio")

#: PostgreSQL catalog relations referred to by their bare name. xit-decl-04: the
#: exemption used to be the PREFIX ``pg_``, and an application prefix in this
#: tree also begins with ``pg_`` -- so those application tables, which carry
#: the very columns the predicate filters on, were exempted with the catalog.
#: Catalog relations are enumerated by NAME instead; an application table can
#: never match this set by accident. `pg_catalog.`/`pg_toast`/`pg_stat*` stay
#: prefix-matched because those are schemas, not tables.
_PG_CATALOG_RELATIONS = frozenset({
    "pg_class", "pg_attribute", "pg_namespace", "pg_type", "pg_proc", "pg_index", "pg_indexes",
    "pg_tables", "pg_views", "pg_matviews", "pg_sequences", "pg_constraint", "pg_extension",
    "pg_available_extensions", "pg_roles", "pg_user", "pg_shadow", "pg_authid", "pg_auth_members",
    "pg_database", "pg_settings", "pg_locks", "pg_description", "pg_depend", "pg_trigger",
    "pg_policy", "pg_policies", "pg_am", "pg_opclass", "pg_attrdef", "pg_inherits", "pg_enum",
    "pg_range", "pg_collation", "pg_tablespace", "pg_event_trigger", "pg_rewrite", "pg_language",
    "pg_cast", "pg_operator", "pg_aggregate", "pg_statistic", "pg_stats", "pg_foreign_table",
    "pg_foreign_server", "pg_foreign_data_wrapper", "pg_replication_slots", "pg_subscription",
    "pg_publication", "pg_publication_tables", "pg_largeobject", "pg_largeobject_metadata",
    "pg_timezone_names", "pg_prepared_statements", "pg_cursors", "pg_seclabel", "pg_shdescription",
    "pg_partitioned_table", "pg_ts_config", "pg_ts_dict", "pg_ts_parser", "pg_ts_template",
    "pg_user_mapping", "pg_default_acl", "pg_init_privs", "pg_transform", "pg_conversion",
    "pg_amop", "pg_amproc", "pg_opfamily", "pg_statistic_ext", "pg_sequence", "pg_group",
    "pg_rules", "pg_shmem_allocations", "pg_file_settings", "pg_hba_file_rules", "pg_config",
})


def _manifest_exempt_tables() -> frozenset:
    """Tables the ownership manifest marks ``rls_exempt`` (icdev.core.sensitivity).

    Empty unless a reviewer added a table to args/schema_ownership_rules.yaml's
    ``rls_exempt`` and regenerated. Fail CLOSED: an unimportable seam exempts
    nothing, because a missing exemption raises UndefinedColumn where a wrong
    exemption leaks rows.
    """
    try:
        from icdev.core.sensitivity import rls_exempt_tables

        return rls_exempt_tables()
    except Exception:  # noqa: BLE001
        return frozenset()


def _is_system_table(sql: str) -> bool:
    """True when the outer query's primary FROM target is a system catalog, or a
    table the ownership manifest explicitly exempts from row security.

    Deliberately reads the OUTER primary table only (via the depth-0 skeleton),
    so a catalog lookup nested inside a query over an application table cannot
    exempt that query from row security.
    """
    m = _RE_FROM_TABLE.search(_depth0_skeleton(sql))
    if not m:
        return False
    table = (m.group(1) or "").lower()
    if table in _PG_CATALOG_RELATIONS or any(table.startswith(p) for p in _SYSTEM_TABLE_PREFIXES):
        return True
    return table in _manifest_exempt_tables()


def _primary_alias(sql: str) -> str | None:
    """Return the alias (or table name) of the primary FROM table, or None.

    Operates on the depth-0 outer-query skeleton so a CTE/subquery's inner table
    alias is never mistaken for the outer query's table (see _depth0_skeleton).
    """
    m = _RE_FROM_TABLE.search(_depth0_skeleton(sql))
    if not m:
        return None
    return m.group(2) or m.group(1)


def _find_outer_where(sql: str) -> re.Match | None:
    """Find the first WHERE keyword at depth-0 (not inside a subquery/parens).

    A naive `_RE_WHERE.search(sql)` hits the first WHERE in the string, which
    may be inside a correlated subquery.  PostgreSQL then rejects RLS predicates
    that reference outer-query aliases from within the subquery scope.
    This walks the string once, counting paren depth, and returns a regex
    Match anchored at the first depth-0 WHERE so callers can use .end() safely.
    """
    depth = 0
    i = 0
    n = len(sql)
    while i < n:
        ch = sql[i]
        if ch == '(':
            depth += 1
            i += 1
        elif ch == ')':
            depth -= 1
            i += 1
        elif ch in ("'", '"'):
            quote = ch
            i += 1
            while i < n and sql[i] != quote:
                if sql[i] == '\\':
                    i += 1
                i += 1
            i += 1  # closing quote
        elif depth == 0:
            m = _RE_WHERE.match(sql, i)
            if m:
                return m
            i += 1
        else:
            i += 1
    return None


_RE_PLACEHOLDER = re.compile(r"\?|%s", re.IGNORECASE)


def _count_params_before(sql: str, pos: int) -> int:
    """Count positional placeholders (? or %s) in sql[:pos], skipping string literals.

    Used to determine how many existing params precede the RLS injection point
    so that extra_params can be inserted at the correct position rather than
    blindly prepended/appended.
    """
    count = 0
    i = 0
    while i < pos:
        ch = sql[i]
        if ch in ("'", '"'):
            quote = ch
            i += 1
            while i < pos and sql[i] != quote:
                if sql[i] == '\\':
                    i += 1
                i += 1
            i += 1
        elif sql[i:i+2] == '%s':
            count += 1
            i += 2
        elif ch == '?':
            count += 1
            i += 1
        else:
            i += 1
    return count


def inject_row_predicate(
    sql: str,
    tenant_id: Optional[str],
    classification: Optional[str] = None,
    classifications: Optional[Set[str]] = None,
    tenant_column: str = "tenant_id",
    classification_column: str = "classification",
    lac_labels: Optional[Set[str]] = None,
    lac_column: str = "lac_label",
    coi_tags: Optional[Set[str]] = None,
    coi_column: str = "coi_tag",
    placeholder: str = "?",
) -> Tuple[str, Tuple[Any, ...], int]:
    """Inject tenant, classification, LAC, and COI predicates into a SQL string.

    LAC (Label-Based Access Control, G-03): rows with lac_label=NULL are
    world-readable within the tenant; rows with a label must match the caller's
    set of allowed labels.

    COI (Community of Interest, G-09): rows with coi_tag=NULL are accessible
    to all within tenant+classification; rows with a tag must match the caller.

    Returns:
        (modified_sql, extra_params, n_params_before) where:
        - extra_params must be inserted at index n_params_before in the
          caller's existing params tuple so positional binding stays correct
          even when subqueries appear before the outer WHERE clause.
        - For UPDATE/DELETE n_params_before is set to len(existing_params)
          (i.e. append), since SET params precede all WHERE params.
    """
    # A system catalog has no tenant/classification columns. Injecting here
    # cannot restrict anything — it only produces an invalid statement, and the
    # caller usually reads the resulting error as "capability unavailable"
    # rather than "query malformed". See _SYSTEM_TABLE_PREFIXES.
    if _is_system_table(sql):
        return sql, (), 0

    extra_clauses: list[str] = []
    extra_params: list[Any] = []

    # When the OUTER query joins multiple tables both columns must be qualified
    # with the primary table's alias so PostgreSQL can resolve the reference
    # unambiguously (AmbiguousColumn). JOIN detection and alias resolution use
    # the depth-0 skeleton so a JOIN inside a CTE/subquery body does not trigger
    # qualification with an alias that is out of scope at the outer WHERE.
    if _RE_JOIN.search(_depth0_skeleton(sql)):
        alias = _primary_alias(sql)
        if alias:
            tenant_column = f"{alias}.{tenant_column}"
            classification_column = f"{alias}.{classification_column}"
            lac_column = f"{alias}.{lac_column}"
            coi_column = f"{alias}.{coi_column}"

    # Tenant predicate
    if tenant_id is not None:
        extra_clauses.append(f"{tenant_column} = {placeholder}")
        extra_params.append(tenant_id)
    else:
        logger.debug("inject_row_predicate called with tenant_id=None; tenant filter skipped")

    # Classification predicate — rows with NULL/empty classification are always
    # visible (unclassified data is world-readable within the tenant).
    # Only rows that have a non-empty classification must match the caller's level.
    if classifications:
        placeholders = ", ".join([placeholder] * len(classifications))
        sorted_classes = sorted(classifications)
        extra_clauses.append(
            f"({classification_column} IS NULL OR {classification_column} = '' "
            f"OR {classification_column} IN ({placeholders}))"
        )
        extra_params.extend(sorted_classes)
    elif classification:
        extra_clauses.append(
            f"({classification_column} IS NULL OR {classification_column} = '' "
            f"OR {classification_column} = {placeholder})"
        )
        extra_params.append(classification)

    # LAC predicate — NULL means world-readable within tenant+classification.
    if lac_labels:
        sorted_lac = sorted(lac_labels)
        placeholders = ", ".join([placeholder] * len(sorted_lac))
        extra_clauses.append(
            f"({lac_column} IS NULL OR {lac_column} IN ({placeholders}))"
        )
        extra_params.extend(sorted_lac)

    # COI predicate — NULL means accessible to all within tenant+classification.
    if coi_tags:
        sorted_coi = sorted(coi_tags)
        placeholders = ", ".join([placeholder] * len(sorted_coi))
        extra_clauses.append(
            f"({coi_column} IS NULL OR {coi_column} IN ({placeholders}))"
        )
        extra_params.extend(sorted_coi)

    if not extra_clauses:
        return sql, (), 0

    # Determine injection point
    predicate = " AND ".join(extra_clauses)

    # INSERT, DDL, PRAGMA: never modify — these have no WHERE clause and
    # injecting anything would corrupt the statement structure.
    if (
        _RE_INSERT.match(sql)
        or sql.strip().upper().startswith("PRAGMA")
        or sql.strip().upper().startswith("CREATE")
    ):
        return sql, (), 0

    # UPDATE/DELETE: predicate appended to the END of the WHERE clause so that
    # SQLite parameter binding stays correct — SET-slot params come first in
    # the param tuple, WHERE-slot params (including the injected predicate)
    # come after.  n_params_before=None signals "append" to the caller.
    if _RE_UPDATE.match(sql) or _RE_DELETE.match(sql):
        where_match = _find_outer_where(sql)
        if where_match:
            new_sql = sql.rstrip().rstrip(";") + " AND " + predicate
        else:
            new_sql = sql.rstrip().rstrip(";") + " WHERE " + predicate
        # -1 sentinel → caller appends (UPDATE/DELETE existing convention)
        return new_sql, tuple(extra_params), -1

    # SELECT (and anything else): predicate injected at the START of the outer
    # WHERE clause.  Use _find_outer_where so we skip any WHERE that lives
    # inside a correlated subquery — PostgreSQL cannot resolve outer-query
    # aliases (e.g. "s.classification") from within a subquery scope and will
    # raise "invalid reference to FROM-clause entry … HINT: use LATERAL".
    # n_params_before tells the caller how many existing params precede the
    # injection point, so extra_params can be inserted at the correct position
    # rather than blindly prepended (which breaks subqueries with their own ?).
    where_match = _find_outer_where(sql)
    if where_match:
        inject_at = where_match.start()  # position of WHERE keyword in original sql
        n_before = _count_params_before(sql, inject_at)
        pos = where_match.end()
        new_sql = sql[:pos] + " " + predicate + " AND" + sql[pos:]
        return new_sql, tuple(extra_params), n_before

    # No WHERE — inject before ORDER BY / GROUP BY / LIMIT. The injected WHERE
    # sits BEFORE these clauses, so n_params_before is the count of placeholders
    # that appear before the injection point — NOT the whole-statement total.
    # A trailing "LIMIT ?" / "ORDER BY x ?" param comes AFTER the injection and
    # must not be counted; otherwise its value gets bound into the injected
    # predicate (e.g. "classification IN (20, 'CUI')" — the LIMIT 20 leaking in).
    for pattern in (_RE_GROUP_BY, _RE_ORDER_BY, _RE_LIMIT):
        m = pattern.search(sql)
        if m:
            pos = m.start()
            n_before = _count_params_before(sql, pos)
            new_sql = sql[:pos] + " WHERE " + predicate + " " + sql[pos:]
            return new_sql, tuple(extra_params), n_before

    # No WHERE, no ORDER/GROUP/LIMIT — append at end; all existing params precede it.
    n_total = _count_params_before(sql, len(sql))
    new_sql = sql.rstrip().rstrip(";") + " WHERE " + predicate
    return new_sql, tuple(extra_params), n_total


# ---------------------------------------------------------------------------
# PostgreSQL RLS helpers
# ---------------------------------------------------------------------------

def generate_rls_policy(
    table: str,
    predicate_expr: str,
    roles: list[str],
    policy_name: Optional[str] = None,
    command: str = "ALL",
) -> str:
    """Emit a ``CREATE POLICY`` DDL statement for PostgreSQL.

    Args:
        table: table name
        predicate_expr: raw SQL predicate (e.g. "tenant_id = current_setting('app.tenant_id')")
        roles: list of role names to apply the policy to
        policy_name: optional policy name (defaults to rls_{table}_{command})
        command: ALL, SELECT, INSERT, UPDATE, DELETE
    """
    name = policy_name or f"rls_{table}_{command.lower()}"
    roles_str = ", ".join(roles)
    return (
        f"CREATE POLICY {name}\n"
        f"  ON {table}\n"
        f"  FOR {command}\n"
        f"  TO {roles_str}\n"
        f"  USING ({predicate_expr});"
    )


def apply_tenant_rls(
    conn,
    table: str,
    roles: Optional[list[str]] = None,
    tenant_setting: str = "app.tenant_id",
) -> None:
    """Auto-generate and execute a tenant-scoped RLS policy on PostgreSQL.

    Requires a PostgreSQL connection (not StorageConnection wrapper).
    """
    if roles is None:
        roles = ["tenant_admin", "developer", "compliance_officer", "auditor", "viewer"]
    predicate = f"tenant_id = current_setting('{tenant_setting}')"
    ddl = generate_rls_policy(table, predicate, roles)
    try:
        conn.execute(ddl)
        conn.commit()
        logger.info("Applied tenant RLS on %s", table)
    except Exception as exc:
        logger.warning("Could not apply tenant RLS on %s: %s", table, exc)


def set_pg_session_vars(conn, tenant_id: Optional[str], classification: Optional[str] = None) -> None:
    """Set PostgreSQL session variables for RLS predicates."""
    if tenant_id is not None:
        try:
            conn.execute("SELECT set_config('app.tenant_id', %s, false)", (tenant_id,))
        except Exception:
            conn.execute("SELECT set_config('app.tenant_id', %s, false)", (tenant_id,))
    if classification is not None:
        try:
            conn.execute("SELECT set_config('app.classification', %s, false)", (classification,))
        except Exception:
            conn.execute("SELECT set_config('app.classification', %s, false)", (classification,))


# ---------------------------------------------------------------------------
# Audit logging
# ---------------------------------------------------------------------------

def log_rls_event(
    conn,
    table: str,
    action: str,
    tenant_id: Optional[str],
    details: Optional[dict] = None,
) -> None:
    """Log an append-only RLS audit event."""
    try:
        from datetime import datetime, timezone
        conn.execute(
            """
            INSERT INTO rls_audit (table_name, action, tenant_id, details, recorded_at)
            VALUES (%s, %s, %s, %s, %s)
            """,
            (
                table,
                action,
                tenant_id,
                json.dumps(details or {}),
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        conn.commit()
    except Exception as exc:
        logger.debug("Could not log RLS event: %s", exc)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Row Security CLI")
    parser.add_argument("--test", action="store_true", help="Run self-test")
    parser.add_argument("--json", action="store_true", help="JSON output")
    args = parser.parse_args()

    if args.test:
        sql1, params1, n_before1 = inject_row_predicate(
            "SELECT * FROM projects WHERE status = ?", "tenant_a", "CUI"
        )
        sql2, params2, n_before2 = inject_row_predicate(
            "SELECT * FROM projects", "tenant_a", classifications={"CUI", "SECRET"}
        )
        results = {
            "where_injected": sql1,
            "where_params": params1,
            "where_n_before": n_before1,
            "no_where_injected": sql2,
            "no_where_params": params2,
            "no_where_n_before": n_before2,
        }
        print(json.dumps(results, indent=2) if args.json else str(results))


if __name__ == "__main__":
    main()

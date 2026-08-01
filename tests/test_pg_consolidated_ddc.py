# CUI // SP-CTI
"""Drift guard: all 44 Data Design Canvas (DDC) tables must appear in the
consolidated PostgreSQL bootstrap schema (tools/db/schema/pg_consolidated.sql).

On PG-primary, the Data Canvas routes to the SHARED icdev database
(tools/db/storage.py), so these are shared-DB tables. A fresh PG instance
bootstrapped from pg_consolidated.sql (tools/db/bootstrap_pg.py) only gets them
if they are in the consolidated snapshot — otherwise they exist only lazily via
the blueprint factory's swallowed init_db() (tools/data_canvas/blueprint.py).
This test fails closed if a DDC table (or a high-traffic column) is added to the
runtime initializer (tools/data_canvas/db/init_db.py) but not mirrored here.

Static parse only — no live PostgreSQL required.

Task: dcpr-db-01.
"""

import re
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SCHEMA_FILE = _REPO_ROOT / "tools" / "db" / "schema" / "pg_consolidated.sql"

# The 44 tables that make up the DDC schema:
#   - 43 from tools/data_canvas/db/init_db.py::SCHEMA
#   - dd_migration_jobs (CAM extension, added in init_db() after the SCHEMA blob)
# 6 of these (data_nodes, data_edges, data_twin_snapshots, dd_freshness_alerts,
# dm_ports, dd_pii_scans) already lived in the pg_dump body; the other 38 were
# added by the DDC additive section + migration 273 (dcpr-db-01).
DDC_TABLES = [
    # design core
    "data_designs",
    "dd_templates",
    "dd_snippets",
    "dd_assessments",
    "dd_audit",
    "dd_versions",
    "dd_collab_sessions",
    "dd_lineage",
    "data_nodes",
    "data_edges",
    "data_twin_snapshots",
    # ops runbooks / SOPs
    "ddc_runbooks",
    "ddc_runbook_executions",
    "ddc_sops",
    "ddc_sop_approvals",
    # data science
    "dd_explore_sessions",
    "dd_explore_profiles",
    "dd_anomaly_runs",
    "dd_query_history",
    "dd_quality_rules",
    "dd_quality_runs",
    "dd_freshness_alerts",
    # data mesh
    "dm_domains",
    "dm_data_products",
    "dm_contracts",
    "dm_input_ports",
    "dm_output_ports",
    "dm_ports",
    "dm_domain_maturity",
    "dm_governance_policies",
    "dm_catalog_entries",
    "dm_audit",
    "dm_opa_policies",
    "dm_policy_audit_log",
    "dm_csp_sync_log",
    "dm_product_slas",
    "dm_product_subscriptions",
    "dm_data_contracts",
    "dm_contract_test_runs",
    # AI data mapping
    "dd_mapping_sessions",
    "dd_field_mappings",
    "dd_mapping_transforms",
    "dd_pii_scans",
    # CAM extension
    "dd_migration_jobs",
]

# Columns referenced by high-traffic tables' runtime SQL. Derived from:
#   data_designs        -> tools/data_canvas/blueprint.py INSERT
#   dd_audit            -> tools/data_canvas/blueprint.py::_audit INSERT
#   dm_domains          -> tools/data_canvas/data_mesh.py INSERT
#   dd_mapping_sessions -> tools/data_canvas/db/init_db.py::SCHEMA
# The consolidated DDL column set MUST be a superset of each of these.
# (`user` normalizes from the quoted "user" identifier in the DDL.)
RUNTIME_COLUMNS = {
    "data_designs": {
        "id", "name", "description", "graph_json", "template_id",
        "classification", "created_at", "updated_at",
    },
    "dd_audit": {
        "design_id", "user", "action", "detail", "classification", "created_at",
    },
    "dm_domains": {
        "id", "name", "description", "owner", "status", "classification",
        "created_at", "updated_at",
    },
    "dd_mapping_sessions": {
        "id", "name", "source_format", "target_format", "status",
        "classification", "tenant_id", "created_at", "updated_at",
    },
}


@pytest.fixture(scope="module")
def schema_sql() -> str:
    assert _SCHEMA_FILE.exists(), f"consolidated schema not found: {_SCHEMA_FILE}"
    return _SCHEMA_FILE.read_text(encoding="utf-8-sig")


def _create_table_regex(table: str) -> re.Pattern:
    # Matches CREATE TABLE [IF NOT EXISTS] [public.]<table> ( — the file
    # schema-qualifies additive tables with `public.` and uses IF NOT EXISTS.
    return re.compile(
        r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?(?:public\.)?"
        + re.escape(table)
        + r"\s*\(",
        re.IGNORECASE,
    )


def _extract_table_block(sql: str, table: str) -> str:
    """Return the parenthesised body of the CREATE TABLE statement for `table`."""
    m = _create_table_regex(table).search(sql)
    assert m, f"CREATE TABLE for {table!r} not found in pg_consolidated.sql"
    start = sql.index("(", m.end() - 1)
    depth = 0
    for i in range(start, len(sql)):
        ch = sql[i]
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return sql[start + 1 : i]
    raise AssertionError(f"unbalanced parentheses in CREATE TABLE {table!r}")


def _column_names(table_block: str) -> set:
    """Best-effort column-name extraction from a CREATE TABLE body."""
    cols = set()
    depth = 0
    current = []
    defs = []
    for ch in table_block:
        if ch == "(":
            depth += 1
            current.append(ch)
        elif ch == ")":
            depth -= 1
            current.append(ch)
        elif ch == "," and depth == 0:
            defs.append("".join(current))
            current = []
        else:
            current.append(ch)
    if current:
        defs.append("".join(current))

    _constraint_kw = {"primary", "foreign", "check", "unique", "constraint"}
    for d in defs:
        tokens = d.strip().split()
        if not tokens:
            continue
        first = tokens[0].strip('"').lower()
        if first in _constraint_kw:
            continue
        cols.add(first)
    return cols


@pytest.mark.parametrize("table", DDC_TABLES)
def test_ddc_table_present(schema_sql, table):
    """Each of the 44 DDC tables has a CREATE TABLE statement in the baseline."""
    assert _create_table_regex(table).search(schema_sql), (
        f"DDC table {table!r} missing from pg_consolidated.sql — a fresh PG "
        f"bootstrap would lack it (see task dcpr-db-01)."
    )


def test_all_44_ddc_tables_counted(schema_sql):
    """Sanity: exactly the 44 expected DDC tables, no more no fewer, are asserted."""
    assert len(DDC_TABLES) == 44
    found = [t for t in DDC_TABLES if _create_table_regex(t).search(schema_sql)]
    assert sorted(found) == sorted(DDC_TABLES), (
        f"missing DDC tables: {sorted(set(DDC_TABLES) - set(found))}"
    )


@pytest.mark.parametrize("table", sorted(RUNTIME_COLUMNS))
def test_ddc_ddl_superset_of_runtime_columns(schema_sql, table):
    """Consolidated DDL columns are a superset of runtime-referenced columns
    for the highest-traffic DDC tables."""
    block = _extract_table_block(schema_sql, table)
    ddl_cols = _column_names(block)
    missing = RUNTIME_COLUMNS[table] - ddl_cols
    assert not missing, (
        f"{table}: consolidated DDL is missing runtime-referenced column(s) "
        f"{sorted(missing)}. DDL columns present: {sorted(ddl_cols)}"
    )

# CUI // SP-CTI
"""Drift guard: all 17 Pipeline Design Canvas (PDC) tables must appear in the
consolidated PostgreSQL bootstrap schema (tools/db/schema/pg_consolidated.sql).

On PG-primary, the canvas db_path 'pipeline_canvas' routes to the SHARED icdev
database (tools/db/storage.py), so these are shared-DB tables. A fresh PG
instance bootstrapped from pg_consolidated.sql (tools/db/bootstrap_pg.py) only
gets them if they are in the consolidated snapshot — otherwise they exist only
lazily via the blueprint factory's swallowed init_db(). This test fails closed
if a PDC table (or a high-traffic column) is added to the runtime initializer
(tools/pipeline/db/init_db.py) or migration 027 but not mirrored here.

Static parse only — no live PostgreSQL required.

Task: pdx-data-04.
"""

import re
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SCHEMA_FILE = _REPO_ROOT / "tools" / "db" / "schema" / "pg_consolidated.sql"

# The 17 tables that make up the PDC schema:
#   - 16 from tools/pipeline/db/init_db.py::SCHEMA
#   - pipeline_snapshots from tools/db/migrations/027_pipeline_snapshots/up.py
# pdx-data-01 (PR #441) confirmed pdc_snapshots is authoritative AND
# pipeline_snapshots stays, so all 17 belong in the consolidated baseline.
PDC_TABLES = [
    "pipelines",
    "pc_templates",
    "pc_snippets",
    "pc_versions",
    "pc_audit",
    "pc_stages",
    "pc_compliance_checks",
    "pc_compliance_findings",
    "pc_boundaries",
    "pc_projects",
    "pc_project_pipelines",
    "pc_change_requests",
    "pc_collab_sessions",
    "pdc_sops",
    "pdc_snapshots",
    "pdc_simulations",
    "pipeline_snapshots",
]

# Columns referenced by the 4 highest-traffic tables' runtime SQL. Derived from:
#   pipelines      -> tools/pipeline/blueprint.py INSERT statements
#   pdc_snapshots  -> tools/pipeline/twin.py INSERT
#   pdc_sops       -> tools/pipeline/sops.py INSERT
#   pc_audit       -> tools/pipeline/blueprint.py audit INSERT
# The consolidated DDL column set MUST be a superset of each of these.
RUNTIME_COLUMNS = {
    "pipelines": {
        "id", "name", "description", "graph_json", "template_id",
        "classification", "target_csp", "created_at", "updated_at",
    },
    "pdc_snapshots": {
        "id", "pipeline_id", "label", "graph_json", "node_count",
        "edge_count", "created_by", "created_at",
    },
    "pdc_sops": {
        "id", "title", "sop_type", "description", "purpose", "scope",
        "steps", "nist_controls", "owner", "reviewer", "approval_status",
        "version", "next_review_date", "classification",
        "created_at", "updated_at",
    },
    "pc_audit": {
        "action", "entity_type", "entity_id", "details", "user_id", "ts",
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
    """Return the parenthesised body of the CREATE TABLE statement for `table`.

    Balanced-paren scan from the opening '(' of the matched CREATE TABLE.
    """
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
    """Best-effort column-name extraction from a CREATE TABLE body.

    Splits top-level (depth-0) comma-separated defs and takes the first token
    of each, skipping table-level constraint clauses (PRIMARY KEY, FOREIGN KEY,
    CHECK, UNIQUE, CONSTRAINT ...).
    """
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


@pytest.mark.parametrize("table", PDC_TABLES)
def test_pdc_table_present(schema_sql, table):
    """Each of the 17 PDC tables has a CREATE TABLE statement in the baseline."""
    assert _create_table_regex(table).search(schema_sql), (
        f"PDC table {table!r} missing from pg_consolidated.sql — a fresh PG "
        f"bootstrap would lack it (see task pdx-data-04)."
    )


def test_all_17_pdc_tables_counted(schema_sql):
    """Sanity: exactly the 17 expected PDC tables, no more no fewer, are asserted."""
    assert len(PDC_TABLES) == 17
    found = [t for t in PDC_TABLES if _create_table_regex(t).search(schema_sql)]
    assert sorted(found) == sorted(PDC_TABLES), (
        f"missing PDC tables: {sorted(set(PDC_TABLES) - set(found))}"
    )


@pytest.mark.parametrize("table", sorted(RUNTIME_COLUMNS))
def test_pdc_ddl_superset_of_runtime_columns(schema_sql, table):
    """Consolidated DDL columns are a superset of runtime-referenced columns
    for the 4 highest-traffic PDC tables."""
    block = _extract_table_block(schema_sql, table)
    ddl_cols = _column_names(block)
    missing = RUNTIME_COLUMNS[table] - ddl_cols
    assert not missing, (
        f"{table}: consolidated DDL is missing runtime-referenced column(s) "
        f"{sorted(missing)}. DDL columns present: {sorted(ddl_cols)}"
    )

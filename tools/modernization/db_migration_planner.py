#!/usr/bin/env python3
# CUI // SP-CTI
"""Database Migration Planning Tool for ICDEV DoD Modernization.

Generates DDL scripts, data migration SQL, stored procedure translations, and
validation queries for migrating legacy databases to modern targets (PostgreSQL,
MySQL, Aurora). All output is generated as SQL files for DBA review — nothing
is executed directly (air-gap safe).

Reads legacy schema metadata from icdev.db (legacy_applications, legacy_db_schemas)
and type/function/syntax mappings from context/modernization/db_type_mappings.json.

All generated artifacts include CUI // SP-CTI banners as required for Controlled
Unclassified Information handling.

Usage:
    python tools/modernization/db_migration_planner.py --app-id APP-001 --output-dir /tmp/migration --type all
    python tools/modernization/db_migration_planner.py --app-id APP-001 --output-dir /tmp/migration --type schema
    python tools/modernization/db_migration_planner.py --app-id APP-001 --output-dir /tmp/migration --type data
    python tools/modernization/db_migration_planner.py --app-id APP-001 --output-dir /tmp/migration --type procedures --source-path /opt/legacy/sql
    python tools/modernization/db_migration_planner.py --app-id APP-001 --output-dir /tmp/migration --type validation
    python tools/modernization/db_migration_planner.py --app-id APP-001 --output-dir /tmp/migration --type all --json
"""

import argparse
import json
import os
import re
import sqlite3
import sys
import textwrap
from collections import OrderedDict
from datetime import datetime
from pathlib import Path

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = BASE_DIR / "data" / "icdev.db"
TYPE_MAPPINGS_PATH = BASE_DIR / "context" / "modernization" / "db_type_mappings.json"

CUI_BANNER = "CUI // SP-CTI"
SQL_CUI_HEADER = (
    f"-- {'=' * 68}\n"
    f"-- {CUI_BANNER}\n"
    f"-- {'=' * 68}\n"
)
SQL_CUI_FOOTER = (
    f"\n-- {'=' * 68}\n"
    f"-- {CUI_BANNER}\n"
    f"-- {'=' * 68}\n"
)
MD_CUI_HEADER = f"<!-- {CUI_BANNER} -->"
MD_CUI_FOOTER = f"<!-- {CUI_BANNER} -->"

# Estimated byte sizes per data type for volume estimation
TYPE_SIZE_ESTIMATES = {
    "VARCHAR": 50,
    "CHAR": 10,
    "TEXT": 500,
    "INTEGER": 8,
    "INT": 8,
    "SMALLINT": 4,
    "BIGINT": 8,
    "NUMERIC": 16,
    "DECIMAL": 16,
    "REAL": 4,
    "FLOAT": 8,
    "DOUBLE PRECISION": 8,
    "BOOLEAN": 1,
    "DATE": 4,
    "TIMESTAMP": 8,
    "TIMESTAMPTZ": 8,
    "TIME": 8,
    "BYTEA": 256,
    "UUID": 16,
    "JSON": 200,
    "JSONB": 200,
    "XML": 500,
    "BLOB": 1024,
    "CLOB": 1024,
    "IMAGE": 1024,
}

# Normalised lookup key for migration path resolution
_PATH_ALIASES = {
    "oracle": "oracle",
    "mssql": "mssql",
    "sqlserver": "mssql",
    "sql server": "mssql",
    "microsoft sql server": "mssql",
    "db2": "db2",
    "ibm db2": "db2",
    "sybase": "sybase",
    "sap sybase": "sybase",
    "sap sybase ase": "sybase",
    "mysql": "mysql",
    "postgresql": "postgresql",
    "postgres": "postgresql",
    "aurora": "postgresql",
}


# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------

def _get_db() -> sqlite3.Connection:
    """Return a sqlite3 connection with Row factory for dict-style access."""
    if not DB_PATH.exists():
        print(f"ERROR: Database not found at {DB_PATH}", file=sys.stderr)
        print("Run 'python tools/db/init_icdev_db.py' first.", file=sys.stderr)
        sys.exit(1)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def _normalise_db_name(name: str) -> str:
    """Normalise a database system name to a canonical key."""
    return _PATH_ALIASES.get(name.lower().strip(), name.lower().strip())


# ---------------------------------------------------------------------------
# Type mapping loader
# ---------------------------------------------------------------------------

def load_type_mappings(source_db: str, target_db: str) -> dict:
    """Load type, function, and syntax mappings for a specific migration path.

    Reads context/modernization/db_type_mappings.json and locates the matching
    migration path entry (e.g., oracle_to_postgresql).

    Returns a dict with keys: data_type_mappings, function_mappings,
    syntax_mappings. Each value is a list of mapping dicts from the JSON.
    Returns empty lists if no matching path is found.
    """
    if not TYPE_MAPPINGS_PATH.exists():
        print(f"WARNING: Type mappings file not found at {TYPE_MAPPINGS_PATH}",
              file=sys.stderr)
        return {
            "data_type_mappings": [],
            "function_mappings": [],
            "syntax_mappings": [],
        }

    with open(TYPE_MAPPINGS_PATH, "r", encoding="utf-8") as fh:
        data = json.load(fh)

    src_key = _normalise_db_name(source_db)
    tgt_key = _normalise_db_name(target_db)

    migration_paths = data.get("migration_paths", {})

    # Try exact path key first (e.g., "oracle_to_postgresql")
    path_key = f"{src_key}_to_{tgt_key}"
    if path_key in migration_paths:
        entry = migration_paths[path_key]
        return {
            "data_type_mappings": entry.get("data_type_mappings", []),
            "function_mappings": entry.get("function_mappings", []),
            "syntax_mappings": entry.get("syntax_mappings", []),
        }

    # Fallback: iterate and match by source/target field values
    for _key, entry in migration_paths.items():
        entry_src = _normalise_db_name(entry.get("source", ""))
        entry_tgt = _normalise_db_name(entry.get("target", ""))
        if entry_src == src_key and entry_tgt == tgt_key:
            return {
                "data_type_mappings": entry.get("data_type_mappings", []),
                "function_mappings": entry.get("function_mappings", []),
                "syntax_mappings": entry.get("syntax_mappings", []),
            }

    print(f"WARNING: No migration path found for {source_db} -> {target_db}",
          file=sys.stderr)
    return {
        "data_type_mappings": [],
        "function_mappings": [],
        "syntax_mappings": [],
    }


# ---------------------------------------------------------------------------
# Data type conversion
# ---------------------------------------------------------------------------

def _map_data_type(source_type: str, source_db: str, target_db: str,
                   mappings: dict) -> str:
    """Convert a single data type from source to target using mappings.

    Handles parametric types such as NUMBER(10,2) -> NUMERIC(10,2) and
    VARCHAR2(100) -> VARCHAR(100). If no mapping is found the source type
    is returned with a -- TODO comment appended.
    """
    raw = source_type.strip()

    # Extract base type and optional parameters — e.g. "NUMBER(10,2)" -> ("NUMBER", "(10,2)")
    param_match = re.match(r'^([A-Za-z_][A-Za-z0-9_ ]*)\s*(\(.*\))?$', raw)
    if param_match:
        base_type = param_match.group(1).strip().upper()
        params = param_match.group(2) or ""
    else:
        base_type = raw.upper()
        params = ""

    # Build a quick lookup from the data_type_mappings list
    type_map = {}
    for m in mappings.get("data_type_mappings", []):
        src = m.get("source_type", "").upper().strip()
        tgt = m.get("target_type", "").strip()
        type_map[src] = tgt

    if base_type in type_map:
        target_base = type_map[base_type]
        # If target already contains params (e.g. NUMERIC(19,4)), use as-is
        if "(" in target_base:
            return target_base
        return f"{target_base}{params}" if params else target_base

    # Try full type string match (e.g. "TINYINT(1)")
    full_upper = raw.upper()
    if full_upper in type_map:
        return type_map[full_upper]

    # No mapping found — return source with TODO
    return f"{raw}  -- TODO: unmapped type from {source_db}, review manually"


# ---------------------------------------------------------------------------
# Function translation
# ---------------------------------------------------------------------------

def translate_functions(app_id: str, target_db: str, content: str) -> str:
    """Translate built-in function calls within SQL content.

    Applies regex-based replacements for common function conversions from
    Oracle and MSSQL to PostgreSQL (or other target). Uses the function_mappings
    from the context JSON where possible, supplemented by hard-coded patterns
    for complex transforms that require regex.
    """
    if not content:
        return content

    tgt = _normalise_db_name(target_db)
    result = content

    # ------- Oracle -> PostgreSQL translations -------
    # NVL2(a, b, c) -> CASE WHEN a IS NOT NULL THEN b ELSE c END
    result = re.sub(
        r'\bNVL2\s*\(\s*([^,]+?)\s*,\s*([^,]+?)\s*,\s*([^)]+?)\s*\)',
        r'CASE WHEN \1 IS NOT NULL THEN \2 ELSE \3 END',
        result, flags=re.IGNORECASE
    )
    # NVL(a, b) -> COALESCE(a, b)
    result = re.sub(
        r'\bNVL\s*\(\s*([^,]+?)\s*,\s*([^)]+?)\s*\)',
        r'COALESCE(\1, \2)',
        result, flags=re.IGNORECASE
    )
    # DECODE(a, b, c, d) -> CASE WHEN a=b THEN c ELSE d END
    result = re.sub(
        r'\bDECODE\s*\(\s*([^,]+?)\s*,\s*([^,]+?)\s*,\s*([^,]+?)\s*,\s*([^)]+?)\s*\)',
        r'CASE WHEN \1 = \2 THEN \3 ELSE \4 END',
        result, flags=re.IGNORECASE
    )
    # TO_DATE(s, 'fmt') -> TO_TIMESTAMP(s, 'fmt')
    result = re.sub(
        r'\bTO_DATE\s*\(',
        'TO_TIMESTAMP(',
        result, flags=re.IGNORECASE
    )
    # SYSDATE -> CURRENT_TIMESTAMP
    result = re.sub(
        r'\bSYSDATE\b',
        'CURRENT_TIMESTAMP',
        result, flags=re.IGNORECASE
    )
    # SUBSTR(s, p, l) -> SUBSTRING(s FROM p FOR l)
    result = re.sub(
        r'\bSUBSTR\s*\(\s*([^,]+?)\s*,\s*([^,]+?)\s*,\s*([^)]+?)\s*\)',
        r'SUBSTRING(\1 FROM \2 FOR \3)',
        result, flags=re.IGNORECASE
    )
    # INSTR(s, sub) -> POSITION(sub IN s)
    result = re.sub(
        r'\bINSTR\s*\(\s*([^,]+?)\s*,\s*([^)]+?)\s*\)',
        r'POSITION(\2 IN \1)',
        result, flags=re.IGNORECASE
    )

    # ------- MSSQL -> PostgreSQL translations -------
    # GETDATE() -> CURRENT_TIMESTAMP
    result = re.sub(
        r'\bGETDATE\s*\(\s*\)',
        'CURRENT_TIMESTAMP',
        result, flags=re.IGNORECASE
    )
    # ISNULL(a, b) -> COALESCE(a, b)
    result = re.sub(
        r'\bISNULL\s*\(\s*([^,]+?)\s*,\s*([^)]+?)\s*\)',
        r'COALESCE(\1, \2)',
        result, flags=re.IGNORECASE
    )
    # LEN(s) -> LENGTH(s)
    result = re.sub(
        r'\bLEN\s*\(',
        'LENGTH(',
        result, flags=re.IGNORECASE
    )
    # CHARINDEX(sub, s) -> POSITION(sub IN s)
    result = re.sub(
        r'\bCHARINDEX\s*\(\s*([^,]+?)\s*,\s*([^)]+?)\s*\)',
        r'POSITION(\1 IN \2)',
        result, flags=re.IGNORECASE
    )
    # DATEADD(day, n, d) -> d + INTERVAL 'n days'
    result = re.sub(
        r'\bDATEADD\s*\(\s*(\w+)\s*,\s*([^,]+?)\s*,\s*([^)]+?)\s*\)',
        r"\3 + INTERVAL '\2 \1s'",
        result, flags=re.IGNORECASE
    )
    # DATEDIFF(day, a, b) -> EXTRACT(DAY FROM b - a)
    result = re.sub(
        r'\bDATEDIFF\s*\(\s*(\w+)\s*,\s*([^,]+?)\s*,\s*([^)]+?)\s*\)',
        r'EXTRACT(\1 FROM \3 - \2)',
        result, flags=re.IGNORECASE
    )
    # CONVERT(type, val) -> CAST(val AS type)
    result = re.sub(
        r'\bCONVERT\s*\(\s*([^,]+?)\s*,\s*([^)]+?)\s*\)',
        r'CAST(\2 AS \1)',
        result, flags=re.IGNORECASE
    )

    return result


# ---------------------------------------------------------------------------
# Schema DDL generation
# ---------------------------------------------------------------------------

def generate_schema_ddl(app_id: str, target_db: str, output_dir: str) -> str:
    """Generate CREATE TABLE DDL for the target database.

    Reads all legacy_db_schemas rows for the given app_id, groups by table,
    maps data types, and produces a complete DDL script with primary key
    constraints, NOT NULL, DEFAULT values, and foreign key constraints.
    Also generates CREATE INDEX for foreign key columns.

    Returns the path to the generated SQL file.
    """
    conn = _get_db()
    try:
        rows = conn.execute(
            """SELECT lds.*, la.primary_language
               FROM legacy_db_schemas lds
               JOIN legacy_applications la ON la.id = lds.legacy_app_id
               WHERE lds.legacy_app_id = ?
               ORDER BY lds.schema_name, lds.table_name, lds.column_name""",
            (app_id,)
        ).fetchall()
    finally:
        conn.close()

    if not rows:
        print(f"WARNING: No schema data found for app_id={app_id}", file=sys.stderr)
        return ""

    # Determine source DB type from first row
    source_db = rows[0]["db_type"]
    mappings = load_type_mappings(source_db, target_db)

    # Group rows by (schema_name, table_name)
    tables = OrderedDict()
    for row in rows:
        key = (row["schema_name"] or "public", row["table_name"])
        if key not in tables:
            tables[key] = []
        tables[key].append(dict(row))

    lines = []
    lines.append(SQL_CUI_HEADER)
    lines.append("-- DDL Migration Script")
    lines.append(f"-- Source: {source_db} -> Target: {target_db}")
    lines.append(f"-- Application ID: {app_id}")
    lines.append(f"-- Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append("-- WARNING: Review before execution. Do NOT run unreviewed.")
    lines.append(f"-- {'=' * 68}")
    lines.append("")

    index_statements = []

    for (schema, table), columns in tables.items():
        qualified = f"{schema}.{table}" if schema and schema != "public" else table

        lines.append(f"-- Table: {qualified}")
        lines.append(f"CREATE TABLE IF NOT EXISTS {qualified} (")

        col_defs = []
        pk_columns = []
        fk_constraints = []

        for col in columns:
            mapped_type = _map_data_type(
                col["data_type"], source_db, target_db, mappings
            )
            parts = [f"    {col['column_name']}", mapped_type]

            # NOT NULL
            if not col["is_nullable"]:
                parts.append("NOT NULL")

            # DEFAULT
            if col["default_value"]:
                default_val = _translate_default(col["default_value"], source_db, target_db)
                parts.append(f"DEFAULT {default_val}")

            col_defs.append(" ".join(parts))

            # Collect primary key columns
            if col["is_primary_key"]:
                pk_columns.append(col["column_name"])

            # Collect foreign keys
            if col["is_foreign_key"] and col["foreign_table"]:
                fk_name = f"fk_{table}_{col['column_name']}"
                fk_constraints.append(
                    f"    CONSTRAINT {fk_name} FOREIGN KEY ({col['column_name']}) "
                    f"REFERENCES {col['foreign_table']}({col['foreign_column'] or 'id'})"
                )
                # Index for FK column
                idx_name = f"idx_{table}_{col['column_name']}"
                index_statements.append(
                    f"CREATE INDEX IF NOT EXISTS {idx_name} ON {qualified}({col['column_name']});"
                )

        # Add primary key constraint
        if pk_columns:
            pk_name = f"pk_{table}"
            col_defs.append(
                f"    CONSTRAINT {pk_name} PRIMARY KEY ({', '.join(pk_columns)})"
            )

        # Add foreign key constraints
        col_defs.extend(fk_constraints)

        lines.append(",\n".join(col_defs))
        lines.append(");")
        lines.append("")

    # Index statements
    if index_statements:
        lines.append("-- Foreign key indexes")
        lines.extend(index_statements)
        lines.append("")

    lines.append(SQL_CUI_FOOTER)

    # Write output
    out_path = Path(output_dir)
    out_path.mkdir(parents=True, exist_ok=True)
    file_path = out_path / "schema_ddl.sql"
    file_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"Schema DDL written to {file_path}")
    return str(file_path)


def _translate_default(default_value: str, source_db: str, target_db: str) -> str:
    """Translate default value expressions from source to target dialect."""
    val = default_value.strip()
    src = _normalise_db_name(source_db)

    # Oracle-specific defaults
    if src == "oracle":
        if val.upper() == "SYSDATE":
            return "CURRENT_TIMESTAMP"
        if val.upper() == "SYS_GUID()":
            return "gen_random_uuid()"
        # sequence.NEXTVAL -> nextval('sequence')
        nextval_match = re.match(r"(\w+)\.NEXTVAL", val, re.IGNORECASE)
        if nextval_match:
            return f"nextval('{nextval_match.group(1)}')"

    # MSSQL-specific defaults
    if src == "mssql":
        if val.upper() in ("GETDATE()", "(GETDATE())"):
            return "CURRENT_TIMESTAMP"
        if val.upper() in ("NEWID()", "(NEWID())"):
            return "gen_random_uuid()"
        # Strip outer parentheses common in MSSQL defaults
        stripped = re.sub(r'^\((.+)\)$', r'\1', val)
        if stripped != val:
            return stripped

    return val


# ---------------------------------------------------------------------------
# Data migration script generation
# ---------------------------------------------------------------------------

def generate_data_migration_scripts(app_id: str, target_db: str,
                                    output_dir: str) -> str:
    """Generate INSERT/SELECT migration SQL for each table.

    For each table produces:
    - INSERT INTO target_table SELECT ... FROM source_table
    - Type casts where data types changed
    - Function translations (NVL->COALESCE, etc.)
    - Row count validation queries

    Returns the path to the generated SQL file.
    """
    conn = _get_db()
    try:
        rows = conn.execute(
            """SELECT lds.*, la.primary_language
               FROM legacy_db_schemas lds
               JOIN legacy_applications la ON la.id = lds.legacy_app_id
               WHERE lds.legacy_app_id = ?
               ORDER BY lds.schema_name, lds.table_name, lds.column_name""",
            (app_id,)
        ).fetchall()
    finally:
        conn.close()

    if not rows:
        print(f"WARNING: No schema data found for app_id={app_id}", file=sys.stderr)
        return ""

    source_db = rows[0]["db_type"]
    mappings = load_type_mappings(source_db, target_db)

    # Group by table
    tables = OrderedDict()
    for row in rows:
        key = (row["schema_name"] or "public", row["table_name"])
        if key not in tables:
            tables[key] = []
        tables[key].append(dict(row))

    lines = []
    lines.append(SQL_CUI_HEADER)
    lines.append("-- Data Migration Script")
    lines.append(f"-- Source: {source_db} -> Target: {target_db}")
    lines.append(f"-- Application ID: {app_id}")
    lines.append(f"-- Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append("-- WARNING: Review before execution. Do NOT run unreviewed.")
    lines.append(f"-- {'=' * 68}")
    lines.append("")

    validation_lines = []

    for (schema, table), columns in tables.items():
        qualified = f"{schema}.{table}" if schema and schema != "public" else table
        source_qualified = f"{schema}.{table}" if schema and schema != "public" else table

        lines.append(f"-- Migrate data: {qualified}")
        lines.append(f"-- Source table: {source_qualified}")
        lines.append("")

        # Build SELECT column list with casts where types changed
        select_parts = []
        col_names = []
        for col in columns:
            col_name = col["column_name"]
            col_names.append(col_name)

            source_type = col["data_type"].strip().upper()
            mapped_type = _map_data_type(col["data_type"], source_db, target_db, mappings)

            # Strip TODO comments for comparison
            clean_mapped = mapped_type.split("--")[0].strip().upper()

            # Extract base type from mapped for comparison
            source_base = re.match(r'^([A-Za-z_][A-Za-z0-9_ ]*)', source_type)
            target_base = re.match(r'^([A-Za-z_][A-Za-z0-9_ ]*)', clean_mapped)
            source_base_str = source_base.group(1).strip() if source_base else source_type
            target_base_str = target_base.group(1).strip() if target_base else clean_mapped

            if source_base_str != target_base_str and "TODO" not in mapped_type:
                # Type changed — add explicit CAST
                select_parts.append(f"CAST({col_name} AS {clean_mapped}) AS {col_name}")
            else:
                select_parts.append(col_name)

        # Apply function translations to the SELECT list
        select_str = ",\n        ".join(select_parts)
        select_str = translate_functions(app_id, target_db, select_str)

        target_cols = ", ".join(col_names)

        lines.append(f"INSERT INTO {qualified} ({target_cols})")
        lines.append("    SELECT")
        lines.append(f"        {select_str}")
        lines.append(f"    FROM {source_qualified};")
        lines.append("")

        # Row count validation
        validation_lines.append(f"-- Validate row counts: {qualified}")
        validation_lines.append(
            f"SELECT 'source' AS side, COUNT(*) AS row_count FROM {source_qualified};"
        )
        validation_lines.append(
            f"SELECT 'target' AS side, COUNT(*) AS row_count FROM {qualified};"
        )
        validation_lines.append("")

    # Append validation section
    lines.append(f"-- {'=' * 68}")
    lines.append("-- ROW COUNT VALIDATION")
    lines.append(f"-- {'=' * 68}")
    lines.append("")
    lines.extend(validation_lines)

    lines.append(SQL_CUI_FOOTER)

    out_path = Path(output_dir)
    out_path.mkdir(parents=True, exist_ok=True)
    file_path = out_path / "data_migration.sql"
    file_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"Data migration script written to {file_path}")
    return str(file_path)


# ---------------------------------------------------------------------------
# Stored procedure translation
# ---------------------------------------------------------------------------

def translate_stored_procedures(app_id: str, target_db: str,
                                source_path: str, output_dir: str) -> tuple:
    """Translate stored procedure and function SQL files to target dialect.

    Scans source_path for .sql files containing CREATE PROCEDURE/FUNCTION,
    applies syntax mappings (PL/SQL -> PL/pgSQL, T-SQL -> PL/pgSQL), and
    writes translated output.

    Returns (file_path, untranslatable_constructs) where
    untranslatable_constructs is a list of strings describing items needing
    manual review.
    """
    src_dir = Path(source_path)
    if not src_dir.exists():
        print(f"WARNING: Source path does not exist: {source_path}", file=sys.stderr)
        return ("", [])

    # Collect .sql files that contain procedure/function definitions
    sql_files = []
    if src_dir.is_file() and src_dir.suffix.lower() == ".sql":
        sql_files.append(src_dir)
    elif src_dir.is_dir():
        for root, _dirs, files in os.walk(str(src_dir)):
            for fname in sorted(files):
                if fname.lower().endswith(".sql"):
                    sql_files.append(Path(root) / fname)

    if not sql_files:
        print(f"WARNING: No .sql files found in {source_path}", file=sys.stderr)
        return ("", [])

    # Determine source DB from the app record
    conn = _get_db()
    try:
        app_row = conn.execute(
            "SELECT * FROM legacy_applications WHERE id = ?", (app_id,)
        ).fetchone()
        schema_row = conn.execute(
            "SELECT db_type FROM legacy_db_schemas WHERE legacy_app_id = ? LIMIT 1",
            (app_id,)
        ).fetchone()
    finally:
        conn.close()

    source_db = schema_row["db_type"] if schema_row else "oracle"
    src_key = _normalise_db_name(source_db)
    mappings = load_type_mappings(source_db, target_db)

    untranslatable = []
    translated_blocks = []

    translated_blocks.append(SQL_CUI_HEADER)
    translated_blocks.append("-- Stored Procedure Translation")
    translated_blocks.append(f"-- Source: {source_db} -> Target: {target_db}")
    translated_blocks.append(f"-- Application ID: {app_id}")
    translated_blocks.append(f"-- Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    translated_blocks.append("-- WARNING: Review before execution. Manual review required.")
    translated_blocks.append(f"-- {'=' * 68}")
    translated_blocks.append("")

    proc_pattern = re.compile(
        r'\bCREATE\s+(OR\s+REPLACE\s+)?(PROCEDURE|FUNCTION)\b',
        re.IGNORECASE
    )

    for sql_file in sql_files:
        content = sql_file.read_text(encoding="utf-8", errors="replace")

        # Only process files containing procedure/function definitions
        if not proc_pattern.search(content):
            continue

        translated_blocks.append(f"-- Source file: {sql_file.name}")
        translated_blocks.append(f"-- {'=' * 50}")

        translated_content = content

        # Apply Oracle PL/SQL -> PL/pgSQL conversions
        if src_key == "oracle":
            # IS -> AS (in procedure/function declarations)
            translated_content = re.sub(
                r'\b(CREATE\s+(?:OR\s+REPLACE\s+)?(?:PROCEDURE|FUNCTION)\s+\w+[^;]*?)\bIS\b',
                r'\1AS',
                translated_content, flags=re.IGNORECASE
            )
            # VARCHAR2 -> VARCHAR
            translated_content = re.sub(
                r'\bVARCHAR2\b', 'VARCHAR',
                translated_content, flags=re.IGNORECASE
            )
            # NUMBER -> NUMERIC
            translated_content = re.sub(
                r'\bNUMBER\b', 'NUMERIC',
                translated_content, flags=re.IGNORECASE
            )
            # DBMS_OUTPUT.PUT_LINE -> RAISE NOTICE
            translated_content = re.sub(
                r"\bDBMS_OUTPUT\.PUT_LINE\s*\(\s*'([^']*)'\s*\)",
                r"RAISE NOTICE '\1'",
                translated_content, flags=re.IGNORECASE
            )
            translated_content = re.sub(
                r"\bDBMS_OUTPUT\.PUT_LINE\s*\(\s*([^)]+)\s*\)",
                r"RAISE NOTICE '%', \1",
                translated_content, flags=re.IGNORECASE
            )
            # sequence.NEXTVAL -> nextval('sequence')
            translated_content = re.sub(
                r'\b(\w+)\.NEXTVAL\b',
                r"nextval('\1')",
                translated_content, flags=re.IGNORECASE
            )
            # sequence.CURRVAL -> currval('sequence')
            translated_content = re.sub(
                r'\b(\w+)\.CURRVAL\b',
                r"currval('\1')",
                translated_content, flags=re.IGNORECASE
            )

            # Detect untranslatable constructs
            if re.search(r'\bCONNECT\s+BY\b', translated_content, re.IGNORECASE):
                untranslatable.append(
                    f"{sql_file.name}: CONNECT BY (hierarchical query) -> use WITH RECURSIVE CTE"
                )
                translated_content = re.sub(
                    r'\bCONNECT\s+BY\b',
                    '-- TODO: CONNECT BY requires manual rewrite to WITH RECURSIVE CTE\n-- CONNECT BY',
                    translated_content, flags=re.IGNORECASE
                )
            if re.search(r'\bCREATE\s+(OR\s+REPLACE\s+)?PACKAGE\b', translated_content, re.IGNORECASE):
                untranslatable.append(
                    f"{sql_file.name}: PACKAGE -> split into schema + individual functions"
                )
                translated_content = re.sub(
                    r'\bCREATE\s+(OR\s+REPLACE\s+)?PACKAGE\b',
                    '-- TODO: PACKAGEs have no PostgreSQL equivalent; split into schema + functions\n-- CREATE PACKAGE',
                    translated_content, flags=re.IGNORECASE
                )
            if re.search(r'\bPRAGMA\b', translated_content, re.IGNORECASE):
                untranslatable.append(
                    f"{sql_file.name}: PRAGMA directives have no PostgreSQL equivalent"
                )

        # Apply MSSQL T-SQL -> PL/pgSQL conversions
        if src_key == "mssql":
            # Remove SET NOCOUNT ON
            translated_content = re.sub(
                r'\bSET\s+NOCOUNT\s+ON\s*;?',
                '-- SET NOCOUNT ON removed (not needed in PostgreSQL)',
                translated_content, flags=re.IGNORECASE
            )
            # GO -> ; (statement separator)
            translated_content = re.sub(
                r'^\s*GO\s*$',
                ';',
                translated_content, flags=re.MULTILINE | re.IGNORECASE
            )
            # DECLARE @var TYPE = value -> DECLARE var TYPE := value
            translated_content = re.sub(
                r'\bDECLARE\s+@(\w+)\s+(\w+(?:\([^)]*\))?)\s*=\s*',
                r'DECLARE \1 \2 := ',
                translated_content, flags=re.IGNORECASE
            )
            # DECLARE @var TYPE (without initial value)
            translated_content = re.sub(
                r'\bDECLARE\s+@(\w+)\s+',
                r'DECLARE \1 ',
                translated_content, flags=re.IGNORECASE
            )
            # @variable -> variable (remove @ prefix)
            translated_content = re.sub(
                r'@(\w+)',
                r'\1',
                translated_content
            )
            # PRINT 'message' -> RAISE NOTICE 'message'
            translated_content = re.sub(
                r'\bPRINT\s+',
                'RAISE NOTICE ',
                translated_content, flags=re.IGNORECASE
            )
            # TOP N -> needs rewrite to LIMIT
            top_matches = re.findall(r'\bSELECT\s+TOP\s+(\d+)\b', translated_content, re.IGNORECASE)
            if top_matches:
                for n in top_matches:
                    translated_content = re.sub(
                        r'\bSELECT\s+TOP\s+' + n + r'\b',
                        f'SELECT /* TODO: add LIMIT {n} at end of query */',
                        translated_content, flags=re.IGNORECASE, count=1
                    )
                untranslatable.append(
                    f"{sql_file.name}: SELECT TOP N -> requires LIMIT at end of query (manual placement)"
                )

            # Detect untranslatable constructs
            if re.search(r'\bEXEC(?:UTE)?\s+sp_', translated_content, re.IGNORECASE):
                untranslatable.append(
                    f"{sql_file.name}: System stored procedures (sp_*) need PostgreSQL equivalents"
                )
            if re.search(r'\bOPENJSON\b', translated_content, re.IGNORECASE):
                untranslatable.append(
                    f"{sql_file.name}: OPENJSON -> use json_to_recordset or json_array_elements"
                )

        # Apply generic function translations
        translated_content = translate_functions(app_id, target_db, translated_content)

        translated_blocks.append(translated_content)
        translated_blocks.append("")

    translated_blocks.append(SQL_CUI_FOOTER)

    out_path = Path(output_dir)
    out_path.mkdir(parents=True, exist_ok=True)
    file_path = out_path / "stored_procedures.sql"
    file_path.write_text("\n".join(translated_blocks), encoding="utf-8")
    print(f"Stored procedure translation written to {file_path}")

    if untranslatable:
        print(f"  {len(untranslatable)} construct(s) need manual review:")
        for item in untranslatable:
            print(f"    - {item}")

    return (str(file_path), untranslatable)


# ---------------------------------------------------------------------------
# Migration validation queries
# ---------------------------------------------------------------------------

def generate_migration_validation(app_id: str, output_dir: str) -> str:
    """Generate comprehensive validation queries for post-migration checks.

    For each table produces:
    - Row count comparison between source and target
    - Checksum (hash of primary key columns) comparison
    - NULL count comparison per nullable column
    - MIN/MAX value checks for numeric and date columns

    Returns the path to the generated SQL file.
    """
    conn = _get_db()
    try:
        rows = conn.execute(
            """SELECT * FROM legacy_db_schemas
               WHERE legacy_app_id = ?
               ORDER BY schema_name, table_name, column_name""",
            (app_id,)
        ).fetchall()
    finally:
        conn.close()

    if not rows:
        print(f"WARNING: No schema data found for app_id={app_id}", file=sys.stderr)
        return ""

    # Group by table
    tables = OrderedDict()
    for row in rows:
        key = (row["schema_name"] or "public", row["table_name"])
        if key not in tables:
            tables[key] = []
        tables[key].append(dict(row))

    lines = []
    lines.append(SQL_CUI_HEADER)
    lines.append("-- Migration Validation Queries")
    lines.append(f"-- Application ID: {app_id}")
    lines.append(f"-- Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append("-- Run these queries against BOTH source and target databases")
    lines.append("-- and compare results to verify migration correctness.")
    lines.append(f"-- {'=' * 68}")
    lines.append("")

    for (schema, table), columns in tables.items():
        qualified = f"{schema}.{table}" if schema and schema != "public" else table

        lines.append(f"-- {'=' * 50}")
        lines.append(f"-- Validation: {qualified}")
        lines.append(f"-- {'=' * 50}")
        lines.append("")

        # 1. Row count comparison
        lines.append("-- Row count")
        lines.append(f"SELECT '{qualified}' AS table_name, COUNT(*) AS row_count FROM {qualified};")
        lines.append("")

        # 2. Checksum on primary key columns
        pk_cols = [c for c in columns if c["is_primary_key"]]
        if pk_cols:
            pk_col_list = ", ".join(c["column_name"] for c in pk_cols)
            pk_concat = " || '|' || ".join(
                f"COALESCE(CAST({c['column_name']} AS TEXT), 'NULL')" for c in pk_cols
            )
            lines.append("-- Primary key checksum")
            lines.append(
                f"SELECT '{qualified}' AS table_name, "
                f"COUNT(DISTINCT ({pk_concat})) AS distinct_pk_count "
                f"FROM {qualified};"
            )
            lines.append("")

        # 3. NULL count comparison per nullable column
        nullable_cols = [c for c in columns if c["is_nullable"]]
        if nullable_cols:
            lines.append("-- NULL counts per column")
            null_selects = []
            for col in nullable_cols:
                null_selects.append(
                    f"SUM(CASE WHEN {col['column_name']} IS NULL THEN 1 ELSE 0 END) "
                    f"AS {col['column_name']}_nulls"
                )
            lines.append(
                f"SELECT '{qualified}' AS table_name,\n"
                f"    {(',{}'.format(chr(10)) + '    ').join(null_selects)}\n"
                f"FROM {qualified};"
            )
            lines.append("")

        # 4. MIN/MAX checks for numeric and date columns
        numeric_types = {
            "NUMBER", "NUMERIC", "DECIMAL", "INTEGER", "INT", "BIGINT",
            "SMALLINT", "FLOAT", "REAL", "DOUBLE", "MONEY", "TINYINT",
            "BINARY_FLOAT", "BINARY_DOUBLE",
        }
        date_types = {"DATE", "TIMESTAMP", "DATETIME", "TIMESTAMPTZ", "TIME"}

        minmax_cols = []
        for col in columns:
            base = re.match(r'^([A-Za-z_]+)', col["data_type"].upper())
            base_type = base.group(1) if base else col["data_type"].upper()
            if base_type in numeric_types or base_type in date_types:
                minmax_cols.append(col)

        if minmax_cols:
            lines.append("-- MIN/MAX value checks")
            mm_selects = []
            for col in minmax_cols:
                mm_selects.append(f"MIN({col['column_name']}) AS {col['column_name']}_min")
                mm_selects.append(f"MAX({col['column_name']}) AS {col['column_name']}_max")
            lines.append(
                f"SELECT '{qualified}' AS table_name,\n"
                f"    {(',{}'.format(chr(10)) + '    ').join(mm_selects)}\n"
                f"FROM {qualified};"
            )
            lines.append("")

    lines.append(SQL_CUI_FOOTER)

    out_path = Path(output_dir)
    out_path.mkdir(parents=True, exist_ok=True)
    file_path = out_path / "validation_queries.sql"
    file_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"Validation queries written to {file_path}")
    return str(file_path)


# ---------------------------------------------------------------------------
# Data volume estimation
# ---------------------------------------------------------------------------

def estimate_data_volume(app_id: str) -> dict:
    """Estimate data volume based on schema column types.

    For each table estimates a per-row byte size based on column types and
    returns a summary dict. This is a schema-based estimate only — actual row
    counts are not known at planning time.

    Returns:
        {
            "app_id": str,
            "tables": [
                {"name": str, "schema": str, "estimated_row_bytes": int,
                 "column_count": int}
            ],
            "total_estimated_bytes_per_row": int,
            "table_count": int
        }
    """
    conn = _get_db()
    try:
        rows = conn.execute(
            """SELECT * FROM legacy_db_schemas
               WHERE legacy_app_id = ?
               ORDER BY schema_name, table_name, column_name""",
            (app_id,)
        ).fetchall()
    finally:
        conn.close()

    if not rows:
        return {
            "app_id": app_id,
            "tables": [],
            "total_estimated_bytes_per_row": 0,
            "table_count": 0,
        }

    # Group by table
    tables_map = OrderedDict()
    for row in rows:
        key = (row["schema_name"] or "public", row["table_name"])
        if key not in tables_map:
            tables_map[key] = []
        tables_map[key].append(dict(row))

    table_estimates = []
    total_bytes = 0

    for (schema, table), columns in tables_map.items():
        row_bytes = 0
        for col in columns:
            dtype_upper = col["data_type"].upper().strip()

            # Extract base type
            base_match = re.match(r'^([A-Za-z_][A-Za-z0-9_ ]*)', dtype_upper)
            base_type = base_match.group(1).strip() if base_match else dtype_upper

            # Check for parametric length — e.g. VARCHAR(255) -> use 255
            param_match = re.match(r'.*\(\s*(\d+)', dtype_upper)
            if param_match and base_type in ("VARCHAR", "VARCHAR2", "NVARCHAR",
                                              "NVARCHAR2", "CHAR", "NCHAR"):
                # Use declared length as average estimate (halved for VARCHAR)
                declared = int(param_match.group(1))
                row_bytes += max(declared // 2, 10)
            elif base_type in TYPE_SIZE_ESTIMATES:
                row_bytes += TYPE_SIZE_ESTIMATES[base_type]
            else:
                # Unknown type — assume 50 bytes
                row_bytes += 50

        # Add row overhead (tuple header, alignment)
        row_bytes += 24

        table_estimates.append({
            "name": table,
            "schema": schema,
            "estimated_row_bytes": row_bytes,
            "column_count": len(columns),
        })
        total_bytes += row_bytes

    return {
        "app_id": app_id,
        "tables": table_estimates,
        "total_estimated_bytes_per_row": total_bytes,
        "table_count": len(table_estimates),
    }


# ---------------------------------------------------------------------------
# Full migration orchestration
# ---------------------------------------------------------------------------

def generate_full_migration(app_id: str, target_db: str,
                            source_path: str, output_dir: str) -> dict:
    """Orchestrate all migration artifact generation.

    Creates an output subdirectory and calls each generation function:
    - generate_schema_ddl
    - generate_data_migration_scripts
    - translate_stored_procedures (if source_path has .sql files)
    - generate_migration_validation
    - estimate_data_volume
    - Generates a migration_index.md linking all files

    Returns a summary dict.
    """
    migration_dir = Path(output_dir) / "db_migration"
    migration_dir.mkdir(parents=True, exist_ok=True)

    summary = {
        "app_id": app_id,
        "target_db": target_db,
        "output_dir": str(migration_dir),
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "artifacts": {},
        "untranslatable_constructs": [],
    }

    # Schema DDL
    ddl_path = generate_schema_ddl(app_id, target_db, str(migration_dir))
    if ddl_path:
        summary["artifacts"]["schema_ddl"] = ddl_path

    # Data migration
    data_path = generate_data_migration_scripts(app_id, target_db, str(migration_dir))
    if data_path:
        summary["artifacts"]["data_migration"] = data_path

    # Stored procedures
    proc_path = ""
    untranslatable = []
    if source_path:
        src = Path(source_path)
        has_sql = False
        if src.is_file() and src.suffix.lower() == ".sql":
            has_sql = True
        elif src.is_dir():
            for _root, _dirs, files in os.walk(str(src)):
                if any(f.lower().endswith(".sql") for f in files):
                    has_sql = True
                    break
        if has_sql:
            proc_path, untranslatable = translate_stored_procedures(
                app_id, target_db, source_path, str(migration_dir)
            )
    if proc_path:
        summary["artifacts"]["stored_procedures"] = proc_path
    if untranslatable:
        summary["untranslatable_constructs"] = untranslatable

    # Validation
    val_path = generate_migration_validation(app_id, str(migration_dir))
    if val_path:
        summary["artifacts"]["validation_queries"] = val_path

    # Volume estimation
    volume = estimate_data_volume(app_id)
    summary["volume_estimate"] = volume

    # Generate index document
    index_path = _generate_migration_index(summary, str(migration_dir))
    summary["artifacts"]["migration_index"] = index_path

    return summary


def _generate_migration_index(summary: dict, output_dir: str) -> str:
    """Generate a Markdown index document linking all migration artifacts."""
    lines = []
    lines.append(MD_CUI_HEADER)
    lines.append("")
    lines.append("# Database Migration Plan")
    lines.append("")
    lines.append(f"**Application ID:** {summary['app_id']}")
    lines.append(f"**Target Database:** {summary['target_db']}")
    lines.append(f"**Generated:** {summary['generated_at']}")
    lines.append("")
    lines.append("## Generated Artifacts")
    lines.append("")
    lines.append("| Artifact | File | Description |")
    lines.append("|----------|------|-------------|")

    artifact_desc = {
        "schema_ddl": "CREATE TABLE DDL statements for target database",
        "data_migration": "INSERT/SELECT migration SQL with type casts and function translations",
        "stored_procedures": "Translated stored procedures and functions",
        "validation_queries": "Post-migration validation queries (row counts, checksums, MIN/MAX)",
        "migration_index": "This index document",
    }

    for key, path in summary.get("artifacts", {}).items():
        if key == "migration_index":
            continue
        desc = artifact_desc.get(key, key)
        filename = Path(path).name
        lines.append(f"| {key} | `{filename}` | {desc} |")

    lines.append("")

    # Volume estimates
    volume = summary.get("volume_estimate", {})
    if volume and volume.get("tables"):
        lines.append("## Data Volume Estimates (per row)")
        lines.append("")
        lines.append("| Table | Schema | Columns | Est. Row Size (bytes) |")
        lines.append("|-------|--------|---------|----------------------|")
        for t in volume["tables"]:
            lines.append(
                f"| {t['name']} | {t['schema']} | {t['column_count']} | "
                f"{t['estimated_row_bytes']:,} |"
            )
        lines.append("")
        lines.append(
            f"**Total tables:** {volume['table_count']}  "
        )
        lines.append(
            f"**Combined est. row bytes:** {volume['total_estimated_bytes_per_row']:,}"
        )
        lines.append("")

    # Untranslatable constructs
    constructs = summary.get("untranslatable_constructs", [])
    if constructs:
        lines.append("## Items Requiring Manual Review")
        lines.append("")
        for item in constructs:
            lines.append(f"- {item}")
        lines.append("")

    lines.append("## DBA Review Checklist")
    lines.append("")
    lines.append("- [ ] Review schema_ddl.sql for correct data type mappings")
    lines.append("- [ ] Verify PRIMARY KEY and FOREIGN KEY constraints")
    lines.append("- [ ] Review data_migration.sql for correct type casts")
    lines.append("- [ ] Check stored_procedures.sql for TODO comments")
    lines.append("- [ ] Run validation_queries.sql after migration")
    lines.append("- [ ] Verify row counts match between source and target")
    lines.append("- [ ] Test application functionality against migrated database")
    lines.append("- [ ] Verify CUI markings on all generated artifacts")
    lines.append("")
    lines.append(MD_CUI_FOOTER)

    out_path = Path(output_dir)
    file_path = out_path / "migration_index.md"
    file_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"Migration index written to {file_path}")
    return str(file_path)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    """CLI entry point for database migration planning."""
    parser = argparse.ArgumentParser(
        description="Database Migration Planner — generate DDL, data migration SQL, "
                    "stored procedure translations, and validation queries for "
                    "legacy database modernization.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""\
            Examples:
              # Generate all migration artifacts
              python tools/modernization/db_migration_planner.py \\
                  --app-id APP-001 --output-dir /tmp/migration --type all

              # Generate only schema DDL
              python tools/modernization/db_migration_planner.py \\
                  --app-id APP-001 --output-dir /tmp/migration --type schema

              # Translate stored procedures
              python tools/modernization/db_migration_planner.py \\
                  --app-id APP-001 --output-dir /tmp/migration \\
                  --type procedures --source-path /opt/legacy/sql

              # JSON output for pipeline integration
              python tools/modernization/db_migration_planner.py \\
                  --app-id APP-001 --output-dir /tmp/migration --type all --json
        """),
    )

    parser.add_argument(
        "--app-id", required=True,
        help="Legacy application ID from legacy_applications table"
    )
    parser.add_argument(
        "--target", default="postgresql",
        choices=["postgresql", "mysql", "aurora"],
        help="Target database platform (default: postgresql)"
    )
    parser.add_argument(
        "--source-path", default=None,
        help="Path to directory containing stored procedure .sql files"
    )
    parser.add_argument(
        "--output-dir", required=True,
        help="Directory to write generated migration artifacts"
    )
    parser.add_argument(
        "--type", default="all", dest="gen_type",
        choices=["schema", "data", "procedures", "validation", "all"],
        help="Type of artifacts to generate (default: all)"
    )
    parser.add_argument(
        "--json", action="store_true", dest="json_output",
        help="Output results as JSON to stdout"
    )

    args = parser.parse_args()

    # Validate app exists
    conn = _get_db()
    try:
        app = conn.execute(
            "SELECT * FROM legacy_applications WHERE id = ?", (args.app_id,)
        ).fetchone()
        if not app:
            print(f"ERROR: Application '{args.app_id}' not found in legacy_applications.",
                  file=sys.stderr)
            sys.exit(1)
    finally:
        conn.close()

    # Map aurora -> postgresql for type mappings
    target_db = "postgresql" if args.target == "aurora" else args.target

    result = {}

    if args.gen_type == "all":
        result = generate_full_migration(
            app_id=args.app_id,
            target_db=target_db,
            source_path=args.source_path or "",
            output_dir=args.output_dir,
        )

    elif args.gen_type == "schema":
        path = generate_schema_ddl(args.app_id, target_db, args.output_dir)
        result = {"artifact": "schema_ddl", "path": path}

    elif args.gen_type == "data":
        path = generate_data_migration_scripts(args.app_id, target_db, args.output_dir)
        result = {"artifact": "data_migration", "path": path}

    elif args.gen_type == "procedures":
        if not args.source_path:
            print("ERROR: --source-path is required for --type procedures",
                  file=sys.stderr)
            sys.exit(1)
        path, untranslatable = translate_stored_procedures(
            args.app_id, target_db, args.source_path, args.output_dir
        )
        result = {
            "artifact": "stored_procedures",
            "path": path,
            "untranslatable_constructs": untranslatable,
        }

    elif args.gen_type == "validation":
        path = generate_migration_validation(args.app_id, args.output_dir)
        result = {"artifact": "validation_queries", "path": path}

    # Output
    if args.json_output:
        print(json.dumps(result, indent=2, default=str))
    else:
        if args.gen_type == "all":
            print("\nMigration plan generated successfully.")
            print(f"  Application: {args.app_id}")
            print(f"  Target DB:   {target_db}")
            print(f"  Output:      {result.get('output_dir', args.output_dir)}")
            print(f"  Artifacts:   {len(result.get('artifacts', {}))}")
            vol = result.get("volume_estimate", {})
            if vol and vol.get("table_count"):
                print(f"  Tables:      {vol['table_count']}")
            constructs = result.get("untranslatable_constructs", [])
            if constructs:
                print(f"  Manual review items: {len(constructs)}")
        else:
            path = result.get("path", "")
            if path:
                print(f"\nArtifact generated: {path}")
            else:
                print("\nNo output generated — check warnings above.")


if __name__ == "__main__":
    main()
# CUI // SP-CTI

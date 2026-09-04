#!/usr/bin/env python3
# CUI // SP-CTI
"""A budget module is DECLARED in config, so the CHECK may not enumerate two (cch-bud-01).

`module_budget_periods` and `module_budget_usage` both carried

    CHECK(module_name IN ('generative_intelligence', 'predictive_analysis'))

which was correct while those two names were a Python constant. They are now declared in
`args/llm_config.yaml::module_budgets.per_module`, so an enumerating CHECK would need a
migration every time somebody declares a module -- and until that migration ran, the
declared module would fail at INSERT with a CheckViolation rather than simply not being
enforced.

Measured 2026-08-28: declaring `research_knowledge` produced

    psycopg2.errors.CheckViolation: new row for relation "module_budget_periods"
    violates check constraint "module_budget_periods_module_name_check"

WHY NOT WIDEN THE ENUMERATION. Because the set is no longer knowable at migration time.
`check_module_budget` already ALLOWS an unrecognised module rather than blocking on it
(`if module_name not in declared_modules(): return allow`), so an undeclared name is a
no-op, not an unenforced spend -- the failure mode the enumeration was guarding against
does not exist. The remaining invariant worth holding in SQL is that the column is not
blank, and that is what replaces it.

SQLite has no DROP CONSTRAINT and its CHECK is baked into the CREATE TABLE. A table there
was created by the (already updated) DDL in module_budget_tracker.py, and rewriting a
table to alter a CHECK risks the data for a constraint that PostgreSQL is the primary
backend for -- so the SQLite branch verifies and reports rather than rebuilding.
"""

DESCRIPTION = "module budget CHECK: declared modules, not two hardcoded names"

TABLES = ("module_budget_periods", "module_budget_usage")


def _is_pg(conn) -> bool:
    return getattr(conn, "_backend", "sqlite") == "postgresql"


def up(conn):
    if not _is_pg(conn):
        # Nothing to do: SQLite tables are created by CREATE TABLE IF NOT EXISTS from the
        # updated DDL. An EXISTING SQLite table keeps its old CHECK; that is reported by
        # the tracker at write time rather than silently rebuilt here.
        return

    for table in TABLES:
        exists = conn.execute(
            "SELECT 1 FROM information_schema.tables "
            "WHERE table_schema = 'public' AND table_name = %s",
            (table,),
        ).fetchone()
        if not exists:
            continue

        # Drop every CHECK on this table that constrains module_name by enumeration. The
        # constraint NAME is the PostgreSQL default here, but a table created by an older
        # path may carry a different one -- so find them rather than assuming.
        rows = conn.execute(
            """
            SELECT con.conname AS name, pg_get_constraintdef(con.oid) AS def
            FROM pg_constraint con
            JOIN pg_class rel ON rel.oid = con.conrelid
            JOIN pg_namespace nsp ON nsp.oid = rel.relnamespace
            WHERE nsp.nspname = 'public' AND rel.relname = %s AND con.contype = 'c'
            """,
            (table,),
        ).fetchall()
        for row in rows:
            d = dict(row) if not isinstance(row, dict) else row
            definition = (d.get("def") or "")
            # PostgreSQL renders `IN (...)` as `= ANY (ARRAY[...])` in
            # pg_get_constraintdef, so matching on " IN " finds NOTHING and the drop
            # silently does nothing -- which is exactly what the first run of this
            # migration did. Match both spellings.
            up = definition.upper()
            if "MODULE_NAME" not in up or ("ANY (ARRAY" not in up and " IN (" not in up):
                continue
            conn.execute(f'ALTER TABLE {table} DROP CONSTRAINT "{d["name"]}"')

        # The invariant that survives: a budget row must name SOMETHING.
        already = conn.execute(
            """
            SELECT 1 FROM pg_constraint con
            JOIN pg_class rel ON rel.oid = con.conrelid
            JOIN pg_namespace nsp ON nsp.oid = rel.relnamespace
            WHERE nsp.nspname = 'public' AND rel.relname = %s
              AND con.conname = %s
            """,
            (table, f"{table}_module_name_not_blank"),
        ).fetchone()
        if not already:
            conn.execute(
                f'ALTER TABLE {table} ADD CONSTRAINT "{table}_module_name_not_blank" '
                f"CHECK (module_name <> '')"
            )


def down(conn):
    """Deliberately NOT a restore of the enumeration.

    Re-adding `CHECK(module_name IN ('generative_intelligence','predictive_analysis'))`
    would fail against any row a declared module has since written, turning a rollback
    into a broken table. Dropping the not-blank check alone would loosen the schema for
    no benefit, so this is a no-op.
    """
    return

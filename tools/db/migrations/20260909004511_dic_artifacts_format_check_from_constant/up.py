#!/usr/bin/env python3
# CUI // SP-CTI
"""dwr-word-01 — rebuild the CHECK on ``dic_artifacts.format`` from
``exporter.EXPORT_FORMATS``, which now names ``docx_tracked``.

Migration 20260903194350 declared the column with an INLINE four-name CHECK
(``format IN ('md','html','docx','pdf')``) rendered from the constant AT THAT
TIME. Adding a fifth format makes that constraint the stale copy: a
``docx_tracked`` export would render the file, pass every gate, and then be
REFUSED by the database on the ``dic_artifacts`` INSERT — an artifact on disk
that the record does not know about, which is the exact failure mode the row
exists to prevent.

The constraint body is GENERATED from the constant, following migration
20260903100336 (``dic_documents.template_type``): a ``.sql`` file spelling the
five names out would be the next stale copy, and there is no third door.
Adding a format is now: append to ``EXPORT_FORMATS``, scaffold a migration that
calls :func:`rebuild_format_constraint`.

BOTH BACKENDS ARE HANDLED, and the SQLite half is why this is not a two-line
migration. Unlike the template_type precedent — which could leave SQLite alone
because the constraint had never landed there — this one HAS landed wherever
20260903194350 ran, so a SQLite deployment would refuse every tracked export
for the life of the database. SQLite cannot ALTER a CHECK, so the table is
rebuilt (create, copy, drop, rename) and ONLY when the live DDL is measurably
stale: the rebuild is skipped for a table carrying no CHECK at all (which is
what ``exporter._ensure_schema`` creates, and what a test database has) and for
one already naming every current format. A rebuild nothing needs is still a
drop and a copy of somebody's data.

The new DDL is derived from ``exporter._SCHEMA`` with the CHECK injected, so
the columns cannot drift from the runtime CREATE — the property
``tests/document_intelligence/test_export.py::
test_migration_and_runtime_ddl_declare_the_same_columns`` already pins for the
original migration holds here by construction rather than by a second copy.
"""

CONSTRAINT = "dic_artifacts_format_check"
TABLE = "dic_artifacts"


def format_check_sql() -> str:
    """The CHECK clause, derived from the constant — never respelled."""
    from tools.document_intelligence.exporter import EXPORT_FORMATS

    quoted = ", ".join("'" + f.replace("'", "''") + "'" for f in EXPORT_FORMATS)
    return f"CHECK (format IN ({quoted}))"


def _sqlite_ddl() -> str:
    """``exporter._SCHEMA`` with the derived CHECK on ``format``.

    One declaration of the columns; only the constraint is added here.
    """
    from tools.document_intelligence.exporter import _SCHEMA

    old = "format           TEXT NOT NULL,"
    new = f"format           TEXT NOT NULL {format_check_sql()},"
    if old not in _SCHEMA:  # pragma: no cover - pinned by test
        raise RuntimeError(
            "exporter._SCHEMA no longer declares `format` the way this "
            "migration expects; re-derive the DDL rather than guessing")
    return (_SCHEMA.replace(old, new)
            .replace("CREATE TABLE IF NOT EXISTS dic_artifacts",
                     "CREATE TABLE dic_artifacts__new"))


def _sqlite_needs_rebuild(conn) -> tuple[bool, str]:
    """(needed, why). A table with NO CHECK accepts every format already."""
    from tools.document_intelligence.exporter import EXPORT_FORMATS

    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (TABLE,)
    ).fetchone()
    if not row or not row[0]:
        return False, "table absent"
    ddl = row[0]
    if "CHECK" not in ddl.upper():
        return False, "no CHECK on the live table"
    missing = [f for f in EXPORT_FORMATS if f"'{f}'" not in ddl]
    if not missing:
        return False, "CHECK already names every format"
    return True, f"CHECK omits {', '.join(missing)}"


def _column_names(conn) -> list[str]:
    return [r[1] for r in conn.execute(f"PRAGMA table_info({TABLE})").fetchall()]


def rebuild_format_constraint(conn) -> dict:
    """Make the live CHECK agree with ``EXPORT_FORMATS`` on either backend.

    The connection is caller-owned: migrations run inside a larger transaction
    and closing it here would break the rest of their run.
    """
    if getattr(conn, "_backend", "") == "postgresql":
        conn.execute(
            f"ALTER TABLE {TABLE} DROP CONSTRAINT IF EXISTS {CONSTRAINT}")
        conn.execute(
            f"ALTER TABLE {TABLE} ADD CONSTRAINT {CONSTRAINT} {format_check_sql()}")
        conn.commit()
        return {"backend": "postgresql", "rebuilt": True, "reason": "constraint replaced"}

    needed, why = _sqlite_needs_rebuild(conn)
    if not needed:
        return {"backend": "sqlite", "rebuilt": False, "reason": why}
    cols = ", ".join(_column_names(conn))
    conn.execute("DROP TABLE IF EXISTS dic_artifacts__new")
    conn.execute(_sqlite_ddl())
    conn.execute(f"INSERT INTO dic_artifacts__new ({cols}) SELECT {cols} FROM {TABLE}")
    conn.execute(f"DROP TABLE {TABLE}")
    conn.execute(f"ALTER TABLE dic_artifacts__new RENAME TO {TABLE}")
    conn.commit()
    return {"backend": "sqlite", "rebuilt": True, "reason": why}


def up(conn):
    result = rebuild_format_constraint(conn)
    print(f"  dic_artifacts.format CHECK: {result['backend']} — "
          f"rebuilt={result['rebuilt']} ({result['reason']})")
    return result

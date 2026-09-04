#!/usr/bin/env python3
# CUI // SP-CTI
"""rmf-wp-01 — rebuild the CHECK on ``dic_documents.template_type`` from
``constants.TEMPLATE_TYPES``, which now names WHITEPAPER.

Migration 230 declared the column with an INLINE six-name CHECK. On the live
PostgreSQL board (measured 2026-09-03) that constraint is NOT present — the
table carries only its primary key, because ``translate_sql`` rewrites
``ADD COLUMN`` to ``ADD COLUMN IF NOT EXISTS`` and the column already existed
there, so the CHECK never landed. This migration therefore ADDS a named
constraint on PG rather than widening one; the 55 existing rows (54 NULL,
1 ARCH_NETWORK) all satisfy it.

The constraint body is GENERATED from the constant. A ``.sql`` file spelling
the type names out would be the next stale copy — exactly the drift that left
``template_id`` accepted and ignored for months. Adding a template type is
now: append to ``TEMPLATE_TYPES``, scaffold a migration that calls
:func:`rebuild_template_type_constraint`.

SQLite is a no-op by design, and that is a REAL limitation stated rather than
hidden: SQLite cannot ALTER a CHECK, so a SQLite database that ran migration
230 keeps the six-name inline constraint and refuses a WHITEPAPER row on
INSERT. Rebuilding ``dic_documents`` there (copy, drop, rename) is too blunt to
run unconditionally against a database this migration cannot inspect, which is
the same call the zta_maturity and audit_trail migrations made. A fresh SQLite
database is unaffected only insofar as it never ran 230.
"""

CONSTRAINT = "dic_documents_template_type_check"


def template_type_check_sql() -> str:
    """The CHECK clause, derived from the constant — never respelled."""
    from tools.document_intelligence.constants import TEMPLATE_TYPES

    quoted = ", ".join("'" + t.replace("'", "''") + "'" for t in TEMPLATE_TYPES)
    return f"CHECK (template_type IS NULL OR template_type IN ({quoted}))"


def rebuild_template_type_constraint(conn) -> bool:
    """Drop-then-add the named constraint on PG. False on SQLite (see module doc).

    The connection is caller-owned: migrations run inside a larger transaction
    and closing it here would break the rest of their run.
    """
    if getattr(conn, "_backend", "") != "postgresql":
        return False
    conn.execute(f"ALTER TABLE dic_documents DROP CONSTRAINT IF EXISTS {CONSTRAINT}")
    conn.execute(
        f"ALTER TABLE dic_documents ADD CONSTRAINT {CONSTRAINT} {template_type_check_sql()}"
    )
    conn.commit()
    return True


def up(conn):
    rebuilt = rebuild_template_type_constraint(conn)
    if not rebuilt:
        print(
            "  dic_documents.template_type CHECK left as-is (SQLite cannot ALTER a "
            "CHECK); a database that ran migration 230 still carries the six-name "
            "inline constraint there."
        )
    return {"constraint_rebuilt": rebuilt}

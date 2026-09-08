# CUI // SP-CTI
"""Test fixtures that GUARANTEE a table's shape instead of assuming it.

THE DEFECT THIS EXISTS FOR, reproduced deterministically::

    an earlier module in the process leaves `dic_documents` WITHOUT `status`
    -> the fixture's `CREATE TABLE IF NOT EXISTS dic_documents (... status ...)`
       silently NO-OPS, because IF NOT EXISTS means "if a table by this name
       exists, keep whatever shape it has"
    -> `INSERT INTO dic_documents (... status ...)` raises
       sqlite3.OperationalError: table dic_documents has no column named status

CLAUDE.md already states the rule for production code — "``CREATE TABLE IF NOT
EXISTS`` never alters an existing table, so a table created by an older
migration keeps its old columns while the DDL moves on" — and
``check_insert_schema_parity`` enforces it there. Nothing was checking it in
TEST fixtures, where the same statement is the standard way to set a table up.

WHY IT IS SO HARD TO SEE. The fixture is correct in isolation and stays correct
until some *other* module happens to create the same table first, in the same
process, with a narrower shape. So it passes locally, passes alone, passes in
its own directory, and fails on one CI shard. It broke PR #2167 exactly that
way; the same file passes in file order on a developer machine. And it is not
stable debt: crx-test-07 bin-packs shards by measured duration, so adding two
test files anywhere moved ~50 of 442 files between shards — which means the
precondition appears and disappears with edits that have nothing to do with the
test.

THE FIX IS THE PRODUCTION IDIOM. ``ingest_orchestrator._ensure_schema`` creates
its tables and then ALTERs in the columns an older database is missing.
:func:`ensure_table` does the same for a fixture: create if absent, then add
whatever the declaration names and the live table lacks.

    from tests._schema_compat import ensure_table

    ensure_table(conn, '''CREATE TABLE IF NOT EXISTS dic_documents (
        doc_id TEXT PRIMARY KEY, status TEXT, origin TEXT)''')

DROP-then-CREATE was considered and rejected: a fixture that drops a shared
table destroys whatever another module in the same process is relying on, which
trades a loud failure here for a mysterious one somewhere else. Adding a column
is additive and cannot break a reader that never asked for it.

SQLite only, deliberately. ``ALTER TABLE ... ADD COLUMN`` is the one DDL verb
SQLite and PostgreSQL spell the same way, but the gated suite runs on SQLite
(``tests/conftest.py`` forces ``ICDEV_STORAGE_BACKEND=sqlite``) and a fixture
helper that quietly reshapes a live PostgreSQL table is a much worse idea than
one that refuses to.
"""
from __future__ import annotations

import re
from typing import Iterable

__all__ = ["ensure_table", "declared_columns", "existing_columns"]

_CREATE_RE = re.compile(
    r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?[\"'`]?(\w+)[\"'`]?\s*\((.*)\)\s*\Z",
    re.S | re.I,
)

#: Leading words that begin a table CONSTRAINT rather than a column definition.
_CONSTRAINT_WORDS = frozenset(
    {"primary", "foreign", "unique", "check", "constraint", "index", "key"}
)


def _split_top_level(body: str) -> list[str]:
    """Split on commas that are NOT inside parentheses.

    ``status TEXT CHECK (status IN ('a','b'))`` contains two commas that do not
    separate columns; splitting naively invents columns named ``'b'``.
    """
    parts: list[str] = []
    depth = 0
    cur = ""
    for ch in body:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == "," and depth == 0:
            parts.append(cur)
            cur = ""
        else:
            cur += ch
    parts.append(cur)
    return parts


def declared_columns(ddl: str) -> tuple[str, list[tuple[str, str]]]:
    """``(table, [(column, type_and_modifiers), ...])`` parsed from a CREATE TABLE.

    Raises ``ValueError`` on something that is not a CREATE TABLE, rather than
    returning an empty list — a helper that silently does nothing when handed
    the wrong string is how a fixture ends up guaranteeing nothing at all.
    """
    m = _CREATE_RE.search(ddl.strip())
    if not m:
        raise ValueError("not a CREATE TABLE statement")
    table = m.group(1)
    cols: list[tuple[str, str]] = []
    for part in _split_top_level(m.group(2)):
        part = part.strip()
        if not part:
            continue
        first = part.split()[0].strip("\"`'").lower()
        if first in _CONSTRAINT_WORDS:
            continue
        bits = part.split(None, 1)
        name = bits[0].strip("\"`'")
        rest = bits[1].strip() if len(bits) > 1 else "TEXT"
        cols.append((name, rest))
    return table, cols


def existing_columns(conn, table: str) -> set[str]:
    """Columns the live table actually has. Empty set when it does not exist."""
    try:
        rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    except Exception:
        return set()
    out: set[str] = set()
    for r in rows:
        try:
            d = dict(r)
            out.add(d["name"])
        except Exception:
            out.add(r[1])
    return out


def _addable(definition: str) -> str:
    """Strip modifiers SQLite refuses in ADD COLUMN.

    SQLite cannot ADD a PRIMARY KEY or a UNIQUE column, and cannot add a NOT
    NULL column without a default. A fixture only needs the column to EXIST, so
    the constraint is dropped rather than the whole repair being abandoned —
    the alternative is the fixture silently keeping the old shape, which is the
    defect this module exists to remove.
    """
    out = definition
    out = re.sub(r"\bPRIMARY\s+KEY\b", "", out, flags=re.I)
    out = re.sub(r"\bUNIQUE\b", "", out, flags=re.I)
    out = re.sub(r"\bAUTOINCREMENT\b", "", out, flags=re.I)
    if re.search(r"\bNOT\s+NULL\b", out, re.I) and not re.search(r"\bDEFAULT\b", out, re.I):
        out = re.sub(r"\bNOT\s+NULL\b", "", out, flags=re.I)
    return " ".join(out.split()) or "TEXT"


def ensure_table(conn, ddl: str) -> list[str]:
    """Create *ddl*'s table, then ADD any column the live table is missing.

    Returns the columns that had to be added — empty when the table was already
    the declared shape. Tests can assert on it, but its real job is that the
    caller's very next INSERT cannot fail on a column the caller just declared.
    """
    table, cols = declared_columns(ddl)
    conn.execute(ddl)
    have = existing_columns(conn, table)
    added: list[str] = []
    for name, definition in cols:
        if name in have:
            continue
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {_addable(definition)}")
        added.append(name)
    if added:
        try:
            conn.commit()
        except Exception:
            pass
    return added


def ensure_tables(conn, ddls: Iterable[str]) -> dict[str, list[str]]:
    """:func:`ensure_table` over several declarations. ``{table: [added, ...]}``."""
    out: dict[str, list[str]] = {}
    for ddl in ddls:
        table, _ = declared_columns(ddl)
        out[table] = ensure_table(conn, ddl)
    return out

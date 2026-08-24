# CUI // SP-CTI
"""aca-hyg-05 — the academy's runtime SQL must be authored for PostgreSQL.

CLAUDE.md: "Runtime SQL is authored for PostgreSQL; translate_sql is a thin SQLite
init-fallback ONLY, never load-bearing." The academy was written entirely in the
SQLite dialect, so on the live PG backend every query depended on translate_sql
rewriting `?` to `%s`. Probed against the real database before the fix, twelve
ordinary read paths emitted 16 of these:

    translate_sql: bare ? placeholder detected in SQL - use %s for psycopg2 directly

That is translate_sql being load-bearing for an entire child app's runtime, and a
warning stream that loud hides real problems.

Locating placeholders with `ast` rather than a regex is the point: the academy is
full of `?` characters that are not placeholders — coach prompts that end in a
question, reflect-step labels, docstrings. Only a string that is syntactically an
argument of `.execute()` is a query.

Learner content under content/ is deliberately out of scope: those are teaching
exercises whose SQLite examples are the lesson.
"""
from __future__ import annotations

import ast
import pathlib
import re

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
ACADEMY = REPO_ROOT / "apps" / "forge_academy"

# Fragments concatenated into a query never appear inside the execute() call, so the
# AST cannot see them. These are the shapes the academy actually uses to build SQL.
_FRAGMENT_HINTS = (
    "SELECT ", "INSERT ", "UPDATE ", "DELETE ", " WHERE ", "VALUES (",
    " AND ", " JOIN ", " FROM ", " SET ", "ORDER BY", "GROUP BY", "LIMIT ",
)

# A placeholder is never preceded by a word character; a question mark ending a
# sentence always is. Without this, coach prompts and reflect-step labels like
# "Which Kanban task will you wire up first and why?" register as SQL, because
# uppercasing them turns their "and" into the " AND " hint above.
_PLACEHOLDER = re.compile(r"(?<![A-Za-z0-9_])\?")


def _runtime_sources() -> list[pathlib.Path]:
    return sorted(
        p for p in ACADEMY.rglob("*.py")
        if "/content/" not in p.as_posix() and "__pycache__" not in p.as_posix()
    )


def _query_literals(tree: ast.AST) -> list[ast.Constant]:
    """Every string literal handed to execute()/executemany()."""
    found: list[ast.Constant] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        name = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", "")
        if name not in {"execute", "executemany", "executescript"} or not node.args:
            continue
        arg = node.args[0]
        found += [n for n in ast.walk(arg)
                  if isinstance(n, ast.Constant) and isinstance(n.value, str)]
    return found


def _docstrings(tree: ast.AST) -> set[int]:
    """The id() of every docstring node — module, class and function.

    A docstring is never concatenated into a query, but it is prose, and prose is
    exactly what the fragment scan below cannot tell from SQL. The route docstring
    for the xAPI export (aca-trn-05, bfb472dc7, 2026-08-02) documents its query
    string as ``?user_id=`` / ``?include_unverified=1`` — a `?` with no word
    character before it — and contains an ordinary English " and ", which is the
    ` AND ` hint. That combination registered as an SQL fragment built with `?`
    and turned this file red three days after it landed green (born_red_survey
    finding 7460f16ce182e015, task-det-7460f16ce1). The docstring is correct as
    written; the scanner was reading text that is structurally never a query.
    """
    ids: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.ClassDef,
                                 ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        body = getattr(node, "body", None) or []
        first = body[0] if body else None
        if (isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant)
                and isinstance(first.value.value, str)):
            ids.add(id(first.value))
    return ids


@pytest.mark.parametrize("path", _runtime_sources(), ids=lambda p: p.name)
def test_queries_use_pg_native_placeholders(path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    offenders = [
        (n.lineno, n.value.strip()[:70])
        for n in _query_literals(tree)
        if _PLACEHOLDER.search(n.value)
    ]
    assert not offenders, (
        f"{path.name} passes SQLite-dialect SQL to execute(); "
        f"translate_sql would have to rewrite it on the live PG backend: {offenders}"
    )


@pytest.mark.parametrize("path", _runtime_sources(), ids=lambda p: p.name)
def test_sql_built_by_concatenation_also_uses_pg_placeholders(path):
    """The six that survived the first pass were assembled into a variable first.

    `q += " AND tier=?"` never appears inside the execute() call, so the check above
    cannot see it — and those six were exactly what the live probe still reported.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    inside_execute = {id(n) for n in _query_literals(tree)}
    # Prose, structurally: a docstring cannot be a fragment of anything.
    prose = _docstrings(tree)
    offenders = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
            continue
        if id(node) in inside_execute or id(node) in prose:
            continue
        if not _PLACEHOLDER.search(node.value):
            continue
        upper = node.value.upper()
        if any(h in upper for h in _FRAGMENT_HINTS):
            offenders.append((node.lineno, node.value.strip()[:70]))
    assert not offenders, (
        f"{path.name} builds SQL fragments with `?`: {offenders}"
    )


def test_placeholder_list_builders_use_pg_placeholders():
    """`",".join(["?"] * n)` for an IN (...) clause is the third shape.

    It is neither a literal in the call nor a recognisable SQL fragment, so it needs
    naming directly. One of these also hid a subtlety: `",".join("?" * n)` works only
    because "?" is a single character — the same trick with "%s" silently produces
    "%,s,%,s". That one is now a comprehension.
    """
    offenders = []
    for path in _runtime_sources():
        for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if '"?"' in line and "join" in line:
                offenders.append(f"{path.name}:{i}: {line.strip()[:70]}")
    assert not offenders, offenders


def test_the_translator_still_covers_the_sqlite_fallback():
    """Authoring PG-native is only safe because the fallback runs the other way.

    Tests and any PG-unreachable init path run on SQLite, where StorageConnection
    rewrites %s back to ?. If that direction ever stopped working, every academy
    query would break in exactly the environments this suite runs in.
    """
    from tools.db.storage import translate_sql

    out = translate_sql("SELECT * FROM fa_users WHERE username=%s", "sqlite")
    assert "%s" not in out and "?" in out

    ddl = translate_sql(
        "CREATE TABLE t (created_at TEXT DEFAULT (datetime('now')))", "postgresql"
    )
    assert "NOW()" in ddl, "the DDL defaults the academy declares must survive to PG"

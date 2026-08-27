# CUI // SP-CTI
"""`knowledge_server` writes `use_count`; `knowledge_patterns` must have it (cch-obs-05).

THE DEFECT. `tools/mcp/knowledge_server.py` orders by `use_count`, reads `row["use_count"]`
and increments it after a self-healing event. `knowledge_patterns` never had that column —
the canonical DDL declares `occurrence_count` and nothing else countable. So every
`search_knowledge` raised `column "use_count" does not exist`, and the Cortex `kb` backend
failed on every resolution.

Measured on the live board 2026-08-27, the most recent `cortex.resolve` carrying backend
detail: used={currency}, failed={dic, graph, kb, rag}. CLAUDE.md records the same error
string twice as a known Cortex defect.

WHY NOT JUST RENAME THE CODE TO `occurrence_count`. They are different facts.
`occurrence_count` is how often the PROBLEM was seen; the increment fires after a pattern was
USED to fix something. Pointing it at `occurrence_count` would inflate a problem's incidence
every time its remedy worked — a metric that rises when things go well.
"""
from __future__ import annotations

import ast
import pathlib
import re

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
SERVER = REPO_ROOT / "tools" / "mcp" / "knowledge_server.py"
INIT_DDL = REPO_ROOT / "tools" / "db" / "init_icdev_db.py"
MIGRATION = (
    REPO_ROOT / "tools" / "db" / "migrations"
    / "20260827224903_knowledge_patterns_use_count" / "up.py"
)


def _knowledge_patterns_ddl() -> str:
    """The CREATE TABLE body for knowledge_patterns out of the canonical DDL."""
    source = INIT_DDL.read_text(encoding="utf-8")
    start = source.index("CREATE TABLE IF NOT EXISTS knowledge_patterns")
    end = source.index(");", start)
    return source[start:end]


def test_the_server_really_does_reference_use_count():
    """The premise. If this stops being true, the column is no longer owed."""
    source = SERVER.read_text(encoding="utf-8")
    assert "use_count" in source
    assert re.search(r"SET\s+use_count\s*=\s*use_count\s*\+\s*1", source), (
        "the increment is what makes this a WRITE and not merely a read of a missing column"
    )


def test_the_canonical_ddl_declares_use_count():
    """The fix. Without it every search_knowledge raises and the Cortex kb rung dies."""
    ddl = _knowledge_patterns_ddl()
    assert "use_count" in ddl, (
        "knowledge_patterns must declare use_count — tools/mcp/knowledge_server.py orders "
        "by it, reads it and increments it"
    )


def test_use_count_is_kept_apart_from_occurrence_count():
    """Two different facts, and merging them gives a metric that rises when things go well."""
    ddl = _knowledge_patterns_ddl()
    assert "occurrence_count" in ddl
    assert re.search(r"\buse_count\s+INTEGER\s+DEFAULT\s+0", ddl), (
        "use_count defaults to 0: a pattern that has healed nothing has been used zero "
        "times. occurrence_count defaults to 1 because a pattern is created BY an "
        "occurrence; this counter has no founding event."
    )


def test_the_migration_adds_it_portably():
    """`ADD COLUMN IF NOT EXISTS` is PostgreSQL-only and conftest forces sqlite.

    Checked against the SQL the code actually executes, not against the file's text: the
    docstring explains why that clause is avoided, and a substring search over the whole
    source matches the explanation. That is precisely the defect kpr-extrepo-03 fixed in
    another structural test — a string match that measured prose instead of code.
    """
    tree = ast.parse(MIGRATION.read_text(encoding="utf-8"))
    names = {n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
    assert {"up", "down", "_is_pg", "_has_column"} <= names

    up_fn = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "up")
    executed = [
        node.value
        for node in ast.walk(up_fn)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    ] + [
        # f-strings: check their literal halves too
        part.value
        for node in ast.walk(up_fn)
        if isinstance(node, ast.JoinedStr)
        for part in node.values
        if isinstance(part, ast.Constant) and isinstance(part.value, str)
    ]
    # `up`'s own docstring is a Constant too; drop it before matching.
    body_doc = ast.get_docstring(up_fn) or ""
    sql = [s for s in executed if s != body_doc]
    offenders = [s for s in sql if "IF NOT EXISTS" in s.upper()]
    assert offenders == [], (
        f"the ADD must be guarded by a column check, not a PG-only clause: {offenders}"
    )
    assert any("ADD COLUMN" in s.upper() for s in sql), "the migration must actually add it"


def test_the_migration_detects_the_backend_the_way_storage_does():
    """A connection-class sniff returns False on real PostgreSQL: storage hands back a
    wrapper, so `type(conn).__module__` is storage's. Measured against the live board."""
    source = MIGRATION.read_text(encoding="utf-8")
    assert '_backend' in source, "backend detection must read conn._backend"
    assert "__module__" not in source, (
        "sniffing the connection class is the trap: it reports sqlite on a PostgreSQL "
        "connection and the migration silently takes the wrong branch"
    )

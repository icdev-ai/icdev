#!/usr/bin/env python3
# CUI // SP-CTI
"""Guard against SQLite-style ``?`` placeholders in runtime SQL call sites.

CLAUDE.md: runtime SQL is authored for PostgreSQL; ``translate_sql`` is a thin
SQLite init-fallback ONLY and must never be load-bearing.

``translate_sql`` silently rewrites ``?`` -> ``%s`` (and logs a warning), so a
runtime module that builds ``?`` placeholders still *works* on PostgreSQL — it
just works entirely because of the shim. Worse, a statement that MIXES both
styles is valid on neither backend on its own: psycopg2 rejects ``?`` and
sqlite3 rejects ``%s``.

These tests pin the mixed case at zero for runtime modules, and pin the
correct builder idiom.
"""
from __future__ import annotations

import pathlib
import re

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]

#: init / seed / migrate / test paths are where translate_sql IS sanctioned.
EXCLUDED_PARTS = {"tests", "migrations", "seeds"}
EXCLUDED_STEMS = {"init_db", "init_icdev_db", "bootstrap_pg", "migrate", "migrate_to_storage"}

#: ``x = ",".join("?" * n)`` / ``x = ", ".join(["?"] * n)`` and friends.
_BUILDER = re.compile(r"""(\w+)\s*=\s*["'][,\s]*["']\.join\(\s*\[?\s*["']\?["']\s*\]?\s*\*""")
_SQLISH = re.compile(r"(SELECT|INSERT|UPDATE|DELETE)\s", re.I)


def _runtime_py_files():
    for path in (REPO_ROOT / "tools").rglob("*.py"):
        parts = set(path.parts)
        if parts & EXCLUDED_PARTS:
            continue
        if path.stem in EXCLUDED_STEMS:
            continue
        yield path


def _mixed_placeholder_sites():
    """Yield (path, lineno) where a ?-builder feeds SQL that also uses %s."""
    for path in _runtime_py_files():
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for i, line in enumerate(lines):
            m = _BUILDER.search(line)
            if not m:
                continue
            var = m.group(1)
            window = "\n".join(lines[i: i + 14])
            if f"{{{var}}}" not in window or not _SQLISH.search(window):
                continue
            if "%s" in window:
                yield path.relative_to(REPO_ROOT), i + 1


def test_no_mixed_placeholder_styles_in_runtime_sql():
    """A statement mixing %s and ? runs on neither backend without the shim."""
    sites = sorted(_mixed_placeholder_sites())
    assert sites == [], (
        "Runtime SQL mixes %s and ? placeholders. psycopg2 rejects ?, sqlite3 "
        "rejects %s — these only work because translate_sql rewrites them, "
        'which CLAUDE.md forbids at runtime call sites. Use \', \'.join(["%s"] * n):\n'
        + "\n".join(f"  {p}:{ln}" for p, ln in sites)
    )


def test_translated_sql_is_identical_for_both_builder_styles():
    """The fix is a no-op after translation — proves it is behaviour-preserving."""
    from tools.db.storage import translate_sql

    base = "SELECT id FROM kg_nodes WHERE graph_id = %s AND entity_type IN ({})"
    old = base.format(",".join("?" * 3))
    new = base.format(",".join(["%s"] * 3))
    for backend in ("postgresql", "sqlite"):
        assert translate_sql(old, backend) == translate_sql(new, backend)


@pytest.mark.parametrize("n", [1, 3, 7])
def test_list_form_is_required_for_percent_s_builder(n):
    """", ".join("%s" * n) is a trap: join() iterates the string's CHARACTERS.

    This is not hypothetical — tools/ccc_canvas/loa_workflow.py shipped it as
    an except-branch fallback, where it would have produced "%, s, %, s, ...".
    """
    correct = ", ".join(["%s"] * n)
    assert correct.count("%s") == n

    if n > 1:
        broken = ", ".join("%s" * n)
        assert broken != correct
        assert "%, s" in broken

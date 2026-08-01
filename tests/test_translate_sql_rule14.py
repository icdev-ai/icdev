# CUI // SP-CTI
"""Rule-14 (sqlite_master → information_schema) translation tests (pgrt-sweep-07).

``translate_sql`` rewrites a small set of ``sqlite_master`` system-catalogue
shapes to PostgreSQL ``information_schema.tables`` so init/seed/migrate fallback
paths keep working when PG is unreachable at startup.  The list-all regexes used
to be un-anchored, so they PREFIX-matched a literal-name query
(``... WHERE type='table' AND name='foo'``): only the prefix was rewritten,
leaving a dangling ``AND name='foo'`` that references information_schema's
nonexistent ``name`` column — invalid PG SQL.

These tests lock in that:

* the three legitimate rule-14 shapes still translate;
* a literal-name form and an ``as cnt``-aliased form are NOT mangled (either
  translated correctly or left untouched — never rewritten into invalid SQL);
* a trailing ``ORDER BY`` on the list-all shape still translates cleanly.
"""
from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tools.db.storage import translate_sql  # noqa: E402


def _pg(sql: str) -> str:
    return translate_sql(sql, "postgresql")


def _mangled(out: str) -> bool:
    """True if the output rewrote to information_schema but kept a dangling
    ``name`` predicate (information_schema.tables has ``table_name``, not
    ``name``) — i.e. the exact invalid-PG shape this fix prevents."""
    low = out.lower()
    if "information_schema" not in low:
        return False
    # Strip the legitimate alias/column so only a *bare* ``name`` predicate trips.
    stripped = low.replace("table_name", "")
    return "name=" in stripped.replace(" ", "") and "and" in stripped


# ---------------------------------------------------------------------------
# The three legitimate shapes still translate
# ---------------------------------------------------------------------------


def test_list_all_names_translates():
    out = _pg("SELECT name FROM sqlite_master WHERE type='table'")
    assert out == (
        "SELECT table_name AS name FROM information_schema.tables "
        "WHERE table_schema = 'public'"
    )
    assert "sqlite_master" not in out


def test_count_all_translates():
    out = _pg("SELECT count(*) FROM sqlite_master WHERE type='table'")
    assert out == (
        "SELECT count(*) FROM information_schema.tables WHERE table_schema = 'public'"
    )
    assert "sqlite_master" not in out


def test_named_parameterised_form_translates():
    out = _pg("SELECT 1 FROM sqlite_master WHERE type='table' AND name=%s")
    assert out == (
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_schema = 'public' AND table_name = %s"
    )
    assert "sqlite_master" not in out


def test_named_count_parameterised_form_translates():
    out = _pg("SELECT count(*) FROM sqlite_master WHERE type='table' AND name=%s")
    assert out == (
        "SELECT count(*) FROM information_schema.tables "
        "WHERE table_schema = 'public' AND table_name = %s"
    )
    assert "sqlite_master" not in out


# ---------------------------------------------------------------------------
# The regression: literal-name / aliased forms must NOT be mangled
# ---------------------------------------------------------------------------


def test_literal_name_form_not_mangled():
    # The named regex handles only ``AND name=%s``; a literal ``AND name='foo'``
    # must be left ALONE, never rewritten into ``... AND name='foo'`` against
    # information_schema (which has no ``name`` column).
    out = _pg("SELECT name FROM sqlite_master WHERE type='table' AND name='foo'")
    assert not _mangled(out), out


def test_literal_name_count_form_not_mangled():
    out = _pg("SELECT count(*) FROM sqlite_master WHERE type='table' AND name='foo'")
    assert not _mangled(out), out


def test_as_cnt_aliased_form_not_mangled():
    # ``count(*) as cnt`` does not match the named regex (``as cnt`` between the
    # aggregate and FROM); it must not be partially rewritten either.
    out = _pg("SELECT count(*) as cnt FROM sqlite_master WHERE type='table' AND name=%s")
    assert not _mangled(out), out


# ---------------------------------------------------------------------------
# Trailing clauses on the list-all shape still translate
# ---------------------------------------------------------------------------


def test_list_all_with_order_by_translates():
    out = _pg("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
    assert out == (
        "SELECT table_name AS name FROM information_schema.tables "
        "WHERE table_schema = 'public' ORDER BY name"
    )
    assert "sqlite_master" not in out

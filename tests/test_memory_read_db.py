# CUI // SP-CTI
"""oss2-fix-05 (D6/D7) — memory_read renders DB entries; PG-style placeholders.

memory_read is the module behind `python tools/memory/memory_read.py --format
markdown`, the FIRST command in CLAUDE.md's Session Start Protocol. It had no tests,
which is why D6 survived: read_db_recent selects 6 columns but format_markdown
unpacked 4 (ValueError), and the SELECT used bare ? placeholders (SQLite dialect)
that trip translate_sql's warning on the PostgreSQL backend.
"""
from __future__ import annotations

import importlib

mr = importlib.import_module("tools.memory.memory_read")


def test_format_markdown_renders_six_column_rows():
    """D6 crash guard: read_db_recent yields 6-column rows; rendering must not raise
    `ValueError: too many values to unpack (expected 4)`."""
    rows = [
        ("deployed the helm chart", "procedure", 7, "2026-07-26T10:00:00", "CUI", ""),
        ("the sky is blue", "fact", 3, "2026-07-26T09:00:00", "CUI", "NOFORN"),
    ]
    out = mr.format_markdown("long-term text", [], rows)
    assert "deployed the helm chart" in out
    assert "[procedure]" in out
    assert "importance: 7" in out
    assert "2026-07-26T10:00:00" in out
    # the trailing security-context columns are not rendered but must not break it
    assert "NOFORN" not in out


def test_read_db_recent_uses_pg_style_placeholders():
    """D6: the SELECT must use %s (PostgreSQL), not bare ? (SQLite dialect), so it
    stops tripping translate_sql's bare-placeholder warning on the live backend."""
    captured = {}

    class _Cur:
        def execute(self, sql, params):
            captured["sql"] = sql
            captured["params"] = params

        def fetchall(self):
            return []

    class _Conn:
        def cursor(self):
            return _Cur()

        def close(self):
            pass

    import unittest.mock as m
    with m.patch.object(mr, "_connect", return_value=_Conn()):
        mr.read_db_recent(limit=5, user_id="u1", tenant_id="t1")

    sql = captured["sql"]
    assert "%s" in sql
    assert "?" not in sql, "bare ? placeholder must be gone (PG dialect)"
    # three bound params: user_id, tenant_id, limit
    assert captured["params"] == ["u1", "t1", 5]


def test_reset_decay_documents_it_is_unwired():
    """D7: reset_decay's docstring must no longer claim a live strengthening path."""
    from tools.memory.memory_write import reset_decay

    rd = (reset_decay.__doc__ or "").lower()
    assert "no callers" in rd or "dead column" in rd

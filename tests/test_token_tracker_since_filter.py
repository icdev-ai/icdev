# [TEMPLATE: CUI // SP-CTI]
"""token_tracker ``since`` filter reads the column the table actually has.

``get_usage_summary(since=...)`` and the CLI ``--since`` flag filtered on a
``timestamp`` column. ``agent_token_usage`` has no such column -- its time is
``created_at`` (tools/db/schema/pg_consolidated.sql, init_icdev_db.py and the
module's own CREATE_TABLE_SQL all agree) -- so every ``since`` query raised
``no such column: timestamp`` / ``UndefinedColumn`` instead of filtering.

The table is created by the module's own ``_ensure_table`` bootstrap, so no
fixture DDL is duplicated here.
"""

import importlib
import json
import sqlite3
import sys

import pytest

tt = importlib.import_module("tools.agent.token_tracker")

OLD = "2026-01-01T00:00:00+00:00"
NEW = "2026-06-01T00:00:00+00:00"


@pytest.fixture()
def db_path(tmp_path):
    path = tmp_path / "token_usage.db"
    # Bootstrap the table through the module (its canonical CREATE_TABLE_SQL).
    tt._connect(path).close()
    conn = sqlite3.connect(str(path))
    try:
        for created_at, tokens in ((OLD, 100), (NEW, 7)):
            conn.execute(
                "INSERT INTO agent_token_usage (agent_id, project_id, model_id, "
                "input_tokens, output_tokens, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                ("builder-agent", "proj-since", "m", tokens, 0, created_at),
            )
        conn.commit()
    finally:
        conn.close()
    return path


def test_since_excludes_rows_before_the_bound(db_path):
    summary = tt.get_usage_summary(since="2026-03-01T00:00:00", db_path=db_path)
    assert summary["count"] == 1
    assert summary["total_input"] == 7


def test_no_since_counts_every_row(db_path):
    summary = tt.get_usage_summary(db_path=db_path)
    assert summary["count"] == 2
    assert summary["total_input"] == 107


def test_cli_since_flag_filters(db_path, monkeypatch, capsys):
    monkeypatch.setattr(tt, "DB_PATH", db_path)
    monkeypatch.setattr(
        sys, "argv",
        ["token_tracker.py", "--action", "summary", "--since", "2026-03-01", "--json"],
    )
    tt.main()
    out = json.loads(capsys.readouterr().out)
    assert out["count"] == 1
    assert out["total_input"] == 7

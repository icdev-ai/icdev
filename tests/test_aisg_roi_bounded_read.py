# CUI // SP-CTI
"""qa-fail-1f5a9e0dfee73531: the AISG ROI pages must not read the whole table.

``aisg_roi_events`` gains a row per genesis reflex (118,466 on 2026-10-14), and
``/dashboard/executive-view`` + ``/ai-roi`` fetched every row to render ten of
them plus a few sums — ~9.5s and ~12s per request, enough to time out the nav
sweep. These tests seed more rows than either page displays and assert that no
fetch returns more rows than the page needs, while the aggregates stay exact.
"""
from __future__ import annotations

import importlib

import pytest

from tests._schema_compat import ensure_table

_CREATE_SQL = """
CREATE TABLE IF NOT EXISTS aisg_roi_events (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    action_type          TEXT    NOT NULL,
    time_saved_minutes   NUMERIC NOT NULL,
    description          TEXT,
    triggered_by         TEXT,
    occurred_at          TEXT    NOT NULL
)
"""

_N_ROWS = 40  # comfortably more than the 10 "recent" rows either page shows
_RECENT = 10


@pytest.fixture
def seeded_db(tmp_path, monkeypatch):
    monkeypatch.setenv("ICDEV_DB_PATH", str(tmp_path / "icdev.db"))
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    from tools.db.storage import get_connection

    conn = get_connection()
    try:
        ensure_table(conn, _CREATE_SQL)
        for i in range(_N_ROWS):
            month = "2026-04" if i < 30 else "2026-05"
            conn.execute(
                "INSERT INTO aisg_roi_events "
                "(action_type, time_saved_minutes, description, triggered_by, occurred_at) "
                "VALUES (%s, %s, %s, %s, %s)",
                ("genesis_reflex", 6, f"e{i}", "genesis_daemon",
                 f"{month}-{(i % 28) + 1:02d}T09:00:{i % 60:02d}"),
            )
        conn.commit()
    finally:
        conn.close()


def _spy_fetches(monkeypatch, mod) -> list[int]:
    """Wrap ``mod.get_connection`` so every fetchall's row count is recorded."""
    sizes: list[int] = []
    real = mod.get_connection

    class _Cur:
        def __init__(self, cur):
            self._cur = cur

        def execute(self, *a, **k):
            self._cur.execute(*a, **k)
            return self

        def fetchall(self):
            rows = self._cur.fetchall()
            sizes.append(len(rows))
            return rows

        def __getattr__(self, name):
            return getattr(self._cur, name)

    class _Conn:
        def __init__(self, conn):
            self._conn = conn

        def cursor(self):
            return _Cur(self._conn.cursor())

        def __getattr__(self, name):
            return getattr(self._conn, name)

    monkeypatch.setattr(mod, "get_connection", lambda *a, **k: _Conn(real(*a, **k)))
    return sizes


def test_executive_view_reads_a_bounded_number_of_rows(seeded_db, monkeypatch):
    mod = importlib.import_module("tools.aisg.executive_view")
    sizes = _spy_fetches(monkeypatch, mod)

    data = mod.get_executive_data()

    assert max(sizes) <= _RECENT, f"fetched {max(sizes)} rows to render {_RECENT}"
    assert data["total_tasks_automated"] == _N_ROWS
    assert data["hours_saved"] == 4.0  # 40 * 6 min
    assert len(data["roi_events"]) == _RECENT
    assert data["roi_events"][0]["occurred_at"].startswith("2026-05")
    assert data["agent_activity"][0]["agent"] == "genesis_daemon"
    assert data["agent_activity"][0]["tasks_completed"] == _N_ROWS


def test_roi_summary_reads_a_bounded_number_of_rows(seeded_db, monkeypatch):
    mod = importlib.import_module("tools.aisg.roi_tracker")
    sizes = _spy_fetches(monkeypatch, mod)

    summary = mod.get_roi_summary()

    assert max(sizes) <= _RECENT, f"fetched {max(sizes)} rows to render {_RECENT}"
    assert summary["total_events"] == _N_ROWS
    assert summary["hours_saved"] == 4.0
    assert len(summary["recent_events"]) == _RECENT
    trend = {m["key"]: m["hours"] for m in summary["monthly_trend"]}
    assert trend == {"2026-04": 3.0, "2026-05": 1.0}

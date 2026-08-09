# CUI // SP-CTI
"""Regression — failure_triage's recency window must key on the FAILURE time.

Kept in its own file rather than appended to ``tests/test_failure_triage.py``:
the sibling card kax-recover-01 is remediating the same 2026-08-08 incident from
the other side (a deny-list of non-code failure reasons) and appends to the end
of that file, so two branches editing the same tail is a merge conflict for no
benefit. The two fixes are complementary — a deny-list filters WHICH reasons are
actionable, this one fixes WHICH failures count as recent, and a genuine old
failure that gets re-queued today is caught only by the latter.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

import _sql_compat
from tools.db import storage as storage_mod


@pytest.fixture
def ft(monkeypatch, tmp_path):
    """failure_triage with its file-backed state redirected into tmp_path."""
    from tools.workflow import failure_triage as ft_mod

    monkeypatch.setattr(ft_mod, "TRIAGED_DIR", tmp_path / "triaged")
    monkeypatch.setattr(ft_mod, "RATE_FILE", tmp_path / "rate.json")
    monkeypatch.delenv(ft_mod.AUTOFIX_ENV, raising=False)
    return ft_mod


class TestFindRecentFailuresWindow:
    """Recency must key on WHEN THE TASK FAILED, not on when its row changed.

    Measured 2026-08-08: re-queueing nine sbx tasks (closing stale PRs so they
    rebuild against current main) refreshed ``updated_at`` while they still
    carried ``last_failure_reason`` from the attempts whose PRs had just been
    closed. ``find_recent_failures`` selected on ``updated_at``, so five tasks
    with nothing wrong with them entered the autofix queue; only the absence of
    ICDEV_AUTOFIX_AUTOMERGE kept patches off main. Startup recovery had the same
    shape — it used to stamp a failure reason on an interrupted task.
    """

    _SCHEMA = """
    CREATE TABLE kanban_tasks (
        id                  TEXT PRIMARY KEY,
        title               TEXT,
        description         TEXT,
        task_type           TEXT,
        priority            TEXT DEFAULT 'high',
        status              TEXT,
        depends_on_task_id  TEXT,
        failure_count       INTEGER DEFAULT 0,
        last_failure_reason TEXT,
        last_failure_at     TEXT,
        updated_at          TEXT
    );
    """

    def _seed(self, db_path, rows):
        """Build the schema and rows on a raw connection — test-local `?` SQL only.

        Kept out of :meth:`_db` on purpose: the factory handed to production code
        must contain nothing but the translating wrapper, or a reader (and
        ``coherence_checker.check_test_db_isolation``) cannot tell which
        connection the module under test actually gets.
        """
        conn = sqlite3.connect(str(db_path))
        conn.executescript(self._SCHEMA)
        for r in rows:
            conn.execute(
                "INSERT INTO kanban_tasks (id, title, status, last_failure_reason, "
                "last_failure_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                (r["id"], r["id"], r.get("status", "backlog"),
                 r.get("last_failure_reason"), r.get("last_failure_at"),
                 r["updated_at"]),
            )
        conn.commit()
        conn.close()

    def _db(self, tmp_path, monkeypatch, rows):
        """Point ``failure_triage``'s late-bound get_connection at a seeded DB.

        Through ``_sql_compat``, never a bare sqlite3 handle: the query under
        test is authored for PostgreSQL, and an untranslated ``%s`` raises inside
        the function's own ``except`` — which returns [] and makes every
        assertion here pass against a no-op.
        """
        db_path = tmp_path / "triage.db"
        self._seed(db_path, rows)

        def _translating_factory(*_a, **_kw):
            return _sql_compat.connect(db_path)

        monkeypatch.setattr(storage_mod, "get_connection", _translating_factory)

    def test_a_requeue_does_not_re_date_an_old_failure(self, ft, tmp_path, monkeypatch):
        now = datetime.now(timezone.utc)
        self._db(tmp_path, monkeypatch, [{
            "id": "sbx-fld-03",
            "last_failure_reason": "build failed on a PR that has since been closed",
            "last_failure_at": (now - timedelta(days=4)).isoformat(),
            "updated_at": now.isoformat(),      # re-queued minutes ago
        }])

        assert ft.find_recent_failures(window_hours=24) == []

    def test_a_genuine_recent_failure_is_still_found(self, ft, tmp_path, monkeypatch):
        now = datetime.now(timezone.utc)
        self._db(tmp_path, monkeypatch, [{
            "id": "sbx-fld-04",
            "last_failure_reason": "AttributeError: _x",
            "last_failure_at": (now - timedelta(hours=2)).isoformat(),
            "updated_at": (now - timedelta(hours=2)).isoformat(),
        }])

        found = ft.find_recent_failures(window_hours=24)

        assert [f["id"] for f in found] == ["sbx-fld-04"]

    def test_rows_predating_last_failure_at_fall_back_to_updated_at(self, ft, tmp_path, monkeypatch):
        """COALESCE keeps historical rows (reason set, timestamp NULL) in scope."""
        now = datetime.now(timezone.utc)
        self._db(tmp_path, monkeypatch, [{
            "id": "legacy-01",
            "last_failure_reason": "old failure with no last_failure_at",
            "last_failure_at": None,
            "updated_at": (now - timedelta(hours=1)).isoformat(),
        }])

        assert [f["id"] for f in ft.find_recent_failures(window_hours=24)] == ["legacy-01"]

    def test_a_task_with_no_failure_reason_is_never_returned(self, ft, tmp_path, monkeypatch):
        self._db(tmp_path, monkeypatch, [{
            "id": "clean-01",
            "last_failure_reason": None,
            "last_failure_at": None,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }])

        assert ft.find_recent_failures(window_hours=24) == []

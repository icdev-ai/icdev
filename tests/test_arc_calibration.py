# CUI // SP-CTI
"""Tests for ARC calibration recording + precision math (arc-cal-03).

Covers, through an in-memory SQLite ``triage_runs`` + ``triage_outcomes`` +
``kanban_tasks`` tables:

  * ``_record_triage_outcome`` — exactly one outcomes row per apply.
  * ``resolve_outcomes`` — appends resolution rows (never UPDATE), marks
    ``held`` when the task reaches ``done``, ``reverted`` when a newer apply
    exists.
  * ``rolling_precision`` — ``held_count / applied_count`` per
    (task_type, signature_class) over the window.
  * Append-only constraint — original audit rows remain untouched.

No live daemon.
"""
import sqlite3
import uuid
from datetime import datetime, timezone

import pytest


class _PersistentConn(sqlite3.Connection):
    """In-memory connection whose ``close()`` is a no-op."""

    def close(self):  # noqa: D401
        pass

    def _hard_close(self):
        super().close()


@pytest.fixture
def shared_conn(monkeypatch):
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    conn = sqlite3.connect(":memory:", factory=_PersistentConn)
    conn.row_factory = sqlite3.Row

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS triage_runs (
            id              TEXT PRIMARY KEY,
            started_at      TEXT NOT NULL,
            finished_at     TEXT,
            scanned         INTEGER NOT NULL DEFAULT 0,
            applied         INTEGER NOT NULL DEFAULT 0,
            suggested       INTEGER NOT NULL DEFAULT 0,
            autofix_enabled INTEGER NOT NULL DEFAULT 0,
            trace_id        TEXT,
            created_at      TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS triage_outcomes (
            id                          TEXT PRIMARY KEY,
            run_id                      TEXT NOT NULL,
            task_id                     TEXT NOT NULL,
            signature                   TEXT NOT NULL,
            signature_class             TEXT,
            task_type                   TEXT,
            recommendation              TEXT,
            confidence_raw              REAL,
            confidence_selfconsistency  REAL,
            gate_decision               TEXT,
            applied                     INTEGER NOT NULL DEFAULT 0,
            verify_rc                   INTEGER,
            autofix_branch              TEXT,
            autofix_commit              TEXT,
            merged                      INTEGER NOT NULL DEFAULT 0,
            held                        TEXT,
            resolution_of               TEXT,
            created_at                  TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS kanban_tasks (
            id                    TEXT PRIMARY KEY,
            title                 TEXT NOT NULL,
            description           TEXT,
            task_type             TEXT DEFAULT 'build',
            priority              TEXT DEFAULT 'high',
            status                TEXT DEFAULT 'backlog',
            scheduled_at          TEXT,
            created_at            TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at            TEXT DEFAULT CURRENT_TIMESTAMP,
            completed_at          TEXT,
            failure_count         INTEGER DEFAULT 0,
            last_failure_reason   TEXT,
            last_failure_at       TEXT
        )
        """
    )
    conn.commit()

    import importlib

    storage = importlib.import_module("tools.db.storage")
    monkeypatch.setattr(storage, "get_connection", lambda *a, **k: conn)
    yield conn
    conn._hard_close()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _seed_run(conn) -> str:
    run_id = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO triage_runs (id, started_at) VALUES (?, ?)",
        (run_id, _now()),
    )
    conn.commit()
    return run_id


def _seed_task(conn, task_id, task_type="fix", status="backlog"):
    conn.execute(
        "INSERT INTO kanban_tasks (id, title, task_type, status) VALUES (?, ?, ?, ?)",
        (task_id, f"Task {task_id}", task_type, status),
    )
    conn.commit()


def _seed_outcome(conn, run_id, task_id, signature, applied=1, held=None,
                  task_type="fix", signature_class="typo",
                  created_at=None, **extra):
    if created_at is None:
        created_at = _now()
    outcome_id = extra.get("id", str(uuid.uuid4()))
    conn.execute(
        "INSERT INTO triage_outcomes "
        "(id, run_id, task_id, signature, signature_class, task_type, "
        " applied, held, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (outcome_id, run_id, task_id, signature, signature_class,
         task_type, applied, held, created_at),
    )
    conn.commit()
    return outcome_id


# ---------------------------------------------------------------------------
# _record_triage_outcome
# ---------------------------------------------------------------------------

class TestRecordOutcome:
    def test_apply_writes_exactly_one_outcomes_row(self, shared_conn, monkeypatch):
        from tools.workflow import failure_triage as ft

        run_id = _seed_run(shared_conn)
        task = {"id": "t-1", "task_type": "fix", "last_failure_reason": "AttributeError: x"}
        diag = {"root_cause": "typo", "recommendation": "patch", "confidence": 0.95}
        entry = {"apply_result": {"applied": True, "verification_rc": 0, "branch": "b1", "commit": "c1"}}
        ft._record_triage_outcome(run_id, task, diag, None, entry)

        rows = shared_conn.execute("SELECT * FROM triage_outcomes").fetchall()
        assert len(rows) == 1
        r = dict(rows[0])
        assert r["task_id"] == "t-1"
        assert r["applied"] == 1
        assert r["held"] is None
        assert r["autofix_branch"] == "b1"


# ---------------------------------------------------------------------------
# resolve_outcomes — append-only
# ---------------------------------------------------------------------------

class TestResolveOutcomes:
    def test_held_appends_resolution_row(self, shared_conn):
        from tools.workflow import failure_triage as ft

        run_id = _seed_run(shared_conn)
        _seed_task(shared_conn, "t-held", status="done")
        orig_id = _seed_outcome(shared_conn, run_id, "t-held", "sig-held",
                                task_type="fix", signature_class="typo")

        result = ft.resolve_outcomes(window_days=7)
        assert result["resolved"] == 1
        assert result["details"][0]["held"] == "held"

        # Original row untouched
        orig = dict(shared_conn.execute(
            "SELECT * FROM triage_outcomes WHERE id = ?", (orig_id,)
        ).fetchone())
        assert orig["held"] is None
        assert orig["applied"] == 1

        # Resolution row appended
        res_rows = shared_conn.execute(
            "SELECT * FROM triage_outcomes WHERE resolution_of = ?", (orig_id,)
        ).fetchall()
        assert len(res_rows) == 1
        res = dict(res_rows[0])
        assert res["applied"] == 0
        assert res["held"] == "held"
        assert res["task_id"] == "t-held"
        assert res["signature"] == "sig-held"

    def test_reverted_appends_resolution_row(self, shared_conn):
        from tools.workflow import failure_triage as ft

        run_id = _seed_run(shared_conn)
        _seed_task(shared_conn, "t-rev", status="backlog")
        orig_id = _seed_outcome(
            shared_conn, run_id, "t-rev", "sig-rev",
            task_type="fix", signature_class="typo",
            created_at="2026-06-04T00:00:00",
        )
        # Newer apply outcome for same task + signature
        _seed_outcome(
            shared_conn, run_id, "t-rev", "sig-rev",
            task_type="fix", signature_class="typo",
            created_at="2026-06-08T00:00:00",
        )

        result = ft.resolve_outcomes(window_days=7)
        assert result["resolved"] == 1
        assert result["details"][0]["held"] == "reverted"

        # Original row untouched
        orig = dict(shared_conn.execute(
            "SELECT * FROM triage_outcomes WHERE id = ?", (orig_id,)
        ).fetchone())
        assert orig["held"] is None
        assert orig["applied"] == 1

        # Resolution row appended
        res_rows = shared_conn.execute(
            "SELECT * FROM triage_outcomes WHERE resolution_of = ?", (orig_id,)
        ).fetchall()
        assert len(res_rows) == 1
        res = dict(res_rows[0])
        assert res["applied"] == 0
        assert res["held"] == "reverted"

    def test_pending_skipped(self, shared_conn):
        from tools.workflow import failure_triage as ft

        run_id = _seed_run(shared_conn)
        _seed_task(shared_conn, "t-pend", status="backlog")
        _seed_outcome(shared_conn, run_id, "t-pend", "sig-pend")

        result = ft.resolve_outcomes(window_days=7)
        assert result["resolved"] == 0
        assert shared_conn.execute(
            "SELECT COUNT(*) FROM triage_outcomes WHERE applied = 0"
        ).fetchone()[0] == 0

    def test_idempotent_no_duplicate_resolutions(self, shared_conn):
        from tools.workflow import failure_triage as ft

        run_id = _seed_run(shared_conn)
        _seed_task(shared_conn, "t-idem", status="done")
        orig_id = _seed_outcome(shared_conn, run_id, "t-idem", "sig-idem")

        ft.resolve_outcomes(window_days=7)
        ft.resolve_outcomes(window_days=7)

        res_rows = shared_conn.execute(
            "SELECT * FROM triage_outcomes WHERE resolution_of = ?", (orig_id,)
        ).fetchall()
        assert len(res_rows) == 1


# ---------------------------------------------------------------------------
# rolling_precision — math
# ---------------------------------------------------------------------------

class TestRollingPrecision:
    def test_precision_held_over_applied_per_cohort(self, shared_conn):
        from tools.workflow import failure_triage as ft

        run_id = _seed_run(shared_conn)
        _seed_task(shared_conn, "t1", status="done")
        _seed_task(shared_conn, "t2", status="backlog")
        _seed_task(shared_conn, "t3", status="backlog")

        # Cohort A: 2 applied, 1 held (t1 done, t2 pending)
        o1 = _seed_outcome(shared_conn, run_id, "t1", "sA",
                           task_type="fix", signature_class="typo")
        _seed_outcome(shared_conn, run_id, "t2", "sA",
                      task_type="fix", signature_class="typo")
        # Cohort B: 1 applied, 0 held (pending)
        _seed_outcome(shared_conn, run_id, "t3", "sB",
                      task_type="build", signature_class="import")

        ft.resolve_outcomes(window_days=7)

        precision = ft.rolling_precision(window_days=7)
        assert "error" not in precision or precision.get("error") is None
        cohorts = {c["signature_class"]: c for c in precision["cohorts"]}

        assert cohorts["typo"]["applied_count"] == 2
        assert cohorts["typo"]["held_count"] == 1
        assert cohorts["typo"]["precision"] == 0.5

        assert cohorts["import"]["applied_count"] == 1
        assert cohorts["import"]["held_count"] == 0
        assert cohorts["import"]["precision"] == 0.0

    def test_precision_one_hundred_percent(self, shared_conn):
        from tools.workflow import failure_triage as ft

        run_id = _seed_run(shared_conn)
        _seed_task(shared_conn, "t1", status="done")
        _seed_outcome(shared_conn, run_id, "t1", "s1",
                      task_type="fix", signature_class="typo")
        ft.resolve_outcomes(window_days=7)

        precision = ft.rolling_precision(window_days=7)
        cohort = precision["cohorts"][0]
        assert cohort["applied_count"] == 1
        assert cohort["held_count"] == 1
        assert cohort["precision"] == 1.0

    def test_precision_zero_percent_reverted(self, shared_conn):
        from tools.workflow import failure_triage as ft

        run_id = _seed_run(shared_conn)
        _seed_task(shared_conn, "t1", status="backlog")
        orig_id = _seed_outcome(
            shared_conn, run_id, "t1", "s1",
            task_type="fix", signature_class="typo",
            created_at="2026-06-04T00:00:00",
        )
        # Newer apply for same signature → original reverted
        _seed_outcome(
            shared_conn, run_id, "t1", "s1",
            task_type="fix", signature_class="typo",
            created_at="2026-06-08T00:00:00",
        )
        ft.resolve_outcomes(window_days=7)

        precision = ft.rolling_precision(window_days=7)
        cohort = precision["cohorts"][0]
        assert cohort["applied_count"] == 2
        assert cohort["held_count"] == 0
        assert cohort["precision"] == 0.0

    def test_empty_window_returns_zero_cohorts(self, shared_conn):
        from tools.workflow import failure_triage as ft

        precision = ft.rolling_precision(window_days=7)
        assert precision["cohorts"] == []


# ---------------------------------------------------------------------------
# Append-only guard
# ---------------------------------------------------------------------------

class TestAppendOnly:
    def test_original_row_never_updated(self, shared_conn):
        """Original apply rows must keep held=NULL forever."""
        from tools.workflow import failure_triage as ft

        run_id = _seed_run(shared_conn)
        _seed_task(shared_conn, "t1", status="done")
        orig_id = _seed_outcome(shared_conn, run_id, "t1", "s1")

        ft.resolve_outcomes(window_days=7)

        rows = shared_conn.execute(
            "SELECT * FROM triage_outcomes WHERE id = ?", (orig_id,)
        ).fetchall()
        assert len(rows) == 1
        assert rows[0]["held"] is None
        assert rows[0]["applied"] == 1

    def test_no_delete_on_triage_outcomes(self, shared_conn):
        """Suite-level sanity: the test harness never deletes."""
        # This test passes implicitly if all other tests pass with
        # row counts monotonically increasing.
        from tools.workflow import failure_triage as ft

        run_id = _seed_run(shared_conn)
        _seed_task(shared_conn, "t1", status="done")
        _seed_outcome(shared_conn, run_id, "t1", "s1")
        ft.resolve_outcomes(window_days=7)

        total = shared_conn.execute(
            "SELECT COUNT(*) FROM triage_outcomes"
        ).fetchone()[0]
        assert total == 2  # original + resolution

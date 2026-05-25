"""Tests for tools/innovation/kanban_promoter.py

Covers pure functions (build_kanban_task, _priority_for_score, load_config)
and promote_signals against a fake connection. Integration with the real
PostgreSQL DB is exercised via the --dry-run --list CLI path.
"""
from __future__ import annotations

import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[2]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.innovation import kanban_promoter as kp  # noqa: E402


# --------------------------------------------------------------------------
# Priority mapping
# --------------------------------------------------------------------------
def test_priority_high_score():
    assert kp._priority_for_score(85, {"high": 80, "medium": 60}) == "high"


def test_priority_medium_score():
    assert kp._priority_for_score(65, {"high": 80, "medium": 60}) == "medium"


def test_priority_low_score():
    assert kp._priority_for_score(45, {"high": 80, "medium": 60}) == "low"


def test_priority_none_score():
    assert kp._priority_for_score(None, {"high": 80, "medium": 60}) == "low"


def test_priority_boundary_exactly_high():
    assert kp._priority_for_score(80, {"high": 80, "medium": 60}) == "high"


# --------------------------------------------------------------------------
# build_kanban_task
# --------------------------------------------------------------------------
def test_build_task_basic():
    sig = {"id": "sig-abc123def", "title": "Use Ruff for linting",
           "innovation_score": 85, "triage_result": "approved"}
    task = kp.build_kanban_task(sig, kp.DEFAULT_CONFIG)
    assert task["status"] == "suggested"
    assert task["priority"] == "high"
    assert task["source_prediction_id"] == "sig-abc123def"
    assert task["task_type"] == "research"
    assert task["title"].startswith("INNOV-sig-abc1: Use Ruff")
    assert "innovation_signals.id = sig-abc123def" in task["description"]


def test_build_task_title_truncated():
    sig = {"id": "sig-longone", "title": "x" * 200, "innovation_score": 50}
    task = kp.build_kanban_task(sig, kp.DEFAULT_CONFIG)
    assert len(task["title"]) <= 120


def test_build_task_includes_spec_when_present():
    sig = {"id": "sig-1", "title": "T", "innovation_score": 70,
           "spec_content": "This is a detailed solution spec.",
           "estimated_effort": "2 days"}
    task = kp.build_kanban_task(sig, kp.DEFAULT_CONFIG)
    assert "Solution spec" in task["description"]
    assert "2 days" in task["description"]


def test_build_task_missing_optional_fields():
    """Signals with only id/title shouldn't crash."""
    sig = {"id": "sig-minimal", "title": None, "innovation_score": None}
    task = kp.build_kanban_task(sig, kp.DEFAULT_CONFIG)
    assert task["priority"] == "low"
    # id is truncated to 8 chars in the title prefix
    assert "sig-mini" in task["title"]


# --------------------------------------------------------------------------
# Config loading
# --------------------------------------------------------------------------
def test_default_config_shape():
    cfg = kp.DEFAULT_CONFIG
    assert "triage_results" in cfg
    assert "min_innovation_score" in cfg
    assert "priority_thresholds" in cfg
    assert cfg["priority_thresholds"]["high"] > cfg["priority_thresholds"]["medium"]


def test_load_config_returns_dict():
    cfg = kp.load_config()
    assert isinstance(cfg, dict)
    assert "priority_thresholds" in cfg


# --------------------------------------------------------------------------
# promote_signals with a fake connection
# --------------------------------------------------------------------------
class _FakeCursor:
    def __init__(self):
        self.executed: list[tuple[str, tuple]] = []

    def execute(self, sql, params=()):
        self.executed.append((sql, tuple(params)))

    def fetchall(self):
        return []

    def fetchone(self):
        return None


class _FakeConn:
    def __init__(self):
        self._cur = _FakeCursor()
        self.committed = False

    def cursor(self):
        return self._cur

    def commit(self):
        self.committed = True


def test_promote_dry_run_inserts_nothing():
    signals = [{"id": "sig-1", "title": "A", "innovation_score": 70}]
    conn = _FakeConn()
    result = kp.promote_signals(conn, signals, kp.DEFAULT_CONFIG, dry_run=True)
    assert result["inserted"] == 1
    assert result["dry_run"] is True
    assert conn.committed is False
    assert len(conn._cur.executed) == 0


def test_promote_live_issues_insert_and_commits():
    signals = [
        {"id": "sig-a", "title": "A", "innovation_score": 85},
        {"id": "sig-b", "title": "B", "innovation_score": 65},
    ]
    conn = _FakeConn()
    result = kp.promote_signals(conn, signals, kp.DEFAULT_CONFIG, dry_run=False)
    assert result["inserted"] == 2
    assert conn.committed is True
    inserts = [sql for sql, _ in conn._cur.executed if "INSERT INTO kanban_tasks" in sql]
    assert len(inserts) == 2
    audits = [sql for sql, _ in conn._cur.executed if "audit_trail" in sql]
    assert len(audits) == 1  # single batch audit row


def test_promote_empty_list_no_audit_no_commit():
    conn = _FakeConn()
    result = kp.promote_signals(conn, [], kp.DEFAULT_CONFIG, dry_run=False)
    assert result["inserted"] == 0
    assert conn.committed is False
    assert len(conn._cur.executed) == 0

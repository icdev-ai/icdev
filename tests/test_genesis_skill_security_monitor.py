# CUI // SP-CTI
"""Tests for tools/genesis/reflexes/skill_security_monitor.py.

Uses an in-memory SQLite kanban_tasks table and patches
``skill_security_monitor._assess_skill`` so the tests exercise the reflex's
scan, dedupe, and Kanban card creation logic without running the full
SkillSpector code-pattern scanner.
"""
from __future__ import annotations

import sqlite3

import pytest

from tools.genesis.reflexes import skill_security_monitor


# Minimal kanban_tasks schema carrying the columns the reflex reads/writes.
_KANBAN_DDL = """
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
    executor_type         TEXT DEFAULT 'claude_cli',
    execution_id          TEXT,
    executor_url          TEXT,
    depends_on_task_id    TEXT,
    source_prediction_id  TEXT,
    failure_count         INTEGER DEFAULT 0,
    last_failure_reason   TEXT,
    last_failure_at       TEXT,
    dispatch_source       TEXT DEFAULT 'unknown',
    hitl_stage            TEXT,
    start_date            TEXT,
    target_date           TEXT,
    files_changed         INTEGER DEFAULT 0,
    lines_added           INTEGER DEFAULT 0,
    lines_removed         INTEGER DEFAULT 0,
    completed_via_bypass  INTEGER DEFAULT 0
)
"""


@pytest.fixture
def conn(tmp_path):
    """In-memory SQLite connection with the kanban_tasks table."""
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.execute(_KANBAN_DDL)
    c.commit()
    yield c
    c.close()


@pytest.fixture
def skill_dirs(tmp_path):
    """Two temporary skill directories: one clean, one blocked."""
    clean_dir = tmp_path / "icdev-clean"
    blocked_dir = tmp_path / "icdev-blocked"
    clean_dir.mkdir()
    blocked_dir.mkdir()
    (clean_dir / "SKILL.md").write_text("# Clean\n", encoding="utf-8")
    (blocked_dir / "SKILL.md").write_text("# Blocked\n", encoding="utf-8")
    return {"clean": clean_dir, "blocked": blocked_dir}


@pytest.fixture
def fake_assess(monkeypatch, skill_dirs):
    """Replace the reflex's _assess_skill seam with deterministic results."""
    results = {
        skill_dirs["clean"]: {
            "allowed": True,
            "risk_score": 0.0,
            "risk_severity": "none",
            "findings_count": 0,
            "reason": "No dangerous patterns detected.",
        },
        skill_dirs["blocked"]: {
            "allowed": False,
            "risk_score": 75.0,
            "risk_severity": "high",
            "findings_count": 3,
            "reason": "Blocked: 3 high severity finding(s) require remediation.",
        },
    }

    def _fake(skill_dir):
        return results.get(
            skill_dir,
            {
                "allowed": True,
                "risk_score": 0.0,
                "risk_severity": "none",
                "findings_count": 0,
                "reason": "default clean",
            },
        )

    monkeypatch.setattr(skill_security_monitor, "_assess_skill", _fake)
    return results


def _count_cards(conn, status=None):
    sql = "SELECT COUNT(*) AS n FROM kanban_tasks"
    params = ()
    if status:
        sql += " WHERE status = ?"
        params = (status,)
    return conn.execute(sql, params).fetchone()["n"]


# --------------------------------------------------------------------------- #
# Reflex scan and card creation
# --------------------------------------------------------------------------- #
def test_clean_skill_creates_no_card(conn, skill_dirs, fake_assess):
    """A skill passing the gate must produce zero Kanban cards."""
    result = skill_security_monitor.run(
        {"skills": [str(skill_dirs["clean"])]}, conn
    )
    assert result["success"] is True
    assert result["scanned"] == 1
    assert result["flagged"] == 0
    assert result["cards_created"] == 0
    assert _count_cards(conn) == 0


def test_blocked_skill_creates_one_card(conn, skill_dirs, fake_assess):
    """A blocked skill must open exactly one suggested Kanban card."""
    result = skill_security_monitor.run(
        {"skills": [str(skill_dirs["blocked"])]}, conn
    )
    assert result["success"] is True
    assert result["scanned"] == 1
    assert result["flagged"] == 1
    assert result["cards_created"] == 1
    assert _count_cards(conn) == 1

    card = conn.execute(
        "SELECT title, status, task_type, priority, dispatch_source "
        "FROM kanban_tasks"
    ).fetchone()
    assert card["status"] == "suggested"
    assert card["task_type"] == "fix"
    assert card["priority"] == "high"
    assert card["dispatch_source"] == "skill_security_monitor"
    assert "icdev-blocked" in card["title"]
    assert "high" in card["title"]


def test_critical_severity_gets_critical_priority(conn, skill_dirs, monkeypatch):
    """A critical result maps to critical-priority Kanban card."""
    critical_dir = skill_dirs["blocked"]

    def _critical(skill_dir):
        return {
            "allowed": False,
            "risk_score": 99.0,
            "risk_severity": "critical",
            "findings_count": 5,
            "reason": "Blocked: 5 critical severity finding(s) require remediation.",
        }

    monkeypatch.setattr(skill_security_monitor, "_assess_skill", _critical)

    result = skill_security_monitor.run({"skills": [str(critical_dir)]}, conn)
    assert result["flagged"] == 1

    card = conn.execute("SELECT priority FROM kanban_tasks").fetchone()
    assert card["priority"] == "critical"


# --------------------------------------------------------------------------- #
# Dedupe
# --------------------------------------------------------------------------- #
def test_repeated_run_does_not_duplicate_cards(conn, skill_dirs, fake_assess):
    """Running the reflex twice for the same blocked skill must create one card."""
    r1 = skill_security_monitor.run(
        {"skills": [str(skill_dirs["blocked"])]}, conn
    )
    assert r1["cards_created"] == 1
    assert _count_cards(conn) == 1

    r2 = skill_security_monitor.run(
        {"skills": [str(skill_dirs["blocked"])]}, conn
    )
    assert r2["cards_created"] == 0
    assert r2["details"]["deduped"] == 1
    assert _count_cards(conn) == 1


def test_multiple_blocked_skills_create_multiple_cards(conn, skill_dirs, tmp_path, monkeypatch):
    """Two distinct blocked skills produce two distinct cards."""
    blocked_two = tmp_path / "icdev-blocked-two"
    blocked_two.mkdir()
    (blocked_two / "SKILL.md").write_text("# Blocked 2\n", encoding="utf-8")

    results = {
        skill_dirs["blocked"]: {
            "allowed": False,
            "risk_score": 75.0,
            "risk_severity": "high",
            "findings_count": 3,
            "reason": "Blocked.",
        },
        blocked_two: {
            "allowed": False,
            "risk_score": 50.0,
            "risk_severity": "high",
            "findings_count": 2,
            "reason": "Blocked.",
        },
    }

    monkeypatch.setattr(skill_security_monitor, "_assess_skill", results.get)

    result = skill_security_monitor.run(
        {"skills": [str(skill_dirs["blocked"]), str(blocked_two)]}, conn
    )
    assert result["scanned"] == 2
    assert result["flagged"] == 2
    assert result["cards_created"] == 2
    assert _count_cards(conn) == 2


# --------------------------------------------------------------------------- #
# Dry run
# --------------------------------------------------------------------------- #
def test_dry_run_reports_but_does_not_create_cards(conn, skill_dirs, fake_assess):
    """dry_run=True reports findings without inserting rows."""
    result = skill_security_monitor.run(
        {"skills": [str(skill_dirs["blocked"])], "dry_run": True}, conn
    )
    assert result["success"] is True
    assert result["flagged"] == 1
    assert result["cards_created"] == 0
    assert _count_cards(conn) == 0
    assert len(result["details"]["cards"]) == 1
    assert result["details"]["cards"][0].get("dry_run") is True


# --------------------------------------------------------------------------- #
# Contract
# --------------------------------------------------------------------------- #
def test_reflex_has_cadence_and_implementation_status():
    """Daemon contract: CADENCE_HOURS + IMPLEMENTATION_STATUS must be present."""
    assert isinstance(skill_security_monitor.CADENCE_HOURS, int)
    assert skill_security_monitor.IMPLEMENTATION_STATUS == "full"

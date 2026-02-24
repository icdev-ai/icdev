#!/usr/bin/env python3
# CUI // SP-CTI
"""Tests for AI Reassessment Scheduler (Phase 49).

Covers: create_schedule, check_overdue, complete_reassessment,
get_schedule_summary, frequency validation, REPLACE semantics,
multiple systems per project.
"""

import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

# Ensure project root is importable
BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.compliance.ai_reassessment_scheduler import (
    FREQUENCY_DAYS,
    VALID_FREQUENCIES,
    check_overdue,
    complete_reassessment,
    create_schedule,
    get_schedule_summary,
)


# ============================================================
# Fixtures
# ============================================================

@pytest.fixture
def db_path(tmp_path):
    """Create a temp DB with the ai_reassessment_schedule table."""
    db = tmp_path / "test.db"
    conn = sqlite3.connect(str(db))
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS ai_reassessment_schedule (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id TEXT NOT NULL,
            ai_system TEXT NOT NULL,
            frequency TEXT NOT NULL DEFAULT 'annual',
            next_due TEXT,
            last_completed TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            UNIQUE(project_id, ai_system)
        );
    """)
    conn.commit()
    conn.close()
    return db


# ============================================================
# create_schedule
# ============================================================

def test_create_schedule_annual(db_path):
    """Creating an annual schedule returns status=scheduled with correct fields."""
    result = create_schedule("proj-1", "Classifier", "annual", db_path=db_path)
    assert result["status"] == "scheduled"
    assert result["project_id"] == "proj-1"
    assert result["ai_system"] == "Classifier"
    assert result["frequency"] == "annual"
    # next_due should be roughly 365 days from now
    expected = (datetime.now(timezone.utc) + timedelta(days=365)).strftime("%Y-%m-%d")
    assert result["next_due"] == expected


def test_create_schedule_quarterly(db_path):
    """Creating a quarterly schedule computes next_due ~90 days out."""
    result = create_schedule("proj-1", "Detector", "quarterly", db_path=db_path)
    assert result["frequency"] == "quarterly"
    expected = (datetime.now(timezone.utc) + timedelta(days=90)).strftime("%Y-%m-%d")
    assert result["next_due"] == expected


def test_create_schedule_with_explicit_next_due(db_path):
    """Explicit next_due overrides computed value."""
    result = create_schedule("proj-1", "Analyzer", "annual", next_due="2027-06-15", db_path=db_path)
    assert result["next_due"] == "2027-06-15"


def test_create_schedule_invalid_frequency(db_path):
    """Invalid frequency raises ValueError."""
    with pytest.raises(ValueError, match="Invalid frequency"):
        create_schedule("proj-1", "System", "weekly", db_path=db_path)


def test_create_schedule_replaces_existing(db_path):
    """Same project+system INSERT OR REPLACE overwrites previous record."""
    create_schedule("proj-1", "Model-A", "annual", next_due="2027-01-01", db_path=db_path)
    result = create_schedule("proj-1", "Model-A", "quarterly", next_due="2026-06-01", db_path=db_path)
    assert result["frequency"] == "quarterly"
    assert result["next_due"] == "2026-06-01"

    # Verify only one row in DB
    conn = sqlite3.connect(str(db_path))
    count = conn.execute(
        "SELECT COUNT(*) FROM ai_reassessment_schedule WHERE project_id='proj-1' AND ai_system='Model-A'"
    ).fetchone()[0]
    conn.close()
    assert count == 1


# ============================================================
# check_overdue
# ============================================================

def test_check_overdue_empty(db_path):
    """Empty DB returns zero overdue items."""
    result = check_overdue("proj-1", db_path=db_path)
    assert result["total_overdue"] == 0
    assert result["overdue"] == []


def test_check_overdue_with_past_due(db_path):
    """A record with next_due in the past appears as overdue."""
    past_date = (datetime.now(timezone.utc) - timedelta(days=30)).strftime("%Y-%m-%d")
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "INSERT INTO ai_reassessment_schedule (project_id, ai_system, frequency, next_due) VALUES (?, ?, ?, ?)",
        ("proj-1", "OldSystem", "annual", past_date),
    )
    conn.commit()
    conn.close()

    result = check_overdue("proj-1", db_path=db_path)
    assert result["total_overdue"] == 1
    assert result["overdue"][0]["ai_system"] == "OldSystem"


def test_check_overdue_not_yet_due(db_path):
    """A record with next_due in the future does not appear as overdue."""
    future_date = (datetime.now(timezone.utc) + timedelta(days=90)).strftime("%Y-%m-%d")
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "INSERT INTO ai_reassessment_schedule (project_id, ai_system, frequency, next_due) VALUES (?, ?, ?, ?)",
        ("proj-1", "FutureSystem", "annual", future_date),
    )
    conn.commit()
    conn.close()

    result = check_overdue("proj-1", db_path=db_path)
    assert result["total_overdue"] == 0


def test_check_overdue_days_calculation(db_path):
    """days_overdue should be approximately correct."""
    days_ago = 45
    past_date = (datetime.now(timezone.utc) - timedelta(days=days_ago)).strftime("%Y-%m-%d")
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "INSERT INTO ai_reassessment_schedule (project_id, ai_system, frequency, next_due) VALUES (?, ?, ?, ?)",
        ("proj-1", "LateSystem", "quarterly", past_date),
    )
    conn.commit()
    conn.close()

    result = check_overdue("proj-1", db_path=db_path)
    assert result["total_overdue"] == 1
    assert result["overdue"][0]["days_overdue"] == pytest.approx(days_ago, abs=1)


# ============================================================
# complete_reassessment
# ============================================================

def test_complete_reassessment_basic(db_path):
    """Completing a reassessment returns status=completed."""
    create_schedule("proj-1", "System-X", "annual", db_path=db_path)

    # Retrieve the schedule_id
    conn = sqlite3.connect(str(db_path))
    row = conn.execute("SELECT id FROM ai_reassessment_schedule LIMIT 1").fetchone()
    conn.close()
    schedule_id = row[0]

    result = complete_reassessment(schedule_id, db_path=db_path)
    assert result["status"] == "completed"
    assert result["schedule_id"] == schedule_id
    assert result["ai_system"] == "System-X"
    assert "completed_date" in result
    assert "next_due" in result


def test_complete_reassessment_sets_next_due(db_path):
    """After completing a quarterly schedule, next_due is ~90 days out."""
    create_schedule("proj-1", "QSystem", "quarterly", db_path=db_path)

    conn = sqlite3.connect(str(db_path))
    row = conn.execute("SELECT id FROM ai_reassessment_schedule LIMIT 1").fetchone()
    conn.close()
    schedule_id = row[0]

    result = complete_reassessment(schedule_id, db_path=db_path)
    expected = (datetime.now(timezone.utc) + timedelta(days=90)).strftime("%Y-%m-%d")
    assert result["next_due"] == expected


def test_complete_reassessment_not_found(db_path):
    """Completing a nonexistent schedule returns error."""
    result = complete_reassessment(9999, db_path=db_path)
    assert "error" in result


# ============================================================
# get_schedule_summary
# ============================================================

def test_get_schedule_summary_empty(db_path):
    """Empty project returns zero schedules."""
    result = get_schedule_summary("proj-1", db_path=db_path)
    assert result["total_schedules"] == 0
    assert result["overdue_count"] == 0
    assert result["schedules"] == []


def test_get_schedule_summary_populated(db_path):
    """Summary reflects created schedules."""
    create_schedule("proj-1", "SysA", "annual", db_path=db_path)
    create_schedule("proj-1", "SysB", "quarterly", db_path=db_path)

    result = get_schedule_summary("proj-1", db_path=db_path)
    assert result["total_schedules"] == 2
    assert len(result["schedules"]) == 2


def test_get_schedule_summary_overdue_count(db_path):
    """Summary correctly counts overdue schedules."""
    past_date = (datetime.now(timezone.utc) - timedelta(days=10)).strftime("%Y-%m-%d")
    create_schedule("proj-1", "OverdueSys", "annual", next_due=past_date, db_path=db_path)
    create_schedule("proj-1", "OkSys", "annual", next_due="2099-01-01", db_path=db_path)

    result = get_schedule_summary("proj-1", db_path=db_path)
    assert result["total_schedules"] == 2
    assert result["overdue_count"] == 1


# ============================================================
# Constants & edge cases
# ============================================================

def test_all_valid_frequencies(db_path):
    """All VALID_FREQUENCIES can be used to create a schedule."""
    for freq in VALID_FREQUENCIES:
        result = create_schedule("proj-freq", f"System-{freq}", freq, db_path=db_path)
        assert result["status"] == "scheduled"
        assert result["frequency"] == freq


def test_frequency_days_values():
    """FREQUENCY_DAYS dict has expected values."""
    assert FREQUENCY_DAYS["quarterly"] == 90
    assert FREQUENCY_DAYS["semi_annual"] == 182
    assert FREQUENCY_DAYS["annual"] == 365
    assert FREQUENCY_DAYS["biennial"] == 730


def test_multiple_systems_per_project(db_path):
    """Multiple AI systems under one project are tracked independently."""
    create_schedule("proj-1", "Alpha", "quarterly", db_path=db_path)
    create_schedule("proj-1", "Beta", "annual", db_path=db_path)
    create_schedule("proj-1", "Gamma", "biennial", db_path=db_path)

    result = get_schedule_summary("proj-1", db_path=db_path)
    assert result["total_schedules"] == 3
    systems = {s["ai_system"] for s in result["schedules"]}
    assert systems == {"Alpha", "Beta", "Gamma"}

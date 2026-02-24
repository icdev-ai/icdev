"""Tests for tools/compliance/accountability_manager.py — Phase 49 AI Accountability."""

import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(
    0, str(Path(__file__).resolve().parent.parent / "tools" / "compliance")
)
from accountability_manager import (
    VALID_APPEAL_STATUSES,
    VALID_FREQUENCIES,
    VALID_REVIEW_TYPES,
    _ensure_tables,
    designate_caio,
    file_appeal,
    get_accountability_summary,
    register_oversight_plan,
    resolve_appeal,
    schedule_reassessment,
    submit_ethics_review,
)


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    _ensure_tables(c)
    # Also create audit_trail table for audit log tests
    c.execute(
        """CREATE TABLE IF NOT EXISTS audit_trail (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        project_id TEXT, event_type TEXT, actor TEXT, action TEXT,
        details TEXT, classification TEXT, timestamp TEXT
    )"""
    )
    c.commit()
    yield c
    c.close()


# ---------------------------------------------------------------------------
# Table creation
# ---------------------------------------------------------------------------


def test_ensure_tables_creates_all_five(conn):
    """_ensure_tables should create oversight_plans, caio_designations,
    appeals, ethics_reviews, and reassessment_schedules."""
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    ).fetchall()
    table_names = {r["name"] for r in rows}
    expected = {
        "ai_oversight_plans",
        "ai_caio_registry",
        "ai_accountability_appeals",
        "ai_ethics_reviews",
        "ai_reassessment_schedule",
    }
    assert expected.issubset(table_names), f"Missing tables: {expected - table_names}"


# ---------------------------------------------------------------------------
# register_oversight_plan
# ---------------------------------------------------------------------------


def test_register_oversight_plan_basic(conn):
    result = register_oversight_plan(conn, "proj-1", "Plan A")
    assert result["plan_id"]
    assert result["project_id"] == "proj-1"
    assert result["plan_name"] == "Plan A"
    assert result["approval_status"] == "draft"


def test_register_oversight_plan_with_details(conn):
    result = register_oversight_plan(
        conn,
        "proj-2",
        "Plan B",
        description="Detailed plan",
        created_by="admin@mil",
    )
    assert result["project_id"] == "proj-2"
    assert result["plan_name"] == "Plan B"
    assert result["created_by"] == "admin@mil"


def test_multiple_plans_per_project(conn):
    r1 = register_oversight_plan(conn, "proj-1", "Plan A")
    r2 = register_oversight_plan(conn, "proj-1", "Plan B")
    assert r1["plan_id"] != r2["plan_id"]
    rows = conn.execute(
        "SELECT * FROM ai_oversight_plans WHERE project_id='proj-1'"
    ).fetchall()
    assert len(rows) == 2


# ---------------------------------------------------------------------------
# designate_caio
# ---------------------------------------------------------------------------


def test_designate_caio_basic(conn):
    result = designate_caio(conn, "proj-1", "Jane Doe")
    assert result["caio_id"]
    assert result["project_id"] == "proj-1"
    assert result["name"] == "Jane Doe"
    assert result["role"] == "CAIO"
    assert result["status"] == "active"


def test_designate_caio_custom_role(conn):
    result = designate_caio(
        conn, "proj-1", "John Smith", role="AI Ethics Officer", organization="DoD"
    )
    assert result["role"] == "AI Ethics Officer"


def test_multiple_caios_per_project(conn):
    r1 = designate_caio(conn, "proj-1", "Alice")
    r2 = designate_caio(conn, "proj-1", "Bob")
    assert r1["caio_id"] != r2["caio_id"]
    rows = conn.execute(
        "SELECT * FROM ai_caio_registry WHERE project_id='proj-1'"
    ).fetchall()
    assert len(rows) == 2


# ---------------------------------------------------------------------------
# file_appeal / resolve_appeal
# ---------------------------------------------------------------------------


def test_file_appeal_basic(conn):
    result = file_appeal(conn, "proj-1", "User A", "ChatBot v1")
    assert result["appeal_id"]
    assert result["project_id"] == "proj-1"
    assert result["appellant"] == "User A"
    assert result["ai_system"] == "ChatBot v1"
    assert result["status"] == "submitted"


def test_file_appeal_with_grievance(conn):
    result = file_appeal(
        conn, "proj-1", "User B", "ChatBot v2", grievance="Biased output"
    )
    assert result["appeal_id"]
    assert result["status"] == "submitted"


def test_resolve_appeal_basic(conn):
    appeal = file_appeal(conn, "proj-1", "User A", "System X")
    resolved = resolve_appeal(
        conn, appeal["appeal_id"], resolution="Issue corrected", status="resolved"
    )
    assert resolved["status"] == "resolved"


def test_resolve_appeal_dismissed(conn):
    appeal = file_appeal(conn, "proj-1", "User A", "System X")
    resolved = resolve_appeal(
        conn, appeal["appeal_id"], resolution="Not reproducible", status="dismissed"
    )
    assert resolved["status"] == "dismissed"


def test_resolve_appeal_invalid_status(conn):
    appeal = file_appeal(conn, "proj-1", "User A", "System X")
    result = resolve_appeal(
        conn,
        appeal["appeal_id"],
        resolution="Bad",
        status="invalid_status_xyz",
    )
    assert "error" in result


def test_resolve_appeal_not_found(conn):
    result = resolve_appeal(conn, 99999, resolution="N/A", status="resolved")
    assert "error" in result or result.get("status") == "resolved"


# ---------------------------------------------------------------------------
# submit_ethics_review
# ---------------------------------------------------------------------------


def test_submit_ethics_review_basic(conn):
    result = submit_ethics_review(conn, "proj-1", VALID_REVIEW_TYPES[0])
    assert result["review_id"]
    assert result["project_id"] == "proj-1"
    assert result["review_type"] == VALID_REVIEW_TYPES[0]
    assert result["status"] == "submitted"


def test_submit_ethics_review_invalid_type(conn):
    result = submit_ethics_review(conn, "proj-1", "totally_bogus_type")
    assert "error" in result


def test_submit_ethics_review_all_types(conn):
    for rt in VALID_REVIEW_TYPES:
        result = submit_ethics_review(conn, "proj-1", rt, summary=f"Review for {rt}")
        assert result["review_type"] == rt


# ---------------------------------------------------------------------------
# schedule_reassessment
# ---------------------------------------------------------------------------


def test_schedule_reassessment_annual(conn):
    result = schedule_reassessment(conn, "proj-1", "ChatBot v1", frequency="annual")
    assert result["schedule_id"]
    assert result["next_due"]
    assert "frequency" not in result or result.get("frequency") == "annual"


def test_schedule_reassessment_quarterly(conn):
    result = schedule_reassessment(conn, "proj-1", "ChatBot v1", frequency="quarterly")
    assert result["schedule_id"]
    assert result["next_due"]


def test_schedule_reassessment_invalid_frequency(conn):
    result = schedule_reassessment(conn, "proj-1", "ChatBot v1", frequency="every_millisecond")
    assert "error" in result


def test_schedule_reassessment_with_last_assessed(conn):
    result = schedule_reassessment(
        conn, "proj-1", "ChatBot v1", frequency="annual", last_assessed="2025-06-15"
    )
    assert result["schedule_id"]
    assert result["next_due"]


# ---------------------------------------------------------------------------
# get_accountability_summary
# ---------------------------------------------------------------------------


def test_get_accountability_summary_empty(conn):
    summary = get_accountability_summary(conn, "proj-empty")
    assert summary["oversight_plans"] == 0
    assert summary["caio_designations"] == 0
    assert summary["total_appeals"] == 0
    assert summary["ethics_reviews"] == 0
    assert summary["reassessment_schedules"] == 0


def test_get_accountability_summary_populated(conn):
    register_oversight_plan(conn, "proj-1", "Plan A")
    register_oversight_plan(conn, "proj-1", "Plan B")
    designate_caio(conn, "proj-1", "Alice")
    file_appeal(conn, "proj-1", "User A", "System X")
    submit_ethics_review(conn, "proj-1", VALID_REVIEW_TYPES[0])
    schedule_reassessment(conn, "proj-1", "System X", frequency="annual")

    summary = get_accountability_summary(conn, "proj-1")
    assert summary["oversight_plans"] == 2
    assert summary["caio_designations"] == 1
    assert summary["total_appeals"] == 1
    assert summary["ethics_reviews"] == 1
    assert summary["reassessment_schedules"] == 1


# ---------------------------------------------------------------------------
# Audit trail recording
# ---------------------------------------------------------------------------


def test_audit_trail_recorded_on_plan(conn):
    register_oversight_plan(conn, "proj-1", "Plan A", created_by="admin")
    rows = conn.execute(
        "SELECT * FROM audit_trail WHERE project_id='proj-1'"
    ).fetchall()
    # Should have at least one audit entry related to oversight plan registration
    assert len(rows) >= 1


def test_audit_trail_recorded_on_appeal(conn):
    file_appeal(conn, "proj-1", "User A", "System X")
    rows = conn.execute(
        "SELECT * FROM audit_trail WHERE project_id='proj-1'"
    ).fetchall()
    assert len(rows) >= 1


# ---------------------------------------------------------------------------
# Constants validation
# ---------------------------------------------------------------------------


def test_valid_constants_defined():
    assert len(VALID_REVIEW_TYPES) >= 1
    assert len(VALID_FREQUENCIES) >= 2
    assert len(VALID_APPEAL_STATUSES) >= 2
    assert isinstance(VALID_REVIEW_TYPES, (list, tuple, set, frozenset))
    assert isinstance(VALID_FREQUENCIES, (list, tuple, set, frozenset))
    assert isinstance(VALID_APPEAL_STATUSES, (list, tuple, set, frozenset))

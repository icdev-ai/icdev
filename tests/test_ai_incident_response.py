"""Tests for tools/compliance/ai_incident_response.py — Phase 49 AI Incident Response."""

import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(
    0, str(Path(__file__).resolve().parent.parent / "tools" / "compliance")
)
from ai_incident_response import (
    VALID_INCIDENT_TYPES,
    VALID_SEVERITIES,
    VALID_STATUSES,
    get_incident_stats,
    get_open_incidents,
    log_incident,
    update_incident,
)


@pytest.fixture
def db_path(tmp_path):
    db = tmp_path / "test.db"
    conn = sqlite3.connect(str(db))
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS ai_incident_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id TEXT NOT NULL,
            incident_type TEXT NOT NULL,
            ai_system TEXT,
            severity TEXT DEFAULT 'medium',
            description TEXT NOT NULL,
            corrective_action TEXT,
            status TEXT DEFAULT 'open',
            reported_by TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS audit_trail (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id TEXT, event_type TEXT, actor TEXT, action TEXT,
            details TEXT, classification TEXT, timestamp TEXT
        );
    """
    )
    conn.commit()
    conn.close()
    return db


# ---------------------------------------------------------------------------
# log_incident
# ---------------------------------------------------------------------------


def test_log_incident_basic(db_path):
    result = log_incident(
        "proj-1", "confabulation", "Model hallucinated facts", db_path=str(db_path)
    )
    assert result["incident_id"]
    assert result["status"] == "logged" or result["status"] == "open"


def test_log_incident_with_all_fields(db_path):
    result = log_incident(
        "proj-1",
        "bias_detected",
        "Gender bias in recommendations",
        ai_system="RecEngine v2",
        severity="high",
        reported_by="analyst@mil",
        db_path=str(db_path),
    )
    assert result["incident_id"]


def test_log_incident_invalid_type(db_path):
    with pytest.raises(ValueError):
        log_incident(
            "proj-1",
            "totally_invalid_type",
            "Should fail",
            db_path=str(db_path),
        )


def test_log_incident_invalid_severity(db_path):
    with pytest.raises(ValueError):
        log_incident(
            "proj-1",
            "confabulation",
            "Should fail",
            severity="catastrophic",
            db_path=str(db_path),
        )


def test_log_incident_all_valid_types(db_path):
    for itype in VALID_INCIDENT_TYPES:
        result = log_incident(
            "proj-1", itype, f"Test incident for {itype}", db_path=str(db_path)
        )
        assert result["incident_id"]


# ---------------------------------------------------------------------------
# update_incident
# ---------------------------------------------------------------------------


def test_update_incident_corrective_action(db_path):
    incident = log_incident(
        "proj-1", "confabulation", "Hallucination detected", db_path=str(db_path)
    )
    updated = update_incident(
        incident["incident_id"],
        corrective_action="Retrained model",
        db_path=str(db_path),
    )
    assert updated is not None


def test_update_incident_status_change(db_path):
    incident = log_incident(
        "proj-1", "bias_detected", "Bias in output", db_path=str(db_path)
    )
    updated = update_incident(
        incident["incident_id"],
        status="investigating",
        db_path=str(db_path),
    )
    assert updated is not None


def test_update_incident_not_found(db_path):
    result = update_incident(
        99999,
        corrective_action="N/A",
        db_path=str(db_path),
    )
    assert "error" in result


def test_update_incident_invalid_status(db_path):
    incident = log_incident(
        "proj-1", "confabulation", "Test", db_path=str(db_path)
    )
    with pytest.raises(ValueError):
        update_incident(
            incident["incident_id"],
            status="bogus_status",
            db_path=str(db_path),
        )


# ---------------------------------------------------------------------------
# get_open_incidents
# ---------------------------------------------------------------------------


def test_get_open_incidents_empty(db_path):
    result = get_open_incidents("proj-empty", db_path=str(db_path))
    assert result["total"] == 0
    assert result["incidents"] == []


def test_get_open_incidents_with_data(db_path):
    log_incident("proj-1", "confabulation", "Issue 1", db_path=str(db_path))
    log_incident("proj-1", "bias_detected", "Issue 2", db_path=str(db_path))
    result = get_open_incidents("proj-1", db_path=str(db_path))
    assert result["total"] == 2
    assert len(result["incidents"]) == 2


def test_get_open_incidents_severity_filter(db_path):
    log_incident(
        "proj-1", "confabulation", "Low issue", severity="low", db_path=str(db_path)
    )
    log_incident(
        "proj-1",
        "data_breach",
        "Critical issue",
        severity="critical",
        db_path=str(db_path),
    )
    result = get_open_incidents("proj-1", severity="critical", db_path=str(db_path))
    assert result["total"] == 1
    assert len(result["incidents"]) == 1


# ---------------------------------------------------------------------------
# get_incident_stats
# ---------------------------------------------------------------------------


def test_get_incident_stats_empty(db_path):
    stats = get_incident_stats("proj-empty", db_path=str(db_path))
    assert stats["total"] == 0
    assert stats["open"] == 0
    assert stats["critical_unresolved"] == 0
    assert stats["resolved"] == 0


def test_get_incident_stats_populated(db_path):
    log_incident("proj-1", "confabulation", "Issue 1", db_path=str(db_path))
    log_incident(
        "proj-1", "bias_detected", "Issue 2", severity="critical", db_path=str(db_path)
    )
    stats = get_incident_stats("proj-1", db_path=str(db_path))
    assert stats["total"] == 2
    assert stats["open"] >= 2


def test_get_incident_stats_by_type(db_path):
    log_incident("proj-1", "confabulation", "Issue 1", db_path=str(db_path))
    log_incident("proj-1", "confabulation", "Issue 2", db_path=str(db_path))
    log_incident("proj-1", "bias_detected", "Issue 3", db_path=str(db_path))
    stats = get_incident_stats("proj-1", db_path=str(db_path))
    assert stats["by_type"]["confabulation"] == 2
    assert stats["by_type"]["bias_detected"] == 1


def test_critical_unresolved_count(db_path):
    log_incident(
        "proj-1",
        "data_breach",
        "Critical open",
        severity="critical",
        db_path=str(db_path),
    )
    incident2 = log_incident(
        "proj-1",
        "safety_violation",
        "Critical resolved",
        severity="critical",
        db_path=str(db_path),
    )
    update_incident(incident2["incident_id"], status="closed", db_path=str(db_path))

    stats = get_incident_stats("proj-1", db_path=str(db_path))
    assert stats["critical_unresolved"] == 1


def test_resolved_count(db_path):
    inc = log_incident(
        "proj-1", "model_drift", "Drift detected", db_path=str(db_path)
    )
    update_incident(inc["incident_id"], status="resolved", db_path=str(db_path))

    stats = get_incident_stats("proj-1", db_path=str(db_path))
    assert stats["resolved"] >= 1


# ---------------------------------------------------------------------------
# Ordering — critical incidents first
# ---------------------------------------------------------------------------


def test_incident_ordering(db_path):
    log_incident(
        "proj-1", "confabulation", "Low priority", severity="low", db_path=str(db_path)
    )
    log_incident(
        "proj-1",
        "data_breach",
        "Critical priority",
        severity="critical",
        db_path=str(db_path),
    )
    log_incident(
        "proj-1",
        "bias_detected",
        "Medium priority",
        severity="medium",
        db_path=str(db_path),
    )
    result = get_open_incidents("proj-1", db_path=str(db_path))
    severities = [inc.get("severity", "") for inc in result["incidents"]]
    # Critical should appear before low in the returned list
    if "critical" in severities and "low" in severities:
        assert severities.index("critical") < severities.index("low")


# ---------------------------------------------------------------------------
# Project isolation
# ---------------------------------------------------------------------------


def test_multiple_projects_isolated(db_path):
    log_incident("proj-A", "confabulation", "Issue A1", db_path=str(db_path))
    log_incident("proj-A", "bias_detected", "Issue A2", db_path=str(db_path))
    log_incident("proj-B", "model_drift", "Issue B1", db_path=str(db_path))

    stats_a = get_incident_stats("proj-A", db_path=str(db_path))
    stats_b = get_incident_stats("proj-B", db_path=str(db_path))
    assert stats_a["total"] == 2
    assert stats_b["total"] == 1

    open_a = get_open_incidents("proj-A", db_path=str(db_path))
    open_b = get_open_incidents("proj-B", db_path=str(db_path))
    assert open_a["total"] == 2
    assert open_b["total"] == 1

# CUI // SP-CTI
"""nav-bld-02 — AISG PM / Compliance / Skills views serve LIVE data, never canned.

Contract:
  * Empty tables    -> honest empty states; NONE of the removed demo/canned
                       strings (_ATO_SECTIONS / _POAM_ITEMS / _RECOMMENDATIONS and
                       friends) appear anywhere in the payload.
  * Seeded tables   -> real DB values surface.
  * DB failure      -> degraded payload with ``data_source_error`` set (no canned
                       substitute).
  * Pages render 200 with empty states (no canned strings in the HTML).
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

import tools.aisg.pm_view as pm_view
import tools.aisg.compliance_view as compliance_view
import tools.aisg.skills_tracker as skills_tracker

# ---------------------------------------------------------------------------
# Canned strings that USED to be hardcoded — must never resurface as "real" data.
# ---------------------------------------------------------------------------
_CANNED = [
    # pm_view demo sprint tasks / milestones
    "Initialize ICDEV™ project structure",
    "Run AI-driven requirements intake",
    "Deployed first pattern (Document Q&A)",
    "Completed Visual Agent Builder module",
    "Alice M.",
    "Carol T.",
    # compliance_view _POAM_ITEMS
    "POA-001",
    "Baseline config not documented for all components",
    "Vulnerability scan not run on secondary subnet",
    "FIPS 140-2 validation pending for encryption module",
    # skills_tracker _RECOMMENDATIONS
    "Complete the AI Security module in the AI Builder learning track",
    "Deploy the Threat Triage pattern to practice multi-agent coordination",
    "Practice with the AI Launchpad Wizard across 5 different use cases",
]

_DDL = [
    "CREATE TABLE IF NOT EXISTS kanban_tasks (id TEXT PRIMARY KEY, title TEXT, "
    "status TEXT, priority TEXT, task_type TEXT, dispatch_source TEXT, "
    "created_at TEXT, completed_at TEXT)",
    "CREATE TABLE IF NOT EXISTS kanban_executions (id TEXT PRIMARY KEY, status TEXT, created_at TEXT)",
    "CREATE TABLE IF NOT EXISTS aisg_track_progress (id INTEGER PRIMARY KEY AUTOINCREMENT, "
    "user_email TEXT, track_id TEXT, tasks_completed INTEGER, activated_at TEXT, "
    "completed_at TEXT, created_at TEXT)",
    "CREATE TABLE IF NOT EXISTS aisg_learning_tracks (id TEXT PRIMARY KEY, name TEXT, level TEXT)",
    "CREATE TABLE IF NOT EXISTS compliance_controls (id TEXT PRIMARY KEY, family TEXT, title TEXT)",
    "CREATE TABLE IF NOT EXISTS control_narratives (id INTEGER PRIMARY KEY AUTOINCREMENT, "
    "project_id TEXT, control_id TEXT, narrative_text TEXT)",
    "CREATE TABLE IF NOT EXISTS poam_items (id INTEGER PRIMARY KEY AUTOINCREMENT, "
    "project_id TEXT, weakness_id TEXT, weakness_description TEXT, severity TEXT, "
    "source TEXT, control_id TEXT, status TEXT, milestone_date TEXT)",
    "CREATE TABLE IF NOT EXISTS ato_system_registry (id TEXT PRIMARY KEY, project_id TEXT, "
    "system_name TEXT, ato_type TEXT, impact_level TEXT, ato_expiry TEXT, updated_at TEXT)",
    "CREATE TABLE IF NOT EXISTS aisg_skills (id TEXT PRIMARY KEY, name TEXT, skill_type TEXT, "
    "description TEXT, proficiency_levels_json TEXT, gaps_json TEXT, status TEXT)",
    "CREATE TABLE IF NOT EXISTS aisg_roi_events (id INTEGER PRIMARY KEY AUTOINCREMENT, "
    "action_type TEXT, time_saved_minutes NUMERIC, description TEXT, triggered_by TEXT, occurred_at TEXT)",
]

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _assert_no_canned(payload) -> None:
    blob = payload if isinstance(payload, str) else json.dumps(payload, default=str)
    for s in _CANNED:
        assert s not in blob, f"Canned/demo string leaked into output: {s!r}"


@pytest.fixture
def db(tmp_path):
    """Temp SQLite DB (via the storage layer so %s→? translation is exercised)."""
    from tools.db import storage
    path = str(tmp_path / "aisg_nav_bld_02.db")
    conn = storage.get_connection(db_path=path)
    for ddl in _DDL:
        conn.execute(ddl)
    conn.commit()
    conn.close()

    def factory():
        return storage.get_connection(db_path=path)

    return SimpleNamespace(path=path, factory=factory)


def _boom(*_a, **_k):
    raise RuntimeError("simulated database outage")


# ===========================================================================
# pm_view
# ===========================================================================

def test_pm_empty_states(db, monkeypatch):
    monkeypatch.setattr(pm_view, "get_connection", db.factory)
    result = pm_view.get_pm_data()

    assert result["sprint_tasks"] == []
    assert result["velocity_data"] == []
    assert result["learning_milestones"] == []
    assert result["build_pass_rate"] is None
    assert result["test_coverage"] is None
    assert result["data_source_error"] is None
    assert result["total"] == 0
    _assert_no_canned(result)


def test_pm_seeded_returns_real_values(db, monkeypatch):
    conn = db.factory()
    now = _now()
    conn.execute(
        "INSERT INTO kanban_tasks (id,title,status,priority,task_type,dispatch_source,created_at,completed_at) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
        ("t1", "Wire up telemetry exporter", "done", "high", "build", "aisg_wizard", now, now),
    )
    conn.execute(
        "INSERT INTO kanban_executions (id,status,created_at) VALUES (%s,%s,%s)",
        ("e1", "success", now),
    )
    conn.execute(
        "INSERT INTO kanban_executions (id,status,created_at) VALUES (%s,%s,%s)",
        ("e2", "failed", now),
    )
    conn.execute(
        "INSERT INTO aisg_learning_tracks (id,name,level) VALUES (%s,%s,%s)",
        ("trk1", "Builder Fundamentals", "builder"),
    )
    conn.execute(
        "INSERT INTO aisg_track_progress (user_email,track_id,tasks_completed,activated_at,completed_at,created_at) "
        "VALUES (%s,%s,%s,%s,%s,%s)",
        ("dana@unit.test", "trk1", 5, now, now, now),
    )
    conn.commit()
    conn.close()

    monkeypatch.setattr(pm_view, "get_connection", db.factory)
    result = pm_view.get_pm_data()

    assert "Wire up telemetry exporter" in [t["title"] for t in result["sprint_tasks"]]
    assert result["done"] == 1
    assert result["build_pass_rate"] == 50  # 1 pass / 2 finished
    assert result["velocity_data"], "expected non-empty velocity from live tasks"
    assert any("Builder Fundamentals" in m["milestone"] for m in result["learning_milestones"])
    assert any(m["user"] == "dana@unit.test" for m in result["learning_milestones"])
    assert result["data_source_error"] is None
    _assert_no_canned(result)


def test_pm_db_failure_error_payload(monkeypatch):
    monkeypatch.setattr(pm_view, "get_connection", _boom)
    result = pm_view.get_pm_data()

    assert result["data_source_error"]
    assert result["sprint_tasks"] == []
    assert result["velocity_data"] == []
    assert result["learning_milestones"] == []
    _assert_no_canned(result)


# ===========================================================================
# compliance_view
# ===========================================================================

def test_compliance_empty_states(db, monkeypatch):
    monkeypatch.setattr(compliance_view, "get_connection", db.factory)
    result = compliance_view.get_compliance_data()

    assert result["ato_sections"] == []
    assert result["poam_items"] == []
    assert result["compliance_pct"] is None
    assert result["ato_registered"] is False
    assert result["ato_status"] is None
    assert result["impact_level"] is None
    assert result["poam_open"] == 0
    assert result["data_source_error"] is None
    _assert_no_canned(result)


def test_compliance_seeded_returns_real_values(db, monkeypatch):
    conn = db.factory()
    for cid in ("AC-1", "AC-2", "AC-3"):
        conn.execute(
            "INSERT INTO compliance_controls (id,family,title) VALUES (%s,%s,%s)",
            (cid, "AC", f"Control {cid}"),
        )
    conn.execute(
        "INSERT INTO control_narratives (project_id,control_id,narrative_text) VALUES (%s,%s,%s)",
        ("proj", "AC-1", "Implemented via SSO."),
    )
    conn.execute(
        "INSERT INTO poam_items (project_id,weakness_id,weakness_description,severity,source,control_id,status,milestone_date) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
        ("proj", "W-42", "Encrypt data at rest on tenant volumes", "high", "scan", "SC-28", "open", "2027-01-01"),
    )
    conn.execute(
        "INSERT INTO ato_system_registry (id,project_id,system_name,ato_type,impact_level,ato_expiry,updated_at) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s)",
        ("sys1", "proj", "Tenant Portal", "ato", "IL4", "2028-05-01", _now()),
    )
    conn.commit()
    conn.close()

    monkeypatch.setattr(compliance_view, "get_connection", db.factory)
    result = compliance_view.get_compliance_data()

    ac = next(s for s in result["ato_sections"] if s["id"] == "ac")
    assert ac["controls"] == 3
    assert ac["implemented"] == 1
    assert ac["status"] == "yellow"
    assert result["total_controls"] == 3
    assert result["total_implemented"] == 1
    assert result["compliance_pct"] == 33
    assert result["poam_open"] == 1
    assert any(p["weakness"] == "Encrypt data at rest on tenant volumes" for p in result["poam_items"])
    assert result["ato_registered"] is True
    assert result["impact_level"] == "IL4"
    assert result["ato_status"] == "ATO"
    _assert_no_canned(result)


def test_compliance_db_failure_error_payload(monkeypatch):
    monkeypatch.setattr(compliance_view, "get_connection", _boom)
    result = compliance_view.get_compliance_data()

    assert result["data_source_error"]
    assert result["ato_sections"] == []
    assert result["poam_items"] == []
    assert result["compliance_pct"] is None
    _assert_no_canned(result)


# ===========================================================================
# skills_tracker
# ===========================================================================

def test_skills_empty_states(db, monkeypatch):
    monkeypatch.setattr(skills_tracker, "get_connection", db.factory)
    result = skills_tracker.get_skills_data()

    assert result["skill_domains"] == []
    assert result["recommendations"] == []
    assert result["team_members"] == []
    assert result["maturity_score"] is None
    assert result["maturity_label"] == "Not assessed"
    assert result["total_skills"] == 0
    assert result["data_source_error"] is None
    _assert_no_canned(result)


def test_skills_seeded_returns_real_values(db, monkeypatch):
    conn = db.factory()
    conn.execute(
        "INSERT INTO aisg_skills (id,name,skill_type,description,proficiency_levels_json,gaps_json,status) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s)",
        ("s1", "Threat Modeling", "security", "STRIDE / attack trees",
         '{"current": 2, "target": 4}', "[]", "active"),
    )
    conn.execute(
        "INSERT INTO aisg_roi_events (action_type,time_saved_minutes,description,triggered_by,occurred_at) "
        "VALUES (%s,%s,%s,%s,%s)",
        ("security_scan", 30, "SAST run", "priya@unit.test", _now()),
    )
    conn.commit()
    conn.close()

    monkeypatch.setattr(skills_tracker, "get_connection", db.factory)
    result = skills_tracker.get_skills_data()

    dom = next(s for s in result["skill_domains"] if s["name"] == "Threat Modeling")
    assert dom["current_level"] == 2
    assert dom["target_level"] == 4
    assert dom["gap"] == 2
    assert result["maturity_score"] == 2
    assert result["maturity_label"] == "Piloting"
    assert result["recommendations"], "expected a real gap-derived recommendation"
    assert any("Threat Modeling" in r["skill"] for r in result["recommendations"])
    assert any(m["name"] == "priya@unit.test" for m in result["team_members"])
    _assert_no_canned(result)


def test_skills_db_failure_error_payload(monkeypatch):
    monkeypatch.setattr(skills_tracker, "get_connection", _boom)
    result = skills_tracker.get_skills_data()

    assert result["data_source_error"]
    assert result["skill_domains"] == []
    assert result["recommendations"] == []
    assert result["team_members"] == []
    _assert_no_canned(result)


# ===========================================================================
# Structural — the canned constants are GONE from the modules.
# ===========================================================================

def test_canned_constants_removed():
    assert not hasattr(compliance_view, "_ATO_SECTIONS")
    assert not hasattr(compliance_view, "_POAM_ITEMS")
    assert not hasattr(skills_tracker, "_RECOMMENDATIONS")
    assert not hasattr(skills_tracker, "_SKILL_DOMAINS")

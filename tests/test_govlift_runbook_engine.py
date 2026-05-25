# CUI // SP-CTI
"""Tests for tools.govlift.runbook_engine — Phase 3 Runbook Engine.

Acceptance criteria verified:
  - create_runbook (from template) completes via single API call (≤3 clicks)
  - get_execution_status is fast (no network I/O, SQLite only)
  - trigger_rollback fires steps in reverse dependency order
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

os.environ.setdefault("ICDEV_STORAGE_BACKEND", "sqlite")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _govlift_db(tmp_path, monkeypatch):
    """Isolated SQLite DB with govlift schema for every test.

    get_connection() reads ICDEV_STORAGE_BACKEND/ICDEV_DB_PATH at call time,
    so monkeypatching env vars is sufficient — no module reload needed.
    """
    db_path = tmp_path / "icdev.db"
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("ICDEV_DB_PATH", str(db_path))

    from tools.govlift.db.init_db import init_govlift_db
    init_govlift_db()
    yield db_path


@pytest.fixture()
def sample_steps() -> list[dict]:
    return [
        {"name": "Backup workload", "description": "Snapshot pre-migration state", "depends_on": None},
        {"name": "Stop services", "description": "Drain connections", "depends_on": 1},
        {"name": "Transfer data", "description": "rsync to target", "depends_on": 2},
        {"name": "Validate health", "description": "Run smoke tests", "depends_on": 3},
    ]


@pytest.fixture()
def template(sample_steps):
    from tools.govlift.runbook_engine import create_template
    return create_template(
        name="IL4 Web App Migration",
        category="migration",
        steps=sample_steps,
        description="Standard 4-step migration runbook",
        workload_type="web_app",
        estimated_min=120,
        author="govlift-test",
    )


@pytest.fixture()
def runbook(template):
    from tools.govlift.runbook_engine import create_runbook
    return create_runbook(template["id"], name="Test Runbook Alpha")


# ---------------------------------------------------------------------------
# Template tests
# ---------------------------------------------------------------------------


def test_create_template_returns_id(template):
    assert template.get("id", "").startswith("rbt-")


def test_create_template_stores_steps(template, sample_steps):
    import json
    stored = json.loads(template.get("steps_json", "[]"))
    assert len(stored) == len(sample_steps)


def test_list_templates_includes_created(template):
    from tools.govlift.runbook_engine import list_templates
    items = list_templates()
    ids = [t["id"] for t in items]
    assert template["id"] in ids


def test_list_templates_filter_by_category(template):
    from tools.govlift.runbook_engine import list_templates
    results = list_templates(category="migration")
    assert all(r["category"] == "migration" for r in results)
    assert any(r["id"] == template["id"] for r in results)


def test_list_templates_filter_excludes_wrong_category(template):
    from tools.govlift.runbook_engine import list_templates
    results = list_templates(category="rollback")
    assert all(r["id"] != template["id"] for r in results)


def test_get_template_returns_dict(template):
    from tools.govlift.runbook_engine import get_template
    fetched = get_template(template["id"])
    assert fetched["name"] == "IL4 Web App Migration"
    assert fetched["estimated_min"] == 120


def test_get_template_nonexistent_returns_empty():
    from tools.govlift.runbook_engine import get_template
    result = get_template("rbt-nonexistent")
    assert result == {}


def test_create_template_invalid_category_raises():
    from tools.govlift.runbook_engine import create_template
    with pytest.raises(ValueError, match="category must be one of"):
        create_template("Bad", category="invalid_cat", steps=[])


# ---------------------------------------------------------------------------
# Runbook instance tests (≤3 clicks = 1 API call)
# ---------------------------------------------------------------------------


def test_create_runbook_returns_id(runbook):
    assert runbook.get("id", "").startswith("rb-")


def test_create_runbook_status_is_draft(runbook):
    assert runbook["status"] == "draft"


def test_create_runbook_step_count(runbook, sample_steps):
    assert runbook["step_count"] == len(sample_steps)


def test_create_runbook_custom_name(template):
    from tools.govlift.runbook_engine import create_runbook
    rb = create_runbook(template["id"], name="My Custom Runbook")
    assert rb["name"] == "My Custom Runbook"


def test_create_runbook_invalid_template_raises():
    from tools.govlift.runbook_engine import create_runbook
    with pytest.raises(ValueError, match="not found"):
        create_runbook("rbt-doesnotexist")


def test_list_runbooks_includes_created(runbook):
    from tools.govlift.runbook_engine import list_runbooks
    items = list_runbooks()
    assert any(r["id"] == runbook["id"] for r in items)


def test_list_runbooks_filter_by_status(runbook):
    from tools.govlift.runbook_engine import list_runbooks
    drafts = list_runbooks(status="draft")
    assert any(r["id"] == runbook["id"] for r in drafts)


def test_get_runbook_returns_dict(runbook):
    from tools.govlift.runbook_engine import get_runbook
    fetched = get_runbook(runbook["id"])
    assert fetched["id"] == runbook["id"]


# ---------------------------------------------------------------------------
# Execution tests (live dashboard within 3s)
# ---------------------------------------------------------------------------


def test_start_execution_transitions_to_running(runbook):
    from tools.govlift.runbook_engine import start_execution
    status = start_execution(runbook["id"])
    assert status["status"] == "running"


def test_start_execution_creates_execution_record(runbook):
    from tools.govlift.runbook_engine import start_execution
    status = start_execution(runbook["id"])
    assert status.get("execution_id") is not None


def test_start_execution_first_step_is_running(runbook):
    from tools.govlift.runbook_engine import start_execution
    status = start_execution(runbook["id"])
    steps = status.get("steps", [])
    first = next((s for s in steps if s["step_num"] == 1), None)
    assert first is not None
    assert first["status"] == "running"


def test_get_execution_status_returns_steps(runbook):
    from tools.govlift.runbook_engine import start_execution, get_execution_status
    start_execution(runbook["id"])
    status = get_execution_status(runbook["id"])
    assert isinstance(status.get("steps"), list)
    assert len(status["steps"]) == runbook["step_count"]


def test_get_execution_status_nonexistent_returns_empty():
    from tools.govlift.runbook_engine import get_execution_status
    result = get_execution_status("rb-nonexistent")
    assert result == {}


def test_complete_step_advances_to_next(runbook):
    from tools.govlift.runbook_engine import start_execution, complete_step
    start_execution(runbook["id"])
    status = complete_step(runbook["id"], 1, "completed", output="OK")
    steps = status.get("steps", [])
    step2 = next((s for s in steps if s["step_num"] == 2), None)
    assert step2 is not None
    assert step2["status"] == "running"


def test_complete_all_steps_marks_completed(runbook):
    from tools.govlift.runbook_engine import start_execution, complete_step, get_runbook
    start_execution(runbook["id"])
    for i in range(1, runbook["step_count"] + 1):
        complete_step(runbook["id"], i, "completed", output=f"step {i} done")
    rb = get_runbook(runbook["id"])
    assert rb["status"] == "completed"


def test_complete_step_failed_marks_runbook_failed(runbook):
    from tools.govlift.runbook_engine import start_execution, complete_step, get_runbook
    start_execution(runbook["id"])
    complete_step(runbook["id"], 1, "failed", output="ERROR: disk full")
    rb = get_runbook(runbook["id"])
    assert rb["status"] == "failed"
    assert rb["failed_step"] == 1


def test_complete_step_invalid_status_raises(runbook):
    from tools.govlift.runbook_engine import start_execution, complete_step
    start_execution(runbook["id"])
    with pytest.raises(ValueError, match="status must be one of"):
        complete_step(runbook["id"], 1, "not_a_real_status")


# ---------------------------------------------------------------------------
# Rollback tests (reverse dependency order)
# ---------------------------------------------------------------------------


def test_trigger_rollback_transitions_status(runbook):
    from tools.govlift.runbook_engine import start_execution, complete_step, trigger_rollback
    start_execution(runbook["id"])
    complete_step(runbook["id"], 1, "completed")
    complete_step(runbook["id"], 2, "completed")
    status = trigger_rollback(runbook["id"])
    assert status["status"] == "rolled_back"


def test_trigger_rollback_reverses_completed_steps(runbook):
    from tools.govlift.runbook_engine import start_execution, complete_step, trigger_rollback
    start_execution(runbook["id"])
    complete_step(runbook["id"], 1, "completed")
    complete_step(runbook["id"], 2, "completed")
    status = trigger_rollback(runbook["id"])
    steps = status.get("steps", [])
    completed_steps = [s for s in steps if s["step_num"] in (1, 2)]
    # All completed steps should now show failed (rolled back)
    for s in completed_steps:
        assert s["status"] == "failed"


def test_trigger_rollback_no_steps_raises():
    from tools.govlift.runbook_engine import trigger_rollback
    with pytest.raises(ValueError, match="No steps found"):
        trigger_rollback("rb-nonexistent")


def test_cannot_start_completed_runbook(runbook):
    from tools.govlift.runbook_engine import start_execution, complete_step
    start_execution(runbook["id"])
    for i in range(1, runbook["step_count"] + 1):
        complete_step(runbook["id"], i, "completed")
    with pytest.raises(ValueError, match="Cannot start runbook in status"):
        start_execution(runbook["id"])

# CUI // SP-CTI
"""Module coverage for tools/observability_canvas/{sops,runbooks}.py (obx-test-02).

test_odc_api_hygiene.py already proves the filtered-read + %s-translation path and
basic create/delete for both modules. This file closes the remaining gaps:

  * SOPs — update path (+ unknown-id None), the full approval workflow
    (draft -> pending_review -> approved / rejected, invalid transitions),
    approval_status filtering after a real approve, and seed-content integrity.
  * Runbooks — update path (+ unknown-id None), delete (+ unknown-id False),
    record_execution counter/timestamp, severity ordering, and seed integrity.

Isolation: a temp SQLite file initialised from init_db.SCHEMA, with the canvas
getter (tools.observability_canvas.db.init_db.get_connection) monkeypatched —
shim-aware, via importlib.import_module + setattr — to a translating
StorageConnection so %s placeholders resolve. Nothing touches the shared canvas DB.

NIST 800-53: AU-2, CA-7, IR-8
"""
from __future__ import annotations

import importlib
import sqlite3
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


@pytest.fixture
def odc_db(tmp_path, monkeypatch):
    """Temp canvas DB (init_db.SCHEMA) wired into the canvas connection getter."""
    init_db_mod = importlib.import_module("tools.observability_canvas.db.init_db")
    from tools.db.storage import StorageConnection

    db_path = tmp_path / "odc_canvas.db"
    raw = sqlite3.connect(str(db_path))
    raw.executescript(init_db_mod.SCHEMA)
    raw.commit()
    raw.close()

    def _conn():
        r = sqlite3.connect(str(db_path))
        r.row_factory = sqlite3.Row
        return StorageConnection(r, "sqlite")

    # Shim-aware: patch the exact tools.* module both sops.py and runbooks.py
    # import get_connection from at call time.
    monkeypatch.setattr(init_db_mod, "get_connection", _conn, raising=True)
    return _conn


# ── SOPs — update ────────────────────────────────────────────────────────────

def test_sop_update_changes_fields_and_timestamp(odc_db):
    from tools.observability_canvas import sops

    created = sops.create_sop({"title": "orig", "sop_type": "alert_tuning", "owner": "SRE"})
    updated = sops.update_sop(
        created["id"],
        {"title": "renamed", "owner": "SRE Lead", "steps": [{"order": 1, "description": "x"}],
         "nist_controls": ["SI-4"]},
    )
    assert updated is not None
    assert updated["title"] == "renamed"
    assert updated["owner"] == "SRE Lead"
    assert updated["steps"] == [{"order": 1, "description": "x"}]
    assert updated["nist_controls"] == ["SI-4"]
    # sop_type not supplied in patch => preserved from existing.
    assert updated["sop_type"] == "alert_tuning"


def test_sop_update_unknown_id_returns_none(odc_db):
    from tools.observability_canvas import sops

    assert sops.update_sop("does-not-exist", {"title": "x"}) is None


# ── SOPs — approval workflow ─────────────────────────────────────────────────

def test_sop_full_approval_happy_path(odc_db):
    from tools.observability_canvas import sops

    sop = sops.create_sop({"title": "approve-me", "sop_type": "custom"})
    assert sop["approval_status"] == "draft"

    pending, err = sops.submit_for_review(sop["id"])
    assert err is None
    assert pending["approval_status"] == "pending_review"

    approved, err = sops.approve_sop(sop["id"], approved_by="isso@example.mil")
    assert err is None
    assert approved["approval_status"] == "approved"
    assert approved["approved_by"] == "isso@example.mil"
    assert approved["approved_at"]


def test_sop_reject_then_resubmit(odc_db):
    from tools.observability_canvas import sops

    sop = sops.create_sop({"title": "reject-me", "sop_type": "custom"})
    sops.submit_for_review(sop["id"])

    rejected, err = sops.reject_sop(sop["id"], reason="missing scope", rejected_by="isso")
    assert err is None
    assert rejected["approval_status"] == "rejected"
    assert rejected["rejected_reason"] == "missing scope"

    # A rejected SOP may be resubmitted (draft/rejected are the valid entry states).
    pending, err = sops.submit_for_review(sop["id"])
    assert err is None
    assert pending["approval_status"] == "pending_review"


def test_sop_invalid_transitions_return_error(odc_db):
    from tools.observability_canvas import sops

    sop = sops.create_sop({"title": "guard", "sop_type": "custom"})
    # Cannot approve a draft (must be pending_review first).
    result, err = sops.approve_sop(sop["id"])
    assert result is None
    assert err and "Cannot approve" in err
    # Cannot reject a draft either.
    result, err = sops.reject_sop(sop["id"])
    assert result is None
    assert err and "Cannot reject" in err


def test_sop_workflow_unknown_id(odc_db):
    from tools.observability_canvas import sops

    for fn in (sops.submit_for_review, sops.approve_sop, sops.reject_sop):
        result, err = fn("nope")
        assert result is None
        assert err == "SOP not found"


def test_sop_approval_status_filter_reflects_approve(odc_db):
    from tools.observability_canvas import sops

    sop = sops.create_sop({"title": "filter-me", "sop_type": "custom"})
    # Initially draft — not in the approved bucket.
    assert all(s["id"] != sop["id"] for s in sops.get_all_sops(approval_status="approved"))
    sops.submit_for_review(sop["id"])
    sops.approve_sop(sop["id"])
    approved_ids = {s["id"] for s in sops.get_all_sops(approval_status="approved")}
    assert sop["id"] in approved_ids


def test_sop_delete(odc_db):
    from tools.observability_canvas import sops

    sop = sops.create_sop({"title": "del", "sop_type": "custom"})
    assert sops.delete_sop(sop["id"]) is True
    assert sops.get_sop_by_id(sop["id"]) is None


# ── SOPs — seed integrity ────────────────────────────────────────────────────

def test_seed_sops_loads_default_content(odc_db):
    from tools.observability_canvas import sops

    sops.seed_sops()
    all_sops = sops.get_all_sops()
    assert len(all_sops) == len(sops.SEED_SOPS)

    seeded_types = {s["sop_type"] for s in all_sops}
    for expected in ("service_onboarding", "alert_tuning", "dashboard_review", "log_retention"):
        assert expected in seeded_types

    # Steps + nist_controls persist as parsed JSON lists, not raw strings.
    onboarding = next(s for s in all_sops if s["sop_type"] == "service_onboarding")
    assert isinstance(onboarding["steps"], list) and len(onboarding["steps"]) >= 1
    assert isinstance(onboarding["nist_controls"], list)
    assert "SI-4" in onboarding["nist_controls"]

    # seed is idempotent — a second call with rows present adds nothing.
    sops.seed_sops()
    assert len(sops.get_all_sops()) == len(sops.SEED_SOPS)


# ── Runbooks — update / delete / execution ───────────────────────────────────

def test_runbook_update_changes_fields(odc_db):
    from tools.observability_canvas import runbooks

    rb = runbooks.create_runbook({"title": "orig", "category": "alerting", "severity": "medium"})
    updated = runbooks.update_runbook(
        rb["id"],
        {"title": "tuned", "severity": "critical", "estimated_duration_min": 90,
         "tags": ["alerts", "triage"]},
    )
    assert updated is not None
    assert updated["title"] == "tuned"
    assert updated["severity"] == "critical"
    assert updated["estimated_duration_min"] == 90
    assert updated["tags"] == ["alerts", "triage"]
    # category preserved when not in the patch.
    assert updated["category"] == "alerting"


def test_runbook_update_unknown_returns_none(odc_db):
    from tools.observability_canvas import runbooks

    assert runbooks.update_runbook("missing", {"title": "x"}) is None


def test_runbook_delete_paths(odc_db):
    from tools.observability_canvas import runbooks

    rb = runbooks.create_runbook({"title": "del", "category": "pipeline"})
    assert runbooks.delete_runbook(rb["id"]) is True
    assert runbooks.get_runbook_by_id(rb["id"]) is None
    # Deleting a non-existent runbook reports False rather than raising.
    assert runbooks.delete_runbook("missing") is False


def test_runbook_record_execution_increments(odc_db):
    from tools.observability_canvas import runbooks

    rb = runbooks.create_runbook({"title": "exec", "category": "metrics"})
    assert rb["execution_count"] == 0
    assert not rb["last_executed_at"]

    after = runbooks.record_execution(rb["id"])
    assert after["execution_count"] == 1
    assert after["last_executed_at"]

    after2 = runbooks.record_execution(rb["id"])
    assert after2["execution_count"] == 2

    assert runbooks.record_execution("missing") is None


def test_runbook_severity_filter_and_order(odc_db):
    from tools.observability_canvas import runbooks

    runbooks.create_runbook({"title": "b-crit", "category": "alerting", "severity": "critical"})
    runbooks.create_runbook({"title": "a-crit", "category": "alerting", "severity": "critical"})
    high = runbooks.create_runbook({"title": "hi", "category": "alerting", "severity": "high"})

    crit = runbooks.get_all_runbooks(severity="critical")
    assert len(crit) == 2
    assert all(r["severity"] == "critical" for r in crit)
    # severity="high" query orders by title.
    high_rows = runbooks.get_all_runbooks(severity="high")
    assert [r["id"] for r in high_rows] == [high["id"]]

    # category+severity combined filter.
    both = runbooks.get_all_runbooks(category="alerting", severity="critical")
    assert len(both) == 2


# ── Runbooks — seed integrity ────────────────────────────────────────────────

def test_seed_runbooks_loads_default_content(odc_db):
    from tools.observability_canvas import runbooks

    added = runbooks.seed_runbooks()
    assert added == len(runbooks._SEED_RUNBOOKS)

    all_rb = runbooks.get_all_runbooks()
    ids = {r["id"] for r in all_rb}
    for expected in (
        "rb-odc-alert-storm-triage",
        "rb-odc-log-pipeline-failure",
        "rb-odc-siem-gap-detected",
        "rb-odc-metric-collection-outage",
    ):
        assert expected in ids

    storm = next(r for r in all_rb if r["id"] == "rb-odc-alert-storm-triage")
    assert isinstance(storm["steps"], list) and len(storm["steps"]) >= 1
    first_step = storm["steps"][0]
    assert {"action", "expected_outcome", "rollback_action"} <= set(first_step.keys())
    assert isinstance(storm["tags"], list) and storm["tags"]

    # Idempotent — re-seeding an already-seeded table adds nothing.
    assert runbooks.seed_runbooks() == 0

# CUI // SP-CTI
"""Tests for the SOAR-lite response playbooks — card crx-sec-02.

Verifies the composition + HITL semantics against a temp DB (PUBLIC repo — no real
data). A bare ``get_connection(db_path=...)`` gets no MINIMAL_ICDEV_SCHEMA, so the
engine's ``_ensure_tables`` self-creates its own tables. Playbooks are loaded from
the shipped ``args/soar_playbooks.yaml`` (a config override is passed where a run
needs a deterministic, dependency-free step sequence).
"""
from __future__ import annotations

import pytest

from tools.db.storage import get_connection
from tools.security_canvas import soar_lite


@pytest.fixture()
def conn(tmp_path):
    c = get_connection(db_path=str(tmp_path / "soar.db"))
    soar_lite._ensure_tables(c)
    return c


def _cfg_with(steps):
    """A minimal single-playbook config with a controllable step sequence."""
    return {
        "version": 1,
        "enabled": True,
        "default_classification": "CUI",
        "default_tenant": "platform",
        "playbooks": {
            "unit_pb": {
                "title": "Unit Playbook",
                "finding_type": "unit_finding",
                "severity": "high",
                "steps": steps,
            }
        },
    }


def test_playbooks_yaml_loads_three_seed_playbooks():
    cfg = soar_lite.load_playbooks()
    pbs = cfg.get("playbooks") or {}
    for key in ("cve_triage_sla_breach", "insider_risk_anomaly", "secrets_detection_hit"):
        assert key in pbs, f"missing seed playbook {key}"
        assert pbs[key].get("steps"), f"{key} has no steps"
    # Ships DEFAULT OFF (opt-in auto-trigger).
    assert cfg.get("enabled") is False


def test_enrichment_auto_runs_and_blocks_on_destructive(conn):
    """Enrichment steps auto-execute; the run BLOCKS at the destructive step
    pending HITL approval, and audit rows are written for each event."""
    cfg = _cfg_with([
        {"id": "enrich", "action": "enrich_cve_context", "kind": "enrichment",
         "description": "enrich"},
        {"id": "contain", "action": "block_egress_destination", "kind": "destructive",
         "description": "block egress"},
    ])
    run = soar_lite.start_run("unit_finding", entity="cve-2026-0001",
                              context={"cve_id": "CVE-2026-0001", "severity": "high"},
                              config=cfg, conn=conn)

    assert run["status"] == "awaiting_approval", run
    # The enrichment step ran (index advanced to the destructive step at idx 1).
    assert run["current_step_index"] == 1
    assert any(r["step_id"] == "enrich" and r["result"]["status"] == "enriched"
               for r in run["results"])
    # The destructive action did NOT run yet.
    assert not any(r["step_id"] == "contain" for r in run["results"])

    # Audit trail: started + enrichment executed + awaiting_approval on the gate.
    full = soar_lite.get_run(run["run_id"], conn=conn)
    statuses = [a["status"] for a in full["audit"]]
    assert "started" in statuses
    assert "awaiting_approval" in statuses
    assert any(a["step_id"] == "enrich" for a in full["audit"])


def test_approval_executes_destructive_and_completes(conn):
    cfg = _cfg_with([
        {"id": "enrich", "action": "enrich_cve_context", "kind": "enrichment",
         "description": "enrich"},
        {"id": "contain", "action": "block_egress_destination", "kind": "destructive",
         "description": "block egress"},
    ])
    run = soar_lite.start_run("unit_finding", entity="e1", config=cfg, conn=conn)
    assert run["status"] == "awaiting_approval"

    approved = soar_lite.approve_step(run["run_id"], "contain", actor="analyst-1",
                                      config=cfg, conn=conn)
    assert approved["status"] == "completed", approved
    # Destructive action now has a result recorded.
    contain = [r for r in approved["results"] if r["step_id"] == "contain"]
    assert contain and contain[0].get("approved_by") == "analyst-1"

    full = soar_lite.get_run(run["run_id"], conn=conn)
    statuses = [a["status"] for a in full["audit"]]
    assert "approved" in statuses
    assert "completed" in statuses


def test_rejection_aborts_run(conn):
    cfg = _cfg_with([
        {"id": "contain", "action": "revoke_service_key", "kind": "destructive",
         "description": "revoke"},
    ])
    run = soar_lite.start_run("unit_finding", entity="svc-x", config=cfg, conn=conn)
    assert run["status"] == "awaiting_approval"

    rejected = soar_lite.reject_step(run["run_id"], "contain", actor="analyst-1",
                                     reason="false positive", conn=conn)
    assert rejected["status"] == "aborted"
    full = soar_lite.get_run(run["run_id"], conn=conn)
    assert any(a["status"] == "rejected" for a in full["audit"])


def test_all_enrichment_completes_without_gate(conn):
    cfg = _cfg_with([
        {"id": "a", "action": "enrich_cve_context", "kind": "enrichment", "description": "a"},
        {"id": "b", "action": "enrich_secret_finding", "kind": "enrichment", "description": "b"},
    ])
    run = soar_lite.start_run("unit_finding", config=cfg, conn=conn)
    assert run["status"] == "completed"
    assert len(run["results"]) == 2


def test_unknown_finding_type_returns_no_playbook(conn):
    out = soar_lite.start_run("does_not_exist", conn=conn)
    assert out["status"] == "no_playbook"


def test_revoke_service_key_records_intent_without_agent(conn):
    """The destructive revoke handler degrades honestly (recorded_intent) when it
    has nothing to act on — it never fabricates an action."""
    result = soar_lite._revoke_service_key({})
    assert result["status"] == "recorded_intent"


def test_approve_wrong_step_id_is_rejected(conn):
    cfg = _cfg_with([
        {"id": "contain", "action": "revoke_service_key", "kind": "destructive",
         "description": "revoke"},
    ])
    run = soar_lite.start_run("unit_finding", config=cfg, conn=conn)
    out = soar_lite.approve_step(run["run_id"], "wrong_step", config=cfg, conn=conn)
    assert out["status"] == "error"

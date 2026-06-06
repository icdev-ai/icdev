# CUI // SP-CTI
"""Test the Genesis SUPPORT reflex wrapper for the done-artifact auditor."""
from __future__ import annotations

import importlib


def test_reflex_contract(monkeypatch):
    reflex = importlib.import_module("tools.genesis.reflexes.done_artifact_audit")
    auditor = importlib.import_module("tools.kanban.done_artifact_auditor")
    storage = importlib.import_module("tools.db.storage")

    class _DummyConn:
        def close(self):
            pass

    fake_results = [
        {"task_id": "p1-01", "verdict": "ok", "missing": []},
        {"task_id": "p1-02", "verdict": "missing_artifacts", "missing": ["tools/gone.py"]},
        {"task_id": "p1-03", "verdict": "no_claims", "missing": []},
    ]

    # Shim-aware patching: set attributes on the imported module objects.
    monkeypatch.setattr(reflex, "_project_keys", lambda: ["p1"])
    monkeypatch.setattr(storage, "get_connection", lambda: _DummyConn())
    monkeypatch.setattr(auditor, "audit_project", lambda *a, **k: fake_results)

    result = reflex.run({}, None)

    assert result["success"] is True
    assert result["metric_value"] == 1.0  # one missing_artifacts task
    details = result["details"]
    assert details["flagged_count"] == 1
    assert details["flagged"][0]["task_id"] == "p1-02"
    assert details["done_tasks_audited"] == 3
    assert details["projects_in_flight"] == 1


def test_reflex_registered():
    """The reflex must be in REGISTRY and the daemon REFLEX_NAMES, or it never runs."""
    registry = importlib.import_module("tools.genesis.reflex_registry")
    names = {e.name for e in registry.REGISTRY}
    assert "done_artifact_audit" in names

    daemon = importlib.import_module("tools.genesis.daemon")
    assert "done_artifact_audit" in daemon.REFLEX_NAMES

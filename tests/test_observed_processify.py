# CUI // SP-CTI
"""rea-pif-01: Process-Ify from an observed REA scenario, opt-in, draft for HITL review.

No Node, no REA, no Ghidra: the registry is a fake that replays an REA-shaped
evidence record, and the database is a throwaway SQLite file.
"""
from __future__ import annotations

import importlib
import json
import sqlite3
import sys
from pathlib import Path

import pytest
import yaml

op = importlib.import_module("tools.workflow_canvas.observed_processify")
reg = importlib.import_module("tools.mcp_client.registry")

REPO = Path(__file__).resolve().parents[1]

EVIDENCE = {
    "evidence_id": "ev-proc-001",
    "operation": "capture_process_scenario",
    "confidence": "observed",
    "normalized_result": {
        "events": [
            {"at_ms": 0, "type": "launch", "command": "tool.exe --init"},
            {"at_ms": 120, "type": "input", "data": "open ledger.csv"},
            {"at_ms": 300, "type": "input", "data": "hunter2", "sensitive": True},
            {"at_ms": 450, "type": "output", "text": "3 rows reconciled"},
            "not-a-step",
        ],
        "exit": {"code": 0},
        "residual_unknowns": ["child processes not enumerated"],
    },
    "limitations": ["PTY capture only"],
}


class _Tool:
    def __init__(self, name):
        self.server, self.remote_name = "rea", name


class FakeRegistry:
    """Records every call; offers the process capture tool."""

    def __init__(self, offered=("capture_process_scenario",), body=EVIDENCE):
        self.offered, self.body = offered, body
        self.grants, self.calls, self.closed = [], [], False

    def discover(self):
        return [_Tool(n) for n in self.offered]

    def grant_target(self, server, target, **kw):
        self.grants.append((server, target, kw["basis"]))

    def call(self, name, args, classification="CUI"):
        self.calls.append((name, args))
        return {"ok": True, "result": {"content": [{"type": "text", "text": json.dumps(self.body)}]}}

    def close(self):
        self.closed = True


@pytest.fixture
def exe(tmp_path):
    p = tmp_path / "tool.exe"
    p.write_bytes(b"MZ-fake-binary")
    return p


@pytest.fixture
def db(tmp_path, monkeypatch):
    path = tmp_path / "wfc.db"
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE studio_workflows (workflow_id TEXT PRIMARY KEY, name TEXT NOT NULL, "
        "description TEXT, template_yaml TEXT NOT NULL, category TEXT, created_by TEXT, "
        "created_at TEXT, updated_at TEXT, version INTEGER DEFAULT 1, shared INTEGER DEFAULT 0, "
        "classification TEXT NOT NULL DEFAULT 'CUI', tenant_id TEXT)")
    conn.commit()
    conn.close()

    storage = importlib.import_module("tools.db.storage")

    def _connect(*a, **k):
        c = sqlite3.connect(path)
        c.row_factory = sqlite3.Row
        return storage.StorageConnection(c, "sqlite")

    monkeypatch.setattr(storage, "get_connection", _connect)
    monkeypatch.setattr(op, "_audit", lambda *a, **k: None)
    return _connect


def _capture(exe, **kw):
    fake = kw.pop("fake", None) or FakeRegistry()
    out = op.capture_scenario("process", {"executable": str(exe), "events": [
        {"at_ms": 300, "type": "input", "data": "hunter2", "sensitive": True}],
        "environment": {"API_KEY": "secret"}},
        registry_factory=lambda prefix: fake, **kw)
    return out, fake


# -- the per-run opt-in --------------------------------------------------------

@pytest.mark.parametrize("value", [False, None, "true", 1, "yes"])
def test_no_capture_without_allow_execute_true(exe, value):
    called = []
    out = op.capture_scenario("process", {"executable": str(exe)}, allow_execute=value,
                              authorization_basis="owned",
                              registry_factory=lambda p: called.append(p))
    assert out["status"] == "error" and out["reason"] == "allow_execute_required"
    assert called == [], "no registry may be built before the opt-in"


def test_no_capture_without_authorization_basis(exe, monkeypatch):
    monkeypatch.delenv("ICDEV_RE_AUTHORIZATION_BASIS", raising=False)
    called = []
    out = op.capture_scenario("process", {"executable": str(exe)}, allow_execute=True,
                              registry_factory=lambda p: called.append(p))
    assert out["reason"] == "authorization_missing" and called == []
    assert out["provenance"]["target_sha256"] and out["provenance"]["authorization_basis"] is None


def test_browser_capture_is_unavailable_and_runs_nothing(exe):
    called = []
    out = op.capture_scenario("browser", {"url": "https://example.test"}, allow_execute=True,
                              authorization_basis="owned", registry_factory=lambda p: called.append(p))
    assert out["status"] == "unavailable" and out["reason"] == "browser_target_unauthorizable"
    assert called == []


def test_capture_not_offered_when_registry_lacks_execute_tools(exe):
    out, fake = _capture(exe, allow_execute=True, authorization_basis="owned",
                         fake=FakeRegistry(offered=("open_binary",)))
    assert out["status"] == "unavailable" and out["reason"] == "capture_not_offered"
    assert fake.calls == [] and fake.closed


def test_opted_in_capture_grants_the_executable_and_scrubs_secrets(exe):
    out, fake = _capture(exe, allow_execute=True, authorization_basis="owned")
    assert out["status"] == "ok", out
    assert fake.grants == [("rea", str(exe.resolve()), "owned")]
    assert fake.calls[0][0] == "ext__rea__capture_process_scenario"
    assert fake.closed
    assert out["provenance"]["allow_execute"] is True
    assert out["provenance"]["authorization_basis"] == "owned"
    stored = json.dumps(out["scenario"])
    assert "hunter2" not in stored and "secret" not in stored
    assert out["scenario"]["environment_keys"] == ["API_KEY"]


# -- the registry gate ---------------------------------------------------------

class _Transport:
    def list_tools(self):
        return [{"name": "open_binary"}, {"name": "capture_process_scenario"}]

    def close(self):
        pass


@pytest.mark.parametrize("allow, exposed", [(False, False), (True, True)])
def test_registry_exposes_execute_tools_only_when_opted_in(monkeypatch, allow, exposed):
    spec = {"name": "rea", "transport": "stdio", "tools": ["open_binary"],
            "execute_tools": ["capture_process_scenario"]}
    monkeypatch.setattr(reg, "enabled_servers", lambda: [spec])
    monkeypatch.setattr(reg, "pin_problem", lambda *a, **k: None)
    monkeypatch.setattr(reg.transport_mod, "resolve_command", lambda s: ["x"])
    monkeypatch.setattr(reg.transport_mod, "build_transport", lambda s, **k: _Transport())
    names = {t.remote_name for t in reg.ExternalToolRegistry(allow_execute=allow).discover()}
    assert "open_binary" in names
    assert ("capture_process_scenario" in names) is exposed


def test_shipped_config_keeps_capture_off_the_default_allowlist():
    for path in (REPO / "args/external_mcp_servers.yaml",
                 REPO / "icdev/data/args/external_mcp_servers.yaml"):
        rea = next(s for s in yaml.safe_load(path.read_text(encoding="utf-8"))["servers"]
                   if s["name"] == "rea")
        assert "capture_process_scenario" not in rea["tools"]
        assert "capture_browser_scenario" not in rea["tools"]
        assert rea["execute_tools"] == ["capture_process_scenario"]
        assert "executable" in rea["authorization"]["target_arguments"]


# -- evidence -> steps ---------------------------------------------------------

def test_steps_are_observed_ordered_and_redacted(exe):
    out, _ = _capture(exe, allow_execute=True, authorization_basis="owned")
    steps, basis = op.steps_from_capture(out["record"])
    assert basis == "reported_by_rea"
    assert [s["basis"] for s in steps] == ["observed"] * 5
    assert {s["evidence_id"] for s in steps} == {"ev-proc-001"}
    titles = [s["title"] for s in steps]
    assert titles[0] == "launch: tool.exe --init"
    assert titles[2] == "input: [redacted]"
    assert titles[-1] == "Process exited (code 0)"
    assert "hunter2" not in json.dumps(steps)


@pytest.mark.parametrize("record, basis", [
    (None, "no_evidence"),
    ({"normalized_result": {"summary": "ran"}}, "no_sequence_in_evidence"),
    ({"normalized_result": {"events": []}}, "no_sequence_in_evidence"),
    ({"normalized_result": {"events": [{"at_ms": 1}]}}, "no_labelled_steps"),
])
def test_unmeasured_sequence_is_none_never_empty(record, basis):
    assert op.steps_from_capture(record) == (None, basis)


def test_draft_without_steps_is_not_saved(db):
    draft = op.build_draft({"status": "ok", "kind": "process",
                            "record": {"normalized_result": {"summary": "ran"}}})
    assert draft["steps"] is None
    with pytest.raises(ValueError):
        op.save_draft(draft)


# -- draft in the canvas, HITL review, chain gate, BPMN ------------------------

def test_observed_draft_round_trip_requires_review_before_a_chain(exe, db):
    out, _ = _capture(exe, allow_execute=True, authorization_basis="customer-authorized")
    draft = op.build_draft(out, name="Ledger reconcile (observed)")
    wid = op.save_draft(draft)

    row = db().execute("SELECT * FROM studio_workflows WHERE workflow_id=?", (wid,)).fetchone()
    assert row["category"] == "observed"
    wf = yaml.safe_load(row["template_yaml"])
    assert wf["basis"] == "observed" and wf["status"] == "draft" and wf["observed"] is True
    assert wf["review"]["state"] == op.REVIEW_PENDING and wf["review"]["required"] is True
    assert wf["provenance"]["authorization_basis"] == "customer-authorized"
    assert wf["provenance"]["target_sha256"]
    assert wf["residual_unknowns"] == ["child processes not enumerated"]
    assert op.chain_eligibility(wf) == (False, f"observed draft awaiting human review "
                                               f"(review.state={op.REVIEW_PENDING})")

    bpmn = importlib.import_module("tools.workflow_canvas.bpmn_export").workflow_to_bpmn(wf)
    assert "AS-OBSERVED draft" in bpmn and bpmn.count("<serviceTask") == 5

    with pytest.raises(ValueError):
        op.review_draft(wid, "approve", reviewer="  ")
    review = op.review_draft(wid, "approve", reviewer="operator", note="matches the runbook")
    assert review["state"] == op.REVIEW_APPROVED
    wf = yaml.safe_load(db().execute("SELECT template_yaml FROM studio_workflows "
                                     "WHERE workflow_id=?", (wid,)).fetchone()[0])
    assert op.chain_eligibility(wf) == (True, None)
    with pytest.raises(ValueError):
        op.review_draft(wid, "reject", reviewer="operator")


def test_non_observed_workflows_are_not_gated():
    assert op.chain_eligibility({"name": "doc-derived"}) == (True, None)
    assert op.chain_eligibility(None) == (True, None)


# -- dashboard routes ----------------------------------------------------------

@pytest.fixture
def client(db, monkeypatch):
    from flask import Flask

    bpmod = importlib.import_module("tools.workflow_canvas.blueprint")
    storage = importlib.import_module("tools.db.storage")
    path = db().execute("PRAGMA database_list").fetchone()[2]

    def _wfc_connect(*a, **k):
        c = sqlite3.connect(path)
        c.row_factory = sqlite3.Row
        return storage.StorageConnection(c, "sqlite")

    monkeypatch.setattr(bpmod, "_INIT_DONE", True)
    monkeypatch.setattr(bpmod, "get_connection", _wfc_connect)
    app = Flask(__name__)
    app.register_blueprint(bpmod.create_wfc_blueprint())
    return app.test_client()


def test_route_refuses_capture_without_allow_execute(client, monkeypatch):
    def _boom(*a, **k):
        raise AssertionError("no registry without the opt-in")
    monkeypatch.setattr(op, "_default_registry", _boom)
    for flag in (None, "true", 1):
        r = client.post("/workflow-canvas/api/workflows/observed-draft",
                        json={"kind": "process", "scenario": {"executable": sys.executable},
                              "authorization_basis": "owned", "allow_execute": flag})
        assert r.status_code == 403 and r.get_json()["reason"] == "allow_execute_required"


def test_route_drafts_then_chain_link_waits_for_review(client, exe, monkeypatch):
    fake = FakeRegistry()
    monkeypatch.setattr(op, "_default_registry", lambda prefix: fake)
    r = client.post("/workflow-canvas/api/workflows/observed-draft",
                    json={"kind": "process", "scenario": {"executable": str(exe)},
                          "authorization_basis": "owned", "allow_execute": True})
    assert r.status_code == 200, r.get_json()
    wid = r.get_json()["workflow_id"]
    assert r.get_json()["review"]["state"] == op.REVIEW_PENDING

    r = client.post("/workflow-canvas/api/workflows/chains/chn-x/phases/phs-x/processify",
                    json={"workflow_id": wid})
    assert r.status_code == 409 and "awaiting human review" in r.get_json()["error"]

    r = client.post(f"/workflow-canvas/api/workflows/{wid}/observed-review",
                    json={"decision": "maybe", "reviewer": "op"})
    assert r.status_code == 400
    r = client.post(f"/workflow-canvas/api/workflows/{wid}/observed-review",
                    json={"decision": "approve", "reviewer": "op"})
    assert r.status_code == 200 and r.get_json()["review"]["state"] == op.REVIEW_APPROVED

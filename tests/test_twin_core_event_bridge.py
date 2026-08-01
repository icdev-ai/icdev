# CUI // SP-CTI — Twin Core event-bridge tests (twx-bus-01)
"""Tests for wiring twin_core into the cross-canvas event bus.

The bus (`tools.canvas.event_bus`) is stubbed by capturing publish/subscribe
calls, so tests run on the shared conftest schema without the canvas_events
table. Adapters are stubbed to avoid canvas DBs.
"""
from __future__ import annotations

import importlib

import pytest

from tools.twin_core import event_bridge as bridge
from tools.twin_core.registry import TwinRegistry


@pytest.fixture
def captured_bus(monkeypatch):
    """Capture event_bus.publish / subscribe calls made by the bridge."""
    published: list[dict] = []
    subscribed: list[tuple] = []
    bus = importlib.import_module("tools.canvas.event_bus")

    def fake_publish(source, event_type, payload, *, target_canvas=None, security_context=None):
        published.append({"source": source, "event_type": event_type, "payload": payload,
                          "target_canvas": target_canvas, "security_context": security_context})
        return "evt-" + str(len(published))

    def fake_subscribe(canvas_id, event_type, handler, *, subscriber_context=None):
        subscribed.append((canvas_id, event_type, handler))

    monkeypatch.setattr(bus, "publish", fake_publish)
    monkeypatch.setattr(bus, "subscribe", fake_subscribe)
    # Reset the idempotency latch so register_subscriptions runs under test.
    monkeypatch.setattr(bridge, "_subscriptions_registered", False)
    return {"published": published, "subscribed": subscribed}


def test_facade_simulate_publishes_canonical_verdict(captured_bus, monkeypatch):
    ndc = importlib.import_module("tools.network.twin")
    monkeypatch.setattr(ndc, "simulate_delta", lambda *a, **k: {
        "id": "sim-1", "verdict": "warn",
        "compliance_findings": [{"severity": "high", "id": "r", "title": "t", "recommendation": "x"}],
        "intent_results": [],
    })
    env = bridge.simulate("ndc", "proj-1", {"add_links": []})
    assert env["verdict"] == "warn"
    pubs = captured_bus["published"]
    assert len(pubs) == 1
    p = pubs[0]
    assert p["event_type"] == bridge.TWIN_SIMULATION_COMPLETED
    assert p["source"] == "ndc"
    assert p["payload"]["verdict"] == "warn"
    assert p["payload"]["worst_severity"] == "high"
    assert p["payload"]["target_id"] == "proj-1"


def test_facade_snapshot_publishes(captured_bus, monkeypatch):
    pdc = importlib.import_module("tools.pipeline.twin")
    monkeypatch.setattr(pdc, "take_snapshot", lambda *a, **k: {"id": "snap-9", "label": "l"})
    snap = bridge.snapshot("pdc", "pipe-1")
    assert snap["id"] == "snap-9"
    assert captured_bus["published"][0]["event_type"] == bridge.TWIN_SNAPSHOT_TAKEN
    assert captured_bus["published"][0]["payload"]["snapshot_id"] == "snap-9"


def test_facade_can_suppress_publish(captured_bus, monkeypatch):
    pdc = importlib.import_module("tools.pipeline.twin")
    monkeypatch.setattr(pdc, "take_snapshot", lambda *a, **k: {"id": "s"})
    bridge.snapshot("pdc", "pipe-1", publish=False)
    assert captured_bus["published"] == []


def test_register_subscriptions_wires_two_crosscanvas(captured_bus):
    ran = bridge.register_subscriptions(force=True)
    assert ran is True
    subs = {(c, e) for c, e, _ in captured_bus["subscribed"]}
    # PDC pipeline_deployed -> SDC refresh (subscribed under sdc + pdc)
    assert ("sdc", bridge.EVENT_PIPELINE_DEPLOYED) in subs
    # SDC threat-model-changed -> BDC crosswalk drift (subscribed under bdc + sdc)
    assert ("bdc", bridge.EVENT_THREAT_MODEL_CHANGED) in subs


def test_register_subscriptions_idempotent(captured_bus):
    assert bridge.register_subscriptions(force=True) is True
    assert bridge.register_subscriptions() is False  # already registered


def test_pipeline_deployed_refreshes_sdc(captured_bus, monkeypatch):
    calls = {}

    class _StubSDC:
        canvas_key = "sdc"
        supports_snapshots = True

        def take_snapshot(self, design_id, label=None, **kw):
            calls["design_id"] = design_id
            calls["label"] = label
            return {"id": "sdc-snap-1", "design_id": design_id}

    monkeypatch.setattr(TwinRegistry, "get", classmethod(lambda cls, k: _StubSDC() if k == "sdc" else None))
    bridge._on_pipeline_deployed("e1", "sdc", bridge.EVENT_PIPELINE_DEPLOYED,
                                 {"design_id": "d-42", "_security_context": {"clearance": "CUI"}})
    assert calls["design_id"] == "d-42"
    # A refresh publishes a twin_snapshot_taken for SDC.
    assert any(p["event_type"] == bridge.TWIN_SNAPSHOT_TAKEN and p["source"] == "sdc"
               for p in captured_bus["published"])


def test_pipeline_deployed_no_design_is_noop(captured_bus):
    bridge._on_pipeline_deployed("e1", "sdc", bridge.EVENT_PIPELINE_DEPLOYED, {})
    assert captured_bus["published"] == []


def test_threat_model_changed_reruns_bdc_crosswalk(captured_bus, monkeypatch):
    bdc = importlib.import_module("tools.boundary_canvas.twin")
    monkeypatch.setattr(bdc, "crosswalk_drift", lambda pid, s, t: {"status": "ok", "drifts": [{"x": 1}], "total": 1})
    bridge._on_threat_model_changed("e1", "bdc", bridge.EVENT_THREAT_MODEL_CHANGED,
                                    {"project_id": "p-7"})
    pubs = [p for p in captured_bus["published"] if p["source"] == "bdc"]
    assert pubs and pubs[0]["payload"]["verdict"] == "warn"     # drift present -> warn
    assert pubs[0]["payload"]["drift_total"] == 1


def test_threat_model_changed_no_project_is_noop(captured_bus):
    bridge._on_threat_model_changed("e1", "bdc", bridge.EVENT_THREAT_MODEL_CHANGED, {})
    assert captured_bus["published"] == []


def test_drift_card_disabled_by_default(monkeypatch):
    monkeypatch.delenv("ICDEV_TWIN_DRIFT_CARDS", raising=False)
    out = bridge.create_twin_drift_card("ndc", "proj", "drift", "detail")
    assert out["created"] is False
    assert "disabled" in out["reason"]


def test_recent_twin_events_graceful_without_table(monkeypatch):
    # Force the connection to raise; recent_twin_events must degrade to [].
    storage = importlib.import_module("tools.db.storage")

    def boom():
        raise RuntimeError("no table")

    monkeypatch.setattr(storage, "get_connection", boom)
    assert bridge.recent_twin_events() == []

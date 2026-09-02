# CUI // SP-CTI
"""DataBridge feeds blueprint — allowlist + scope enforcement (ctx-expose-05).

These tests exercise the BLUEPRINT: does it refuse an unauthenticated caller,
an unlisted connector, and a caller missing the read or write scope, and does it
shape a connector's response correctly.

They used to do that THROUGH a named third-party vendor connector, which coupled
the blueprint's access-control tests to one vendor's presence in the tree --
when that connector was removed, six tests of the access-control surface went
with it. The exposed connectors are now both read-only and local-DB-backed, so
there is no vendor stub to borrow either.

A test-local fake is the right dependency anyway: the subject under test is the
blueprint, and the fake makes the read/write contract it relies on explicit
(`read(ConnectorRequest) -> ConnectorResponse`, `write(ConnectorRequest, body)`).
The real allowlist is still asserted directly, so removing a connector from it
cannot pass silently.
"""
from __future__ import annotations

from flask import Flask, g

from tools.cortex.schemas import CortexContext
from tools.dashboard.api import databridge_feeds as feeds
from tools.databridge.connector import ConnectorResponse
from tools.dashboard.api.databridge_feeds import databridge_feeds_bp

# A connector name that is NOT in the real allowlist; tests that need a working
# connector monkeypatch both the allowlist and the instance resolver, so the
# name never has to exist in the registry.
FAKE = "fake_connector"


class _FakeConnector:
    """Minimal stand-in implementing only what the blueprint calls."""

    def __init__(self):
        self.reads: list = []
        self.writes: list = []

    def read(self, req):
        self.reads.append(req)
        return ConnectorResponse(
            status="ok",
            data=[{"id": 1, "value": "a"}, {"id": 2, "value": "b"}],
            row_count=2,
            errors=[],
            metadata={"stub": True},
        )

    def write(self, req, body):
        self.writes.append((req, body))
        return ConnectorResponse(
            status="ok", data={"accepted": True}, row_count=1, errors=[], metadata={}
        )


def make_client(*, binding=None):
    app = Flask(__name__)
    app.register_blueprint(databridge_feeds_bp)

    @app.before_request
    def _simulate_central_auth():
        if binding is not None:
            g.cortex_binding = binding

    return app.test_client()


def _binding(scopes):
    return {
        "ctx": CortexContext(tenant_id="compass", classification="CUI"),
        "scopes": scopes,
        "key_id": "k1",
        "label": "compass",
        "tenant_id": "compass",
    }


def _expose_fake(monkeypatch):
    """Allowlist the fake and resolve it, leaving the real registry alone."""
    conn = _FakeConnector()
    monkeypatch.setattr(
        feeds, "_CONNECTOR_ALLOWLIST", frozenset({FAKE, "icdev_demand", "icdev_cpmp"})
    )
    monkeypatch.setattr(feeds, "_get_instance", lambda name: conn if name == FAKE else None)
    return conn


class TestAllowlistIsReal:
    def test_allowlist_contents(self):
        """Asserted directly, so dropping a connector cannot pass unnoticed."""
        assert feeds._CONNECTOR_ALLOWLIST == frozenset({"icdev_demand", "icdev_cpmp"})

    def test_no_vendor_connector_is_exposed(self):
        """Only first-party, local-DB connectors are externally exposable."""
        assert all(n.startswith("icdev_") for n in feeds._CONNECTOR_ALLOWLIST)


class TestAccessControl:
    def test_no_binding_401(self):
        client = make_client(binding=None)
        resp = client.get("/api/databridge/v1/icdev_demand/capability_gaps")
        assert resp.status_code == 401

    def test_unlisted_connector_403(self):
        client = make_client(binding=_binding(["databridge:splunk:read"]))
        resp = client.get("/api/databridge/v1/splunk/events")
        assert resp.status_code == 403
        assert "not exposed" in resp.get_json()["error"]

    def test_missing_scope_403(self):
        client = make_client(binding=_binding(["cortex:search"]))
        resp = client.get("/api/databridge/v1/icdev_demand/capability_gaps")
        assert resp.status_code == 403
        assert "databridge:icdev_demand:read" in resp.get_json()["error"]


class TestReadWrite:
    def test_read_rows_ok(self, monkeypatch):
        conn = _expose_fake(monkeypatch)
        client = make_client(binding=_binding([f"databridge:{FAKE}:read"]))
        resp = client.get(f"/api/databridge/v1/{FAKE}/some_table")
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["ok"] is True
        assert body["row_count"] == len(body["data"]) == 2
        assert body["metadata"]["stub"] is True
        assert conn.reads[0].table_name == "some_table"

    def test_write_requires_write_scope(self, monkeypatch):
        _expose_fake(monkeypatch)
        client = make_client(binding=_binding([f"databridge:{FAKE}:read"]))
        resp = client.post(f"/api/databridge/v1/{FAKE}/some_table", json={"subject_id": "p-1"})
        assert resp.status_code == 403

    def test_write_with_write_scope_ok(self, monkeypatch):
        conn = _expose_fake(monkeypatch)
        client = make_client(
            binding=_binding([f"databridge:{FAKE}:read", f"databridge:{FAKE}:write"])
        )
        resp = client.post(f"/api/databridge/v1/{FAKE}/some_table", json={"subject_id": "p-1"})
        assert resp.status_code == 200
        assert resp.get_json()["data"]["accepted"] is True
        assert conn.writes[0][1] == {"subject_id": "p-1"}

    def test_write_non_json_400(self, monkeypatch):
        _expose_fake(monkeypatch)
        client = make_client(
            binding=_binding([f"databridge:{FAKE}:read", f"databridge:{FAKE}:write"])
        )
        resp = client.post(f"/api/databridge/v1/{FAKE}/some_table", data="not json")
        assert resp.status_code == 400

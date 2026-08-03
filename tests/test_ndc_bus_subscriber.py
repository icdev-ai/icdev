# CUI // SP-CTI
"""Tests for the NDC reactive event-bus subscriber (ndc-brg-03).

Covers:
  (a) SDC assessment-completion event -> assessment recorded on NDC side.
  (b) CVE/vulnerability event -> affected topology vuln overlay marked stale.
  (c) Malformed event -> handler logs and returns without raising.
  (d) Import-time registration wires both subscriptions onto the event bus.
"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tests import _sql_compat  # noqa: E402


# ---------------------------------------------------------------------------
# Minimal in-memory NDC SQLite DB (mirrors the tools/network vuln schema)
# ---------------------------------------------------------------------------
_SCHEMA = """
CREATE TABLE IF NOT EXISTS nc_vuln_scans (
    id TEXT PRIMARY KEY,
    topology_id TEXT,
    scan_name TEXT DEFAULT '',
    host_count INTEGER DEFAULT 0,
    created_at TEXT
);
CREATE TABLE IF NOT EXISTS nc_vuln_findings (
    id TEXT PRIMARY KEY,
    host_id TEXT,
    scan_id TEXT,
    cve TEXT DEFAULT '',
    severity INTEGER DEFAULT 0
);
"""


def _shim_conn():
    """In-memory NDC DB that translates %s -> ? the way the runtime does.

    ``unclosable`` because bus_subscriber writes through a connection it then
    closes; TranslatingConnection.__exit__/close() would drop the in-memory
    database before the test reads the row back.
    """
    raw = sqlite3.connect(":memory:")
    raw.row_factory = sqlite3.Row
    raw.executescript(_SCHEMA)
    raw.commit()
    return _sql_compat.translating(raw, unclosable=True)


@pytest.fixture
def shim():
    conn = _shim_conn()
    yield conn
    # close the raw handle: the wrapper is unclosable by design, so nothing
    # else rolls back a statement that left a write transaction open.
    conn._conn.close()


def _patch_conn(shim):
    """Patch the get_connection symbol imported into bus_subscriber."""
    return patch("tools.network.bus_subscriber.get_connection", lambda: shim)


# ---------------------------------------------------------------------------
# (a) SDC assessment-completion event
# ---------------------------------------------------------------------------
def test_sdc_assessment_recorded_on_ndc(shim):
    from tools.network import bus_subscriber

    event = {
        "topology_id": "topo-123",
        "design_id": "sdc-abc",
        "assessment_id": "assess-1",
        "risk_score": 42.5,
        "posture_grade": "D",
        "cat1_count": 3,
    }
    with _patch_conn(shim):
        bus_subscriber._on_sdc_assessment_completed("evt-1", "sdc", "sdc.assessment.completed", event)

    row = shim.execute(
        "SELECT * FROM nc_sdc_assessments WHERE topology_id='topo-123'"
    ).fetchone()
    assert row is not None
    assert row["design_id"] == "sdc-abc"
    assert row["posture_grade"] == "D"
    assert row["cat1_count"] == 3
    assert abs(row["risk_score"] - 42.5) < 1e-6


def test_sdc_assessment_is_idempotent(shim):
    from tools.network import bus_subscriber

    event = {"topology_id": "topo-9", "posture_grade": "A", "risk_score": 1}
    with _patch_conn(shim):
        bus_subscriber._on_sdc_assessment_completed("e", "sdc", "t", event)
        event["posture_grade"] = "F"
        bus_subscriber._on_sdc_assessment_completed("e", "sdc", "t", event)

    rows = shim.execute(
        "SELECT * FROM nc_sdc_assessments WHERE topology_id='topo-9'"
    ).fetchall()
    assert len(rows) == 1  # upsert, not duplicate insert
    assert rows[0]["posture_grade"] == "F"  # latest value wins


# ---------------------------------------------------------------------------
# (b) CVE / vulnerability event -> stale-mark path
# ---------------------------------------------------------------------------
def test_cve_event_marks_affected_topology_stale(shim):
    from tools.network import bus_subscriber

    # Seed a scan on topo-cve with a finding referencing CVE-2024-9999.
    shim.execute(
        "INSERT INTO nc_vuln_scans (id, topology_id) VALUES (?, ?)",
        ("scan-1", "topo-cve"),
    )
    shim.execute(
        "INSERT INTO nc_vuln_findings (id, scan_id, cve) VALUES (?, ?, ?)",
        ("find-1", "scan-1", "CVE-2024-9999"),
    )
    shim.commit()

    with _patch_conn(shim):
        bus_subscriber._on_cve_published(
            "evt-cve", "sdc", "sdc.cve.published", {"cve_ids": ["CVE-2024-9999"]}
        )

    row = shim.execute(
        "SELECT * FROM nc_vuln_overlay_stale WHERE topology_id='topo-cve'"
    ).fetchone()
    assert row is not None
    assert row["is_stale"] == 1
    assert "CVE-2024-9999" in json.loads(row["cve_ids"])


def test_cve_event_broad_refresh_marks_all_scanned_topologies(shim):
    from tools.network import bus_subscriber

    shim.execute("INSERT INTO nc_vuln_scans (id, topology_id) VALUES ('s1', 'topo-a')")
    shim.execute("INSERT INTO nc_vuln_scans (id, topology_id) VALUES ('s2', 'topo-b')")
    shim.commit()

    with _patch_conn(shim):
        bus_subscriber._on_cve_published("evt", "sdc", "sdc.cve.published", {})

    rows = shim.execute("SELECT topology_id FROM nc_vuln_overlay_stale").fetchall()
    marked = {r["topology_id"] for r in rows}
    assert marked == {"topo-a", "topo-b"}


# ---------------------------------------------------------------------------
# (c) Malformed events must never raise
# ---------------------------------------------------------------------------
def test_malformed_assessment_event_does_not_raise(shim):
    from tools.network import bus_subscriber

    with _patch_conn(shim):
        # Missing topology_id, junk types — must return silently.
        bus_subscriber._on_sdc_assessment_completed("e", "sdc", "t", {})
        bus_subscriber._on_sdc_assessment_completed("e", "sdc", "t", None)
        bus_subscriber._on_sdc_assessment_completed(
            "e", "sdc", "t", {"topology_id": "x", "risk_score": "not-a-number"}
        )
    # No exception == pass. Verify the well-formed junk row still landed.
    row = shim.execute("SELECT * FROM nc_sdc_assessments WHERE topology_id='x'").fetchone()
    assert row is not None
    assert row["risk_score"] == 0  # unparseable score coerced to 0


def test_malformed_cve_event_does_not_raise(shim):
    from tools.network import bus_subscriber

    with _patch_conn(shim):
        bus_subscriber._on_cve_published("e", "sdc", "t", None)
        bus_subscriber._on_cve_published("e", "sdc", "t", {"cve_ids": "CVE-2024-1"})
    # Reaches here without raising.


def test_handler_swallows_db_errors(shim):
    from tools.network import bus_subscriber

    class _Boom:
        def execute(self, *_a, **_kw):
            raise RuntimeError("simulated DB failure")

        def commit(self):
            pass

        def close(self):
            pass

    with patch("tools.network.bus_subscriber.get_connection", lambda: _Boom()):
        # Both handlers must swallow the exception.
        bus_subscriber._on_sdc_assessment_completed(
            "e", "sdc", "t", {"topology_id": "t1"}
        )
        bus_subscriber._on_cve_published("e", "sdc", "t", {"cve_ids": ["CVE-1"]})


# ---------------------------------------------------------------------------
# (d) Import-time registration wires the subscriptions
# ---------------------------------------------------------------------------
def test_register_wires_both_subscriptions():
    from tools.canvas import event_bus
    from tools.network import bus_subscriber

    # Snapshot listeners so the test is isolated.
    before = {k: list(v) for k, v in event_bus._LISTENERS.items()}
    try:
        bus_subscriber.register()
        assert ("sdc", "sdc.assessment.completed") in event_bus._LISTENERS
        assert ("sdc", "sdc.cve.published") in event_bus._LISTENERS
        handlers_a = [h for h, _ctx in event_bus._LISTENERS[("sdc", "sdc.assessment.completed")]]
        handlers_b = [h for h, _ctx in event_bus._LISTENERS[("sdc", "sdc.cve.published")]]
        assert bus_subscriber._on_sdc_assessment_completed in handlers_a
        assert bus_subscriber._on_cve_published in handlers_b
    finally:
        event_bus._LISTENERS.clear()
        event_bus._LISTENERS.update(before)

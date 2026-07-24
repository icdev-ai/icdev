# CUI // SP-CTI
"""Tests for the DIC Freshness Notifier (dmx-loop-01).

Covers the proactive owner-alert contract:
  * a threshold crossing into aging/stale fires exactly once,
  * the per-document cooldown suppresses a repeat crossing within the window,
  * a new crossing after the window fires again,
  * a state that does not cross does nothing,
  * the config toggle is OFF by default,
  * owner resolution + payload shape (link, findings, severity),
  * gateway-unreachable degrades cleanly (skip, no last_notified_at write).

The gateway is mocked — no real notifications are sent. Uses the conftest
``icdev_db`` fixture (fresh SQLite schema) so it never depends on the checkout's
data/icdev.db.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from tools.document_intelligence.freshness_engine import FreshnessResult
from tools.document_intelligence.freshness_notifier import (
    _load_notif_config,
    notify_freshness_crossings,
)


NOW = datetime(2026, 7, 24, 12, 0, 0, tzinfo=timezone.utc)


class FakeGateway:
    """Records send() calls; returns a gateway-shaped result dict."""

    def __init__(self):
        self.calls = []

    def send(self, event_type, severity="info", title="", body="", metadata=None):
        self.calls.append(
            {
                "event_type": event_type,
                "severity": severity,
                "title": title,
                "body": body,
                "metadata": metadata or {},
            }
        )
        return {"event_type": event_type, "deliveries": {"slack": {"status": "delivered"}}}


class RaisingGateway:
    """Simulates an unreachable channel — send() raises."""

    def __init__(self):
        self.calls = 0

    def send(self, *a, **k):
        self.calls += 1
        raise ConnectionError("channel unreachable (air-gap)")


_CONFIG = {
    "enabled": True,
    "cooldown_hours": 168,
    "event_type": "dic_freshness_alert",
    "default_channel": "#docs-fallback",
    "base_url": "https://icdev.example.mil",
    "max_findings": 3,
    "severity_by_state": {"aging": "warning", "stale": "error"},
}


@pytest.fixture
def conn(icdev_db, monkeypatch):
    """get_connection() pointed at the fresh temp DB, with a seeded collection."""
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("ICDEV_DB_PATH", str(icdev_db))
    from tools.db.storage import get_connection

    c = get_connection()
    cur = c.cursor()
    cur.execute(
        "INSERT INTO dic_collections (collection_id, name, owner_id, tenant_id, classification) "
        "VALUES (%s, %s, %s, %s, %s)",
        ("col1", "Policies", "alice@unit.mil", "default", "CUI"),
    )
    c.commit()
    yield c
    c.close()


def _seed_freshness(conn, doc_id, state, last_notified_at=None, collection_id="col1"):
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO dic_doc_freshness "
        "(doc_id, collection_id, state, reason, source_event, score, updated_at, "
        " last_notified_at, tenant_id, classification) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        (doc_id, collection_id, state, "seed", "freshness_scan", 0.5,
         NOW.isoformat(), last_notified_at, "default", "CUI"),
    )
    conn.commit()


def _read_last_notified(conn, doc_id):
    cur = conn.cursor()
    cur.execute("SELECT last_notified_at FROM dic_doc_freshness WHERE doc_id = %s", (doc_id,))
    row = cur.fetchone()
    return row[0] if row else None


def _result(doc_id, state, collection_id="col1", title="Test Doc", reason="age 200d"):
    return FreshnessResult(
        doc_id=doc_id, title=title, collection_id=collection_id, state=state,
        score=0.85, reason=reason, source_event="freshness_scan",
        tenant_id="default", classification="CUI",
    )


# ---------------------------------------------------------------------------
# Crossing detection
# ---------------------------------------------------------------------------

def test_crossing_into_stale_fires_exactly_once(conn):
    _seed_freshness(conn, "d1", "fresh", last_notified_at=None)
    gw = FakeGateway()
    prior = {"d1": {"state": "fresh", "last_notified_at": None}}

    out = notify_freshness_crossings(
        [_result("d1", "stale")], prior, conn=conn, config=_CONFIG, gateway=gw, now=NOW,
    )

    assert out["notified"] == ["d1"]
    assert len(gw.calls) == 1
    # last_notified_at persisted for cooldown bookkeeping.
    assert _read_last_notified(conn, "d1") == NOW.isoformat()


def test_first_ever_state_unknown_prior_counts_as_crossing(conn):
    _seed_freshness(conn, "d1", "aging", last_notified_at=None)
    gw = FakeGateway()
    prior = {}  # no prior snapshot at all -> unknown

    out = notify_freshness_crossings(
        [_result("d1", "aging")], prior, conn=conn, config=_CONFIG, gateway=gw, now=NOW,
    )
    assert out["notified"] == ["d1"]
    assert gw.calls[0]["severity"] == "warning"


def test_no_crossing_when_already_stale_does_nothing(conn):
    _seed_freshness(conn, "d1", "stale", last_notified_at=None)
    gw = FakeGateway()
    prior = {"d1": {"state": "stale", "last_notified_at": None}}

    out = notify_freshness_crossings(
        [_result("d1", "stale")], prior, conn=conn, config=_CONFIG, gateway=gw, now=NOW,
    )
    assert out["notified"] == []
    assert out["skipped"] == ["d1"]
    assert gw.calls == []


def test_fresh_state_is_never_alerted(conn):
    gw = FakeGateway()
    prior = {"d1": {"state": "stale", "last_notified_at": None}}
    out = notify_freshness_crossings(
        [_result("d1", "fresh")], prior, conn=conn, config=_CONFIG, gateway=gw, now=NOW,
    )
    assert out["notified"] == []
    assert gw.calls == []


# ---------------------------------------------------------------------------
# Cooldown / de-dup
# ---------------------------------------------------------------------------

def test_cooldown_suppresses_repeat_within_window(conn):
    # Alerted 24h ago (< 168h window) then escalates aging -> stale.
    recent = (NOW - timedelta(hours=24)).isoformat()
    _seed_freshness(conn, "d1", "aging", last_notified_at=recent)
    gw = FakeGateway()
    prior = {"d1": {"state": "aging", "last_notified_at": recent}}

    out = notify_freshness_crossings(
        [_result("d1", "stale")], prior, conn=conn, config=_CONFIG, gateway=gw, now=NOW,
    )
    assert out["suppressed"] == ["d1"]
    assert gw.calls == []
    # last_notified_at unchanged by a suppressed alert.
    assert _read_last_notified(conn, "d1") == recent


def test_new_crossing_after_window_fires_again(conn):
    # Alerted 200h ago (> 168h window) -> a fresh crossing must fire.
    old = (NOW - timedelta(hours=200)).isoformat()
    _seed_freshness(conn, "d1", "aging", last_notified_at=old)
    gw = FakeGateway()
    prior = {"d1": {"state": "aging", "last_notified_at": old}}

    out = notify_freshness_crossings(
        [_result("d1", "stale")], prior, conn=conn, config=_CONFIG, gateway=gw, now=NOW,
    )
    assert out["notified"] == ["d1"]
    assert len(gw.calls) == 1
    assert _read_last_notified(conn, "d1") == NOW.isoformat()


# ---------------------------------------------------------------------------
# Config toggle
# ---------------------------------------------------------------------------

def test_disabled_config_sends_nothing(conn):
    _seed_freshness(conn, "d1", "fresh", last_notified_at=None)
    gw = FakeGateway()
    cfg = dict(_CONFIG, enabled=False)
    out = notify_freshness_crossings(
        [_result("d1", "stale")], {}, conn=conn, config=cfg, gateway=gw, now=NOW,
    )
    assert out["enabled"] is False
    assert gw.calls == []


def test_shipped_default_is_off():
    # The config file must ship with notifications disabled.
    assert _load_notif_config()["enabled"] is False


# ---------------------------------------------------------------------------
# Owner resolution + payload
# ---------------------------------------------------------------------------

def test_payload_contains_owner_link_and_findings(conn):
    _seed_freshness(conn, "d1", "fresh", last_notified_at=None)
    gw = FakeGateway()
    out = notify_freshness_crossings(
        [_result("d1", "stale", reason="age 200d; 4 drift events")],
        {"d1": {"state": "fresh", "last_notified_at": None}},
        conn=conn, config=_CONFIG, gateway=gw, now=NOW,
    )
    assert out["notified"] == ["d1"]
    call = gw.calls[0]
    assert call["event_type"] == "dic_freshness_alert"
    assert call["severity"] == "error"
    assert "stale" in call["title"].lower()
    # Modernization link + owner from the collection.
    assert "/document-intelligence/freshness" in call["body"]
    assert call["metadata"]["owner"] == "alice@unit.mil"
    assert call["metadata"]["doc_id"] == "d1"
    assert call["metadata"]["link"].startswith("https://icdev.example.mil")
    # Findings fall back to the freshness reason when no modernization findings.
    assert "age 200d" in call["body"]


def test_owner_falls_back_to_default_channel(conn):
    # A collection with no owner_id -> configured default channel.
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO dic_collections (collection_id, name, owner_id, tenant_id, classification) "
        "VALUES (%s,%s,%s,%s,%s)",
        ("col2", "Orphan", "", "default", "CUI"),
    )
    conn.commit()
    gw = FakeGateway()
    out = notify_freshness_crossings(
        [_result("d9", "stale", collection_id="col2")],
        {}, conn=conn, config=_CONFIG, gateway=gw, now=NOW,
    )
    assert out["notified"] == ["d9"]
    assert gw.calls[0]["metadata"]["owner"] == "#docs-fallback"


# ---------------------------------------------------------------------------
# Air-gap / unreachable channel
# ---------------------------------------------------------------------------

def test_gateway_unreachable_skips_without_crash_or_persist(conn):
    _seed_freshness(conn, "d1", "fresh", last_notified_at=None)
    gw = RaisingGateway()
    out = notify_freshness_crossings(
        [_result("d1", "stale")],
        {"d1": {"state": "fresh", "last_notified_at": None}},
        conn=conn, config=_CONFIG, gateway=gw, now=NOW,
    )
    assert out["skipped"] == ["d1"]
    assert out["notified"] == []
    assert gw.calls == 1
    # Delivery failed -> last_notified_at NOT written, so a later scan retries.
    assert _read_last_notified(conn, "d1") is None

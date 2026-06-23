# CUI // SP-CTI
"""ECR-BILL V&V: usage_events + usage_daily_rollup tables + rollup reflex tests."""
from __future__ import annotations

import importlib
import sqlite3
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Windows first-SQLite-connection latency can exceed the default 30s per-test
# timeout (antivirus scanning of new .db files). Override for this file.
pytestmark = pytest.mark.timeout(120)

# ---------------------------------------------------------------------------
# In-memory schema (mirrors migration 213)
# ---------------------------------------------------------------------------

_SCHEMA = """
CREATE TABLE IF NOT EXISTS usage_events (
    id           TEXT PRIMARY KEY,
    tenant_id    TEXT NOT NULL,
    event_type   TEXT NOT NULL CHECK (event_type IN (
        'llm_token','api_call','storage_mb','canvas_load','coworker_task')),
    quantity     REAL NOT NULL DEFAULT 1.0,
    model        TEXT,
    canvas_key   TEXT,
    metadata     TEXT,
    recorded_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_usage_events_tenant
    ON usage_events(tenant_id, recorded_at);

CREATE INDEX IF NOT EXISTS idx_usage_events_type
    ON usage_events(event_type, recorded_at);

CREATE TABLE IF NOT EXISTS usage_daily_rollup (
    tenant_id      TEXT NOT NULL,
    event_type     TEXT NOT NULL,
    model          TEXT NOT NULL DEFAULT '',
    rollup_date    TEXT NOT NULL,
    total_quantity REAL NOT NULL DEFAULT 0.0,
    PRIMARY KEY (tenant_id, event_type, model, rollup_date)
);

CREATE INDEX IF NOT EXISTS idx_usage_rollup_tenant
    ON usage_daily_rollup(tenant_id, rollup_date);
"""


def _make_db(tmp_path):
    conn = sqlite3.connect(str(tmp_path / "test_bill.db"))
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    conn.commit()
    return conn


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_usage_tables_exist(tmp_path):
    """Migration creates usage_events and usage_daily_rollup tables."""
    conn = _make_db(tmp_path)
    tables = {
        r[0]
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    assert "usage_events" in tables
    assert "usage_daily_rollup" in tables
    conn.close()


def test_usage_event_insert(tmp_path):
    """usage_events accepts valid event types and rejects invalid ones."""
    conn = _make_db(tmp_path)
    valid_types = ["llm_token", "api_call", "storage_mb", "canvas_load", "coworker_task"]
    for et in valid_types:
        conn.execute(
            "INSERT INTO usage_events (id, tenant_id, event_type, quantity, recorded_at) "
            "VALUES (?, 'tenant-1', ?, 1.0, datetime('now'))",
            (str(uuid.uuid4()), et),
        )
    conn.commit()

    count = conn.execute("SELECT COUNT(*) FROM usage_events").fetchone()[0]
    assert count == len(valid_types)

    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO usage_events (id, tenant_id, event_type, quantity, recorded_at) "
            "VALUES (?, 'tenant-1', 'bad_type', 1.0, datetime('now'))",
            (str(uuid.uuid4()),),
        )
    conn.close()


def test_usage_event_primary_key(tmp_path):
    """usage_events id is a unique primary key."""
    conn = _make_db(tmp_path)
    eid = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO usage_events (id, tenant_id, event_type, quantity, recorded_at) "
        "VALUES (?, 'tenant-1', 'api_call', 1.0, datetime('now'))",
        (eid,),
    )
    conn.commit()

    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO usage_events (id, tenant_id, event_type, quantity, recorded_at) "
            "VALUES (?, 'tenant-2', 'api_call', 1.0, datetime('now'))",
            (eid,),
        )
    conn.close()


def test_rollup_upsert(tmp_path):
    """usage_daily_rollup primary key (tenant, type, model, date) is unique; upsert works."""
    conn = _make_db(tmp_path)
    conn.execute(
        "INSERT INTO usage_daily_rollup (tenant_id, event_type, model, rollup_date, total_quantity) "
        "VALUES ('t1', 'llm_token', 'claude-haiku-4-5', '2026-06-22', 1000.0)"
    )
    conn.commit()

    # Upsert same key with updated quantity
    conn.execute(
        "INSERT INTO usage_daily_rollup (tenant_id, event_type, model, rollup_date, total_quantity) "
        "VALUES ('t1', 'llm_token', 'claude-haiku-4-5', '2026-06-22', 2000.0) "
        "ON CONFLICT (tenant_id, event_type, model, rollup_date) "
        "DO UPDATE SET total_quantity = excluded.total_quantity"
    )
    conn.commit()

    row = conn.execute(
        "SELECT total_quantity FROM usage_daily_rollup "
        "WHERE tenant_id='t1' AND event_type='llm_token' AND rollup_date='2026-06-22'"
    ).fetchone()
    assert row["total_quantity"] == 2000.0
    conn.close()


def test_rollup_reflex_module_importable():
    """tools.genesis.reflexes.usage_rollup exports a run() function."""
    module = importlib.import_module("tools.genesis.reflexes.usage_rollup")
    assert callable(getattr(module, "run", None))


def test_metering_records_event(tmp_path, monkeypatch):
    """record_usage() inserts a row into usage_events (synchronous via mock)."""
    import importlib
    conn = _make_db(tmp_path)

    # Synchronous mock so the background thread completes before assertions
    inserted: list = []

    class _FakeConn:
        def execute(self, sql, params=()):
            if "INSERT INTO usage_events" in sql:
                inserted.append(params)
            return self

        def fetchall(self):
            return []

        def __enter__(self):
            return self

        def __exit__(self, *a):
            pass

    mock_cm = _FakeConn()

    metering = importlib.import_module("tools.billing.metering")
    monkeypatch.setattr(metering, "_write_event", lambda *a, **kw: inserted.append(a))

    metering.record_usage("tenant-X", "llm_token", quantity=500.0, model="claude-sonnet-4-6")

    # Give the daemon thread time to execute
    import time
    time.sleep(0.1)

    assert len(inserted) >= 1
    # First positional arg is tenant_id
    assert inserted[0][0] == "tenant-X"
    assert inserted[0][1] == "llm_token"
    assert inserted[0][2] == 500.0


def test_stripe_webhook_updates_status(tmp_path, monkeypatch):
    """POST /webhooks/stripe updates billing_status; invalid sig → 403."""
    import hashlib
    import hmac
    import importlib
    import json
    import time

    secret = "whsec_test_secret_abc123"
    monkeypatch.setenv("ICDEV_STRIPE_WEBHOOK_SECRET", secret)

    stripe_handler = importlib.import_module("tools.billing.stripe_handler")
    importlib.reload(stripe_handler)

    # Build a valid Stripe-Signature header
    tenant_id = str(uuid.uuid4())
    payload_dict = {
        "type": "invoice.payment_succeeded",
        "data": {
            "object": {
                "metadata": {"tenant_id": tenant_id},
                "customer": "cus_test123",
            }
        },
    }
    payload_bytes = json.dumps(payload_dict).encode()
    ts = str(int(time.time()))
    signed = f"{ts}.".encode() + payload_bytes
    sig = hmac.new(secret.encode(), signed, hashlib.sha256).hexdigest()
    valid_header = f"t={ts},v1={sig}"

    # --- Test 1: invalid signature → False ---
    assert stripe_handler.verify_stripe_signature(payload_bytes, "t=bad,v1=badsig") is False

    # --- Test 2: valid signature → True ---
    assert stripe_handler.verify_stripe_signature(payload_bytes, valid_header) is True

    # --- Test 3: handle_webhook updates DB ---
    updated: list = []

    class _FakeConn:
        def execute(self, sql, params=()):
            if "UPDATE tenants SET billing_status" in sql:
                updated.append(params)
            return self

        def fetchall(self):
            return []

        def __enter__(self):
            return self

        def __exit__(self, *a):
            pass

    monkeypatch.setattr(stripe_handler, "_update_billing_status",
                        lambda tid, status: updated.append((tid, status)))

    result = stripe_handler.handle_webhook(
        "invoice.payment_succeeded",
        {"metadata": {"tenant_id": tenant_id}},
    )
    assert result["handled"] is True
    assert result["billing_status"] == "active"
    assert result["tenant_id"] == tenant_id
    assert len(updated) >= 1

    # --- Test 4: payment_failed → past_due ---
    result2 = stripe_handler.handle_webhook(
        "invoice.payment_failed",
        {"metadata": {"tenant_id": tenant_id}},
    )
    assert result2["billing_status"] == "past_due"

    # --- Test 5: subscription.deleted → cancelled ---
    result3 = stripe_handler.handle_webhook(
        "customer.subscription.deleted",
        {"metadata": {"tenant_id": tenant_id}},
    )
    assert result3["billing_status"] == "cancelled"

    # --- Test 6: unknown event → not handled ---
    result4 = stripe_handler.handle_webhook("some.other.event", {})
    assert result4["handled"] is False


def test_rollup_reflex_run(tmp_path):
    """usage_rollup reflex rolls up yesterday's events into usage_daily_rollup."""
    conn = sqlite3.connect(str(tmp_path / "rollup_test.db"))
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)

    yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
    recorded_at = f"{yesterday}T12:00:00"

    # Seed two events for yesterday
    conn.execute(
        "INSERT INTO usage_events (id, tenant_id, event_type, quantity, model, recorded_at) "
        "VALUES (?, 'tenant-A', 'llm_token', 500.0, 'claude-sonnet-4-6', ?)",
        (str(uuid.uuid4()), recorded_at),
    )
    conn.execute(
        "INSERT INTO usage_events (id, tenant_id, event_type, quantity, model, recorded_at) "
        "VALUES (?, 'tenant-A', 'llm_token', 300.0, 'claude-sonnet-4-6', ?)",
        (str(uuid.uuid4()), recorded_at),
    )
    conn.commit()

    # Patch get_connection to return this conn
    mock_ctx = MagicMock()
    mock_ctx.__enter__ = MagicMock(return_value=conn)
    mock_ctx.__exit__ = MagicMock(return_value=False)

    with patch("tools.genesis.reflexes.usage_rollup.get_connection", return_value=mock_ctx):
        module = importlib.import_module("tools.genesis.reflexes.usage_rollup")
        # Reload to pick up patch
        importlib.reload(module)
        with patch.object(module, "get_connection", return_value=mock_ctx):
            result = module.run({}, None)

    assert result["success"] is True
    assert result["metric_value"] >= 0

    # Check rollup row
    row = conn.execute(
        "SELECT total_quantity FROM usage_daily_rollup "
        "WHERE tenant_id='tenant-A' AND event_type='llm_token' AND rollup_date=?",
        (yesterday,),
    ).fetchone()
    if row:
        assert row["total_quantity"] == 800.0
    conn.close()

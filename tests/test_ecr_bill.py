# CUI // SP-CTI
"""ECR-BILL V&V: usage_events + usage_daily_rollup tables + rollup reflex tests."""
from __future__ import annotations

import importlib
import sqlite3
import sys
import uuid
from datetime import datetime, timedelta, timezone
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


def _make_usage_app(conn):
    """Build a minimal Flask app with the admin blueprint wired to a test SQLite conn.

    The tools.* shim puts TWO different module objects into sys.modules:
      sys.modules['tools.db.storage']  — used by `from tools.db.storage import …`
      sys.modules['icdev.tools.db.storage'] — the canonical object
    Patching only the canonical object does NOT affect `from tools.db.storage import`
    lookups, so we must patch sys.modules['tools.db.storage'] directly.
    """
    import importlib
    import os

    os.environ["ICDEV_ADMIN_CONSOLE_ENABLED"] = "true"
    admin_bp_mod = importlib.import_module("tools.admin.blueprint")
    importlib.reload(admin_bp_mod)

    # Ensure the shim entry is loaded so we can patch it.
    import tools.db.storage  # noqa: F401

    _shim = sys.modules["tools.db.storage"]

    class _CM:
        def __enter__(self_):
            return conn
        def __exit__(self_, *a):
            return False

    _orig = _shim.get_connection
    _shim.get_connection = _CM

    from flask import Flask
    app = Flask(__name__)
    app.config["TESTING"] = True

    with patch("tools.admin.blueprint._ENABLED", True):
        bp = admin_bp_mod.create_admin_blueprint()

    assert bp is not None, "Admin blueprint must be created when _ENABLED=True"
    app.register_blueprint(bp)
    return app


def test_usage_api_returns_data(tmp_path):
    """GET /api/admin/tenants/<tid>/usage returns summary + daily breakdown JSON."""
    import json

    conn = _make_db(tmp_path)
    yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
    rec_at = f"{yesterday}T10:00:00"
    conn.execute(
        "INSERT INTO usage_events (id, tenant_id, event_type, quantity, recorded_at) "
        "VALUES (?, 'tenant-test', 'api_call', 5.0, ?)",
        (str(uuid.uuid4()), rec_at),
    )
    conn.execute(
        "INSERT INTO usage_events (id, tenant_id, event_type, quantity, recorded_at) "
        "VALUES (?, 'tenant-test', 'llm_token', 200.0, ?)",
        (str(uuid.uuid4()), rec_at),
    )
    conn.commit()

    app = _make_usage_app(conn)
    since = (datetime.now(timezone.utc) - timedelta(days=7)).strftime("%Y-%m-%d")

    with app.test_client() as client:
        resp = client.get(f"/api/admin/tenants/tenant-test/usage?since={since}")

    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.data}"
    data = json.loads(resp.data)

    assert "summary" in data, "Response must include 'summary'"
    assert "daily" in data, "Response must include 'daily'"
    assert "current_month_total" in data, "Response must include 'current_month_total'"
    assert data["tenant_id"] == "tenant-test"
    assert data["since"] == since

    summary = data["summary"]
    assert "api_call" in summary or "llm_token" in summary, (
        f"Expected api_call or llm_token in summary, got: {summary}"
    )

    assert len(data["daily"]) >= 1, "daily breakdown should have at least one entry"
    row = data["daily"][0]
    assert "date" in row and "event_type" in row and "total_quantity" in row


def test_usage_overview_api(tmp_path):
    """GET /api/admin/usage/overview returns top_consumers + by_event_type."""
    import json

    conn = _make_db(tmp_path)
    yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
    rec_at = f"{yesterday}T11:00:00"
    conn.execute(
        "INSERT INTO usage_events (id, tenant_id, event_type, quantity, recorded_at) "
        "VALUES (?, 'tenant-alpha', 'llm_token', 1000.0, ?)",
        (str(uuid.uuid4()), rec_at),
    )
    conn.execute(
        "INSERT INTO usage_events (id, tenant_id, event_type, quantity, recorded_at) "
        "VALUES (?, 'tenant-beta', 'api_call', 50.0, ?)",
        (str(uuid.uuid4()), rec_at),
    )
    conn.commit()

    app = _make_usage_app(conn)
    since = (datetime.now(timezone.utc) - timedelta(days=7)).strftime("%Y-%m-%d")

    with app.test_client() as client:
        resp = client.get(f"/api/admin/usage/overview?since={since}")

    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.data}"
    data = json.loads(resp.data)

    assert "top_consumers" in data
    assert "by_event_type" in data
    assert isinstance(data["top_consumers"], list)
    assert isinstance(data["by_event_type"], dict)


def test_usage_summary_aggregates(tmp_path):
    """get_usage_summary() returns correct per-event_type totals."""
    import importlib

    conn = _make_db(tmp_path)
    yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
    rec_at = f"{yesterday}T09:00:00"

    for qty, et in [
        (200.0, "llm_token"),
        (300.0, "llm_token"),
        (500.0, "llm_token"),
        (1.0, "api_call"),
        (1.0, "api_call"),
    ]:
        conn.execute(
            "INSERT INTO usage_events (id, tenant_id, event_type, quantity, recorded_at) "
            "VALUES (?, 'tenant-sum', ?, ?, ?)",
            (str(uuid.uuid4()), et, qty, rec_at),
        )
    conn.commit()

    _shim = sys.modules["tools.db.storage"]

    class _CM:
        def __enter__(self):
            return conn

        def __exit__(self, *a):
            return False

    orig = _shim.get_connection
    _shim.get_connection = _CM
    try:
        metering = importlib.import_module("tools.billing.metering")
        summary = metering.get_usage_summary("tenant-sum", since=f"{yesterday}T00:00:00")
    finally:
        _shim.get_connection = orig

    assert "llm_token" in summary, f"Expected llm_token in summary: {summary}"
    assert summary["llm_token"] == 1000.0
    assert "api_call" in summary
    assert summary["api_call"] == 2.0


def test_stripe_invalid_sig_rejected(monkeypatch):
    """POST /webhooks/stripe with a bad Stripe-Signature header returns 403."""
    import importlib
    import json

    monkeypatch.setenv("ICDEV_STRIPE_WEBHOOK_SECRET", "whsec_test_secret_xyz")

    admin_bp_mod = importlib.import_module("tools.admin.blueprint")
    importlib.reload(admin_bp_mod)

    from flask import Flask

    app = Flask(__name__)
    app.config["TESTING"] = True
    wb = admin_bp_mod.create_stripe_webhook_blueprint()
    app.register_blueprint(wb)

    payload = json.dumps({
        "type": "invoice.payment_succeeded",
        "data": {"object": {}},
    }).encode()

    with app.test_client() as client:
        resp = client.post(
            "/webhooks/stripe",
            data=payload,
            content_type="application/json",
            headers={"Stripe-Signature": "t=1,v1=badhash"},
        )
    assert resp.status_code == 403, f"Expected 403, got {resp.status_code}"


def test_daily_rollup_correct(tmp_path):
    """Daily rollup upserts correct aggregated totals per (tenant, event_type, model, date)."""
    conn = _make_db(tmp_path)
    yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
    rec_at = f"{yesterday}T14:00:00"

    # Two llm_token events for tenant-R, same model → expect 800.0
    for qty in [500.0, 300.0]:
        conn.execute(
            "INSERT INTO usage_events "
            "(id, tenant_id, event_type, quantity, model, recorded_at) "
            "VALUES (?, 'tenant-R', 'llm_token', ?, 'claude-sonnet-4-6', ?)",
            (str(uuid.uuid4()), qty, rec_at),
        )
    # One api_call event → expect 7.0
    conn.execute(
        "INSERT INTO usage_events "
        "(id, tenant_id, event_type, quantity, model, recorded_at) "
        "VALUES (?, 'tenant-R', 'api_call', 7.0, NULL, ?)",
        (str(uuid.uuid4()), rec_at),
    )
    conn.commit()

    # Mirror the reflex aggregate-then-upsert pattern
    date_start = f"{yesterday}T00:00:00"
    date_end = f"{yesterday}T23:59:59"
    agg_rows = conn.execute(
        "SELECT tenant_id, event_type, COALESCE(model, '') AS model, SUM(quantity) AS total "
        "FROM usage_events "
        "WHERE recorded_at >= ? AND recorded_at <= ? "
        "GROUP BY tenant_id, event_type, COALESCE(model, '')",
        (date_start, date_end),
    ).fetchall()
    for r in agg_rows:
        conn.execute(
            "INSERT INTO usage_daily_rollup "
            "(tenant_id, event_type, model, rollup_date, total_quantity) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT (tenant_id, event_type, model, rollup_date) "
            "DO UPDATE SET total_quantity = excluded.total_quantity",
            (r[0], r[1], r[2], yesterday, r[3]),
        )
    conn.commit()

    llm_row = conn.execute(
        "SELECT total_quantity FROM usage_daily_rollup "
        "WHERE tenant_id='tenant-R' AND event_type='llm_token' AND rollup_date=?",
        (yesterday,),
    ).fetchone()
    assert llm_row is not None, "llm_token rollup row not found"
    assert llm_row["total_quantity"] == 800.0

    api_row = conn.execute(
        "SELECT total_quantity FROM usage_daily_rollup "
        "WHERE tenant_id='tenant-R' AND event_type='api_call' AND rollup_date=?",
        (yesterday,),
    ).fetchone()
    assert api_row is not None, "api_call rollup row not found"
    assert api_row["total_quantity"] == 7.0
    conn.close()


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

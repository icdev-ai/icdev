# CUI // SP-CTI
"""Unit tests for BDC ISA expiry alerting.

Test: insert ISA with expiry=now+25 days, run check_isa_expiry(),
verify canvas_events row written + Telegram called.
"""
from __future__ import annotations

import json
import sqlite3
import sys
import uuid
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

BASE_DIR = Path(__file__).resolve().parents[1]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))


# ---------------------------------------------------------------------------
# Wrapper: translating StorageConnection that never closes the shared conn.
# ---------------------------------------------------------------------------
# isa_expiry.py issues PG-native ``%s`` placeholders (e.g. ``... <= %s``); a raw
# sqlite3.Connection bypasses the ``%s`` -> ``?`` translator and raises
# ``sqlite3.OperationalError: near "%"``. Wrapping the in-memory conn in
# ``StorageConnection(raw, "sqlite")`` translates exactly as production does
# (the pattern bdr-sec-3 used in tests/test_cato_twin.py). The one wrinkle here:
# isa_expiry opens a FRESH get_connection() per call and closes it in a finally,
# so a real close() would destroy the shared ``:memory:`` DB between calls — the
# ``close()`` override keeps it alive for the duration of a check_isa_expiry() run.

from tools.db.storage import StorageConnection  # noqa: E402


class _NoClose(StorageConnection):
    """Translating StorageConnection over an in-memory sqlite conn; close() no-ops."""

    def __init__(self, conn: sqlite3.Connection):
        super().__init__(conn, "sqlite")

    def close(self):
        pass  # keep the shared :memory: connection alive across get_connection() calls


# ---------------------------------------------------------------------------
# Fixtures — in-memory SQLite for both BDC DB and main icdev DB
# ---------------------------------------------------------------------------

@pytest.fixture()
def bdc_db():
    """In-memory BDC database with bd_isa_tracker schema."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS bd_isa_tracker (
            id              TEXT PRIMARY KEY,
            design_id       TEXT,
            interconnection_id TEXT NOT NULL,
            isa_doc_id      TEXT,
            status          TEXT DEFAULT 'draft',
            expiry_date     TEXT,
            review_date     TEXT,
            owner           TEXT,
            notes           TEXT,
            created_at      TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at      TEXT DEFAULT CURRENT_TIMESTAMP
        );
    """)
    conn.commit()
    return conn


@pytest.fixture()
def main_db():
    """In-memory main DB with canvas_events table."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS canvas_events (
            id           TEXT PRIMARY KEY,
            source_canvas TEXT NOT NULL,
            target_canvas TEXT,
            event_type   TEXT NOT NULL,
            payload_json TEXT NOT NULL DEFAULT '{}',
            created_at   TEXT NOT NULL,
            consumed_at  TEXT
        );
    """)
    conn.commit()
    return conn


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_ensure_isa_expiry_column_adds_column(bdc_db):
    """ensure_isa_expiry_column() should add isa_expiry_date column without error."""
    with patch("tools.boundary_canvas.db.init_db.get_connection", side_effect=lambda: _NoClose(bdc_db)):
        from tools.boundary_canvas.isa_expiry import ensure_isa_expiry_column
        ensure_isa_expiry_column()

    cols = [row[1] for row in bdc_db.execute("PRAGMA table_info(bd_isa_tracker)").fetchall()]
    assert "isa_expiry_date" in cols


def test_ensure_isa_expiry_column_idempotent(bdc_db):
    """Calling ensure_isa_expiry_column() twice must not raise."""
    with patch("tools.boundary_canvas.db.init_db.get_connection", side_effect=lambda: _NoClose(bdc_db)):
        from tools.boundary_canvas.isa_expiry import ensure_isa_expiry_column
        ensure_isa_expiry_column()
        ensure_isa_expiry_column()  # second call must be a no-op


def test_check_isa_expiry_publishes_event_and_sends_notification(bdc_db, main_db):
    """ISA expiring in 25 days → canvas event + Telegram notification."""
    expiry = (date.today() + timedelta(days=25)).isoformat()
    isa_id = str(uuid.uuid4())

    # Seed the ISA into BDC DB
    bdc_db.execute(
        "INSERT INTO bd_isa_tracker (id, design_id, interconnection_id, isa_doc_id, status, expiry_date, owner) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (isa_id, "design-001", "ic-001", "ISA-2026-001", "active", expiry, "alice@example.com"),
    )
    bdc_db.commit()

    tg_calls = []

    def fake_tg_send(title, body, severity="info", **kwargs):
        tg_calls.append({"title": title, "body": body, "severity": severity})
        return {"status": "sent"}

    with (
        patch("tools.boundary_canvas.db.init_db.get_connection", side_effect=lambda: _NoClose(bdc_db)),
        patch("tools.db.storage.get_connection", side_effect=lambda **kw: _NoClose(main_db)),
        patch("tools.canvas.event_bus.get_connection", side_effect=lambda **kw: _NoClose(main_db)),
        patch("tools.notifications.adapters.telegram.send", side_effect=fake_tg_send),
    ):
        # Reload module to pick up patched get_connection
        import importlib
        import tools.boundary_canvas.isa_expiry as mod
        importlib.reload(mod)

        result = mod.check_isa_expiry()

    assert result["isas_checked"] == 1, f"Expected 1 ISA checked, got {result['isas_checked']}"
    assert result["events_published"] == 1, f"Expected 1 canvas event, got {result['events_published']}"
    assert result["notifications_sent"] == 1, f"Expected 1 Telegram notification, got {result['notifications_sent']}"

    # Verify canvas_events row
    event_row = main_db.execute(
        "SELECT * FROM canvas_events WHERE event_type = 'isa_expiring_soon'"
    ).fetchone()
    assert event_row is not None, "canvas_events row missing"
    payload = json.loads(event_row["payload_json"])
    assert payload["isa_id"] == isa_id
    # Allow ±1 for local-vs-UTC date boundary
    assert payload["days_until_expiry"] in (24, 25, 26)

    # Verify Telegram was called
    assert len(tg_calls) == 1
    # Body contains the days count (24–26 depending on local/UTC boundary)
    assert any(str(d) in tg_calls[0]["body"] for d in (24, 25, 26))
    assert tg_calls[0]["severity"] == "warning"


def test_check_isa_expiry_no_notification_beyond_30_days(bdc_db, main_db):
    """ISA expiring in 60 days → canvas event only, no Telegram."""
    expiry = (date.today() + timedelta(days=60)).isoformat()
    isa_id = str(uuid.uuid4())
    bdc_db.execute(
        "INSERT INTO bd_isa_tracker (id, design_id, interconnection_id, isa_doc_id, status, expiry_date) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (isa_id, "design-002", "ic-002", "ISA-2026-002", "active", expiry),
    )
    bdc_db.commit()

    tg_calls = []

    def fake_tg_send(title, body, **kwargs):
        tg_calls.append(body)
        return {"status": "sent"}

    with (
        patch("tools.boundary_canvas.db.init_db.get_connection", side_effect=lambda: _NoClose(bdc_db)),
        patch("tools.db.storage.get_connection", side_effect=lambda **kw: _NoClose(main_db)),
        # event_bus resolves get_connection from its own namespace — patch it too
        # (as the sibling publishes-event test does) so publish() writes to the
        # controlled main_db, not the real DB. Without this the event count is
        # order-dependent when the suite runs after other test files.
        patch("tools.canvas.event_bus.get_connection", side_effect=lambda **kw: _NoClose(main_db)),
        patch("tools.notifications.adapters.telegram.send", side_effect=fake_tg_send),
    ):
        import importlib
        import tools.boundary_canvas.isa_expiry as mod
        importlib.reload(mod)
        result = mod.check_isa_expiry()

    assert result["isas_checked"] == 1
    assert result["events_published"] == 1
    assert result["notifications_sent"] == 0
    assert tg_calls == [], "No Telegram should be sent for 60-day expiry"


def test_check_isa_expiry_skips_terminated(bdc_db, main_db):
    """Terminated ISAs should not trigger alerts."""
    expiry = (date.today() + timedelta(days=10)).isoformat()
    bdc_db.execute(
        "INSERT INTO bd_isa_tracker (id, design_id, interconnection_id, status, expiry_date) "
        "VALUES (?, ?, ?, ?, ?)",
        (str(uuid.uuid4()), "design-003", "ic-003", "terminated", expiry),
    )
    bdc_db.commit()

    with (
        patch("tools.boundary_canvas.db.init_db.get_connection", side_effect=lambda: _NoClose(bdc_db)),
        patch("tools.db.storage.get_connection", side_effect=lambda **kw: _NoClose(main_db)),
    ):
        import importlib
        import tools.boundary_canvas.isa_expiry as mod
        importlib.reload(mod)
        result = mod.check_isa_expiry()

    assert result["isas_checked"] == 0


def test_check_isa_expiry_dry_run(bdc_db, main_db):
    """dry_run=True must not publish events or send notifications."""
    expiry = (date.today() + timedelta(days=5)).isoformat()
    bdc_db.execute(
        "INSERT INTO bd_isa_tracker (id, design_id, interconnection_id, status, expiry_date) "
        "VALUES (?, ?, ?, ?, ?)",
        (str(uuid.uuid4()), "design-004", "ic-004", "active", expiry),
    )
    bdc_db.commit()

    tg_calls = []

    with (
        patch("tools.boundary_canvas.db.init_db.get_connection", side_effect=lambda: _NoClose(bdc_db)),
        patch("tools.db.storage.get_connection", side_effect=lambda **kw: _NoClose(main_db)),
        patch("tools.notifications.adapters.telegram.send", side_effect=lambda *a, **k: tg_calls.append(a)),
    ):
        import importlib
        import tools.boundary_canvas.isa_expiry as mod
        importlib.reload(mod)
        result = mod.check_isa_expiry(dry_run=True)

    assert result["dry_run"] is True
    assert result["events_published"] == 0
    assert result["notifications_sent"] == 0
    assert main_db.execute("SELECT COUNT(*) FROM canvas_events").fetchone()[0] == 0
    assert tg_calls == []


def test_genesis_reflex_bdc_isa_expiry():
    """Genesis reflex run() delegates to check_isa_expiry and returns expected keys."""
    mock_result = {
        "isas_checked": 3,
        "events_published": 3,
        "notifications_sent": 1,
        "dry_run": False,
    }
    with patch("tools.boundary_canvas.isa_expiry.check_isa_expiry", return_value=mock_result):
        from tools.genesis.reflexes.bdc_isa_expiry import run, CADENCE_HOURS
        result = run({})

    assert result["cadence_hours"] == 4
    assert CADENCE_HOURS == 4
    assert result["isas_checked"] == 3
    assert result["events_published"] == 3
    assert result["status"] == "ok"

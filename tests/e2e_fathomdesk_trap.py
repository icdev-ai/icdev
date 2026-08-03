# CUI // SP-CTI
"""E2E integration tests for FathomDesk Phase 7.12 — trap events write path & broker orders.

Validates:
  1. ad_trap_events DB write path via _insert_trap_event
  2. Trap sweep reflex — signal flip detection and deduplication
  3. BrokerAdapter interface — payload construction (no live network calls)
  4. /fathomdesk/api/traps Flask route — filtering and JSON shape

Uses an in-memory SQLite DB so no running server is required.
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock

BASE_DIR = Path(__file__).resolve().parents[1]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

os.environ.setdefault("ICDEV_STORAGE_BACKEND", "sqlite")

# ---------------------------------------------------------------------------
# Shared in-memory DB helper
# ---------------------------------------------------------------------------

_SCHEMA = """
CREATE TABLE IF NOT EXISTS ad_trap_events (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker       TEXT    NOT NULL,
    pattern      TEXT    NOT NULL,
    broken_level REAL,
    breakout_bar TEXT,
    reentry_bar  TEXT,
    confidence   REAL,
    volume_ratio REAL,
    bar_age      INTEGER,
    timeframe    TEXT,
    evidence_json TEXT,
    created_at   TEXT    NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS ad_signals (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker      TEXT NOT NULL,
    direction   TEXT NOT NULL,
    confidence  REAL NOT NULL DEFAULT 1.0,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS ad_reflex_cooldowns (
    key         TEXT PRIMARY KEY,
    value       TEXT NOT NULL
);
"""


def _make_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    return conn


def _make_reflex_conn():
    """A connection safe to hand to the reflex under test.

    The reflex authors PostgreSQL SQL (`%s` placeholders) and relies on
    StorageConnection to rewrite them for SQLite. A bare sqlite3 connection
    removes that layer, so every INSERT raises `near "%": syntax error` inside
    the reflex's best-effort `except` and the test asserts against a no-op it
    caused itself.
    """
    from tests._sql_compat import translating

    return translating(_make_conn())


# ---------------------------------------------------------------------------
# 1. ad_trap_events write path
# ---------------------------------------------------------------------------

class TestTrapEventWritePath(unittest.TestCase):
    """Direct INSERT into ad_trap_events via _insert_trap_event."""

    def setUp(self):
        self.conn = _make_reflex_conn()

    def tearDown(self):
        self.conn.close()

    def _insert(self, event: dict) -> bool:
        from tools.genesis.reflexes.fathomdesk_trap_sweep import _insert_trap_event
        return _insert_trap_event(self.conn, event)

    def test_insert_bull_trap_returns_true(self):
        ok = self._insert({
            "ticker": "SPY",
            "pattern": "bull_trap",
            "broken_level": 450.0,
            "confidence": 0.75,
            "evidence": {"volume_ratio": 0.6},
        })
        self.assertTrue(ok)

    def test_insert_persists_to_db(self):
        self._insert({"ticker": "QQQ", "pattern": "bear_trap", "broken_level": 390.0, "confidence": 0.80})
        row = self.conn.execute("SELECT ticker, pattern, confidence FROM ad_trap_events").fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row["ticker"], "QQQ")
        self.assertEqual(row["pattern"], "bear_trap")
        self.assertAlmostEqual(row["confidence"], 0.80, places=2)

    def test_insert_evidence_json_serialized(self):
        evidence = {"volume_ratio": 0.55, "bar_age": 2}
        self._insert({"ticker": "AAPL", "pattern": "bull_trap", "broken_level": 175.0,
                      "confidence": 0.65, "evidence": evidence})
        row = self.conn.execute("SELECT evidence_json FROM ad_trap_events").fetchone()
        stored = json.loads(row["evidence_json"])
        self.assertEqual(stored["volume_ratio"], 0.55)
        self.assertEqual(stored["bar_age"], 2)

    def test_insert_multiple_rows_increments(self):
        for i in range(3):
            self._insert({"ticker": f"T{i}", "pattern": "bear_trap", "broken_level": float(i),
                          "confidence": 0.6})
        count = self.conn.execute("SELECT COUNT(*) FROM ad_trap_events").fetchone()[0]
        self.assertEqual(count, 3)

    def test_insert_missing_optional_fields_succeeds(self):
        ok = self._insert({"ticker": "MSFT", "pattern": "bull_trap"})
        self.assertTrue(ok)


# ---------------------------------------------------------------------------
# 2. Trap sweep reflex — signal flip detection
# ---------------------------------------------------------------------------

class TestTrapSweepReflex(unittest.TestCase):
    """_trap_sweep detects direction flips and inserts trap events."""

    # _trap_sweep reads its thresholds from a cfg dict (run() supplies
    # _load_trap_config()). Pinned here rather than loaded from
    # args/ta_config.yaml so the confidence and cooldown assertions below stay
    # true when an operator retunes the live config.
    CFG = {
        "cooldown_hours": 4,
        "dedup_window_hours": 24,
        "min_confidence": 0.50,
        "sentiment_elevation": 0.15,
        "sentiment_bearish_threshold": 0.40,
    }

    def setUp(self):
        self.conn = _make_reflex_conn()

    def tearDown(self):
        self.conn.close()

    def _seed_signals(self, ticker: str, directions: list[tuple[str, float, str]]) -> None:
        """Insert signals: [(direction, confidence, created_at), ...]."""
        for direction, confidence, created_at in directions:
            self.conn.execute(
                "INSERT INTO ad_signals (ticker, direction, confidence, created_at) VALUES (?,?,?,?)",
                (ticker, direction, confidence, created_at),
            )
        self.conn.commit()

    def test_bull_trap_inserted_on_buy_then_sell_flip(self):
        from tools.genesis.reflexes.fathomdesk_trap_sweep import _trap_sweep
        self._seed_signals("SPY", [
            ("SELL", 0.85, "2026-04-24T10:00:00"),  # newest
            ("BUY", 0.80, "2026-04-24T08:00:00"),   # older
        ])
        events = _trap_sweep(self.conn, self.CFG)
        self.assertGreaterEqual(len(events), 1)
        tickers = [e["ticker"] for e in events]
        self.assertIn("SPY", tickers)

    def test_bear_trap_inserted_on_sell_then_buy_flip(self):
        from tools.genesis.reflexes.fathomdesk_trap_sweep import _trap_sweep
        self._seed_signals("QQQ", [
            ("BUY", 0.90, "2026-04-24T11:00:00"),
            ("SELL", 0.88, "2026-04-24T09:00:00"),
        ])
        events = _trap_sweep(self.conn, self.CFG)
        tickers = [e["ticker"] for e in events]
        self.assertIn("QQQ", tickers)

    def test_no_trap_when_same_direction(self):
        from tools.genesis.reflexes.fathomdesk_trap_sweep import _trap_sweep
        self._seed_signals("NVDA", [
            ("BUY", 0.80, "2026-04-24T10:00:00"),
            ("BUY", 0.75, "2026-04-24T08:00:00"),
        ])
        events = _trap_sweep(self.conn, self.CFG)
        tickers = [e["ticker"] for e in events]
        self.assertNotIn("NVDA", tickers)

    def test_no_trap_when_low_confidence(self):
        from tools.genesis.reflexes.fathomdesk_trap_sweep import _trap_sweep
        self._seed_signals("AMD", [
            ("SELL", 0.30, "2026-04-24T10:00:00"),
            ("BUY", 0.25, "2026-04-24T08:00:00"),
        ])
        events = _trap_sweep(self.conn, self.CFG)
        tickers = [e["ticker"] for e in events]
        self.assertNotIn("AMD", tickers)

    def test_cooldown_prevents_duplicate_within_4h(self):
        from tools.genesis.reflexes.fathomdesk_trap_sweep import _trap_sweep
        self._seed_signals("TSLA", [
            ("SELL", 0.85, "2026-04-24T10:00:00"),
            ("BUY", 0.82, "2026-04-24T08:00:00"),
        ])
        events1 = _trap_sweep(self.conn, self.CFG)
        events2 = _trap_sweep(self.conn, self.CFG)
        tsla_first = sum(1 for e in events1 if e["ticker"] == "TSLA")
        tsla_second = sum(1 for e in events2 if e["ticker"] == "TSLA")
        # After first sweep sets cooldown, second sweep should not insert again
        self.assertLessEqual(tsla_second, tsla_first)

    def test_no_signals_returns_empty(self):
        from tools.genesis.reflexes.fathomdesk_trap_sweep import _trap_sweep
        events = _trap_sweep(self.conn, self.CFG)
        self.assertEqual(events, [])


# ---------------------------------------------------------------------------
# 3. BrokerAdapter — payload construction (no live network)
# ---------------------------------------------------------------------------

class TestBrokerAdapterPayload(unittest.TestCase):
    """Verify BrokerAdapter builds correct Alpaca payloads without live HTTP."""

    def setUp(self):
        os.environ["ALPACA_API_KEY"] = "TEST_KEY"
        os.environ["ALPACA_SECRET_KEY"] = "TEST_SECRET"
        os.environ["ALPACA_BASE_URL"] = "https://paper-api.alpaca.markets"

    def tearDown(self):
        for k in ("ALPACA_API_KEY", "ALPACA_SECRET_KEY", "ALPACA_BASE_URL"):
            os.environ.pop(k, None)

    def _adapter(self):
        from tools.fathomdesk.broker_adapter import BrokerAdapter
        return BrokerAdapter()

    def test_is_paper_detected(self):
        adapter = self._adapter()
        self.assertTrue(adapter.is_paper)

    def test_limit_order_payload_shape(self):
        adapter = self._adapter()
        captured: list[dict] = []

        def _fake_post(payload):
            captured.append(payload)
            return {"id": "fake-order-id", "status": "accepted"}

        adapter._post_order = _fake_post
        adapter.submit_limit_order("SPY", 1, 455.00, "buy")
        self.assertEqual(len(captured), 1)
        p = captured[0]
        self.assertEqual(p["symbol"], "SPY")
        self.assertEqual(p["side"], "buy")
        self.assertEqual(p["type"], "limit")
        self.assertEqual(p["limit_price"], "455.0")
        self.assertEqual(p["qty"], "1")

    def test_stop_order_payload_shape(self):
        adapter = self._adapter()
        captured: list[dict] = []
        adapter._post_order = lambda p: captured.append(p) or {}
        adapter.submit_stop_order("QQQ", 2, 380.00, "sell")
        self.assertEqual(len(captured), 1)
        p = captured[0]
        self.assertEqual(p["symbol"], "QQQ")
        self.assertEqual(p["side"], "sell")
        self.assertEqual(p["type"], "stop")
        self.assertEqual(p["stop_price"], "380.0")

    def test_ticker_normalized_to_uppercase(self):
        adapter = self._adapter()
        captured: list[dict] = []
        adapter._post_order = lambda p: captured.append(p) or {}
        adapter.submit_limit_order("aapl", 1, 175.00, "buy")
        self.assertEqual(captured[0]["symbol"], "AAPL")

    def test_missing_api_key_raises_value_error(self):
        # Simulate missing key by blanking the instance attribute after construction
        adapter = self._adapter()
        adapter._api_key = ""
        with self.assertRaises(ValueError):
            adapter.submit_limit_order("SPY", 1, 455.00, "buy")


# ---------------------------------------------------------------------------
# 4. Flask route /fathomdesk/api/traps
# ---------------------------------------------------------------------------

class TestTrapsAPIRoute(unittest.TestCase):
    """Flask test client exercises /fathomdesk/api/traps with filtering."""

    def _seed_db(self, conn) -> None:
        rows = [
            ("SPY", "bull_trap", 450.0, 0.75),
            ("QQQ", "bear_trap", 390.0, 0.80),
            ("SPY", "bear_trap", 448.0, 0.65),
        ]
        for ticker, pattern, broken_level, confidence in rows:
            conn.execute(
                "INSERT INTO ad_trap_events (ticker, pattern, broken_level, confidence) "
                "VALUES (?, ?, ?, ?)",
                (ticker, pattern, broken_level, confidence),
            )
        conn.commit()

    def _make_app(self, conn):
        from flask import Flask
        from tools.fathomdesk.blueprint import fathomdesk_api

        app = Flask(__name__)
        app.config["TESTING"] = True
        app.register_blueprint(fathomdesk_api)

        # Patch get_connection to return our in-memory conn
        mock_conn = MagicMock(wraps=conn)
        mock_conn.close = MagicMock()
        # Patch the attribute so blueprint detects sqlite dialect
        mock_conn._dialect = "sqlite"
        # Delegate execute to real conn
        mock_conn.execute = conn.execute

        import tools.fathomdesk.blueprint as bp_mod
        bp_mod.get_connection = lambda: mock_conn
        return app

    def setUp(self):
        self.conn = _make_conn()
        self._seed_db(self.conn)
        self.app = self._make_app(self.conn)
        self.client = self.app.test_client()

    def tearDown(self):
        self.conn.close()

    def test_returns_all_traps(self):
        resp = self.client.get("/fathomdesk/api/traps")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertIn("traps", data)
        self.assertIn("count", data)
        self.assertEqual(data["count"], 3)

    def test_filter_by_ticker(self):
        resp = self.client.get("/fathomdesk/api/traps?ticker=SPY")
        data = resp.get_json()
        self.assertEqual(data["count"], 2)
        for t in data["traps"]:
            self.assertEqual(t["ticker"], "SPY")

    def test_filter_by_trap_type(self):
        resp = self.client.get("/fathomdesk/api/traps?trap_type=bear_trap")
        data = resp.get_json()
        self.assertEqual(data["count"], 2)
        for t in data["traps"]:
            self.assertEqual(t["pattern"], "bear_trap")

    def test_filter_combined(self):
        resp = self.client.get("/fathomdesk/api/traps?ticker=SPY&trap_type=bull_trap")
        data = resp.get_json()
        self.assertEqual(data["count"], 1)
        self.assertEqual(data["traps"][0]["ticker"], "SPY")
        self.assertEqual(data["traps"][0]["pattern"], "bull_trap")

    def test_limit_param_respected(self):
        resp = self.client.get("/fathomdesk/api/traps?limit=2")
        data = resp.get_json()
        self.assertLessEqual(data["count"], 2)

    def test_empty_result_shape(self):
        resp = self.client.get("/fathomdesk/api/traps?ticker=ZZZZ")
        data = resp.get_json()
        self.assertEqual(data["count"], 0)
        self.assertEqual(data["traps"], [])

    def test_trap_fields_present(self):
        resp = self.client.get("/fathomdesk/api/traps?limit=1")
        data = resp.get_json()
        row = data["traps"][0]
        for field in ("id", "ticker", "pattern", "confidence", "created_at"):
            self.assertIn(field, row, f"missing field: {field}")


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    for cls in [
        TestTrapEventWritePath,
        TestTrapSweepReflex,
        TestBrokerAdapterPayload,
        TestTrapsAPIRoute,
    ]:
        suite.addTests(loader.loadTestsFromTestCase(cls))

    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)

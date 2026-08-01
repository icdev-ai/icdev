#!/usr/bin/env python3
"""nav-plat-02 — FathomDesk broker live-order safety guard.

Regression tests for the financial-safety hardening of
``tools/fathomdesk/broker_adapter.py``:

  1. Paper vs. live is decided by the EXPLICIT ``ALPACA_TRADING_MODE`` env var,
     never by a substring of the base URL.
  2. A live-mode submit without ``live_confirmed=True`` raises before any HTTP.
  3. A mode/URL mismatch raises loudly at construction.
  4. The adapter stays web-unexposed — no Flask blueprint/route/app module
     reaches ``submit_limit_order`` / ``submit_stop_order``.
"""

import os
import re
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

# Alpaca env vars we mutate; snapshot/restore so tests don't pollute each other.
_ALPACA_ENV_KEYS = (
    "ALPACA_API_KEY",
    "ALPACA_SECRET_KEY",
    "ALPACA_BASE_URL",
    "ALPACA_TRADING_MODE",
)

PAPER_URL = "https://paper-api.alpaca.markets"
LIVE_URL = "https://api.alpaca.markets"


def _fresh_adapter():
    """Import + construct a BrokerAdapter with the current env."""
    from tools.fathomdesk.broker_adapter import BrokerAdapter
    return BrokerAdapter()


class _EnvIsolatedTestCase(unittest.TestCase):
    """Snapshot/restore the Alpaca env so mode leakage can't cross tests."""

    def setUp(self):
        self._saved = {k: os.environ.get(k) for k in _ALPACA_ENV_KEYS}
        for k in _ALPACA_ENV_KEYS:
            os.environ.pop(k, None)
        # Credentials are always present so construction never trips on keys.
        os.environ["ALPACA_API_KEY"] = "TEST_KEY"
        os.environ["ALPACA_SECRET_KEY"] = "TEST_SECRET"

    def tearDown(self):
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


class TestDefaultMode(_EnvIsolatedTestCase):
    def test_no_env_defaults_to_paper(self):
        # No ALPACA_BASE_URL, no ALPACA_TRADING_MODE -> paper by default.
        adapter = _fresh_adapter()
        self.assertTrue(adapter.is_paper)
        self.assertFalse(adapter.is_live)

    def test_explicit_paper_mode_with_paper_url(self):
        os.environ["ALPACA_TRADING_MODE"] = "paper"
        os.environ["ALPACA_BASE_URL"] = PAPER_URL
        adapter = _fresh_adapter()
        self.assertTrue(adapter.is_paper)


class TestModeUrlMismatch(_EnvIsolatedTestCase):
    def test_paper_mode_with_live_url_raises(self):
        from tools.fathomdesk.broker_adapter import BrokerConfigError
        os.environ["ALPACA_TRADING_MODE"] = "paper"
        os.environ["ALPACA_BASE_URL"] = LIVE_URL
        with self.assertRaises(BrokerConfigError):
            _fresh_adapter()

    def test_live_mode_with_paper_url_raises(self):
        from tools.fathomdesk.broker_adapter import BrokerConfigError
        os.environ["ALPACA_TRADING_MODE"] = "live"
        os.environ["ALPACA_BASE_URL"] = PAPER_URL
        with self.assertRaises(BrokerConfigError):
            _fresh_adapter()

    def test_invalid_mode_raises(self):
        from tools.fathomdesk.broker_adapter import BrokerConfigError
        os.environ["ALPACA_TRADING_MODE"] = "sandbox"
        os.environ["ALPACA_BASE_URL"] = PAPER_URL
        with self.assertRaises(BrokerConfigError):
            _fresh_adapter()

    def test_paper_url_substring_does_not_promote_to_live(self):
        # A misconfigured URL that lacks 'paper' must NOT silently go live —
        # with mode=paper it must raise rather than execute against a live host.
        from tools.fathomdesk.broker_adapter import BrokerConfigError
        os.environ["ALPACA_TRADING_MODE"] = "paper"
        os.environ["ALPACA_BASE_URL"] = "https://api.alpaca.markets"
        with self.assertRaises(BrokerConfigError):
            _fresh_adapter()


class TestLiveConfirmationGuard(_EnvIsolatedTestCase):
    def setUp(self):
        super().setUp()
        os.environ["ALPACA_TRADING_MODE"] = "live"
        os.environ["ALPACA_BASE_URL"] = LIVE_URL

    def test_live_limit_without_confirmation_raises(self):
        from tools.fathomdesk.broker_adapter import LiveOrderConfirmationError
        adapter = _fresh_adapter()
        posted = []
        adapter._post_order = lambda p: posted.append(p) or {}
        with self.assertRaises(LiveOrderConfirmationError):
            adapter.submit_limit_order("SPY", 1, 455.00, "buy")
        # Guard fires BEFORE any HTTP request is issued.
        self.assertEqual(posted, [])

    def test_live_stop_without_confirmation_raises(self):
        from tools.fathomdesk.broker_adapter import LiveOrderConfirmationError
        adapter = _fresh_adapter()
        posted = []
        adapter._post_order = lambda p: posted.append(p) or {}
        with self.assertRaises(LiveOrderConfirmationError):
            adapter.submit_stop_order("QQQ", 2, 380.00, "sell")
        self.assertEqual(posted, [])

    def test_live_limit_with_confirmation_proceeds(self):
        adapter = _fresh_adapter()
        posted = []
        adapter._post_order = lambda p: posted.append(p) or {"id": "ok"}
        result = adapter.submit_limit_order("SPY", 1, 455.00, "buy", live_confirmed=True)
        self.assertEqual(result, {"id": "ok"})
        self.assertEqual(len(posted), 1)
        self.assertEqual(posted[0]["symbol"], "SPY")
        self.assertEqual(posted[0]["type"], "limit")

    def test_live_stop_with_confirmation_proceeds(self):
        adapter = _fresh_adapter()
        posted = []
        adapter._post_order = lambda p: posted.append(p) or {"id": "ok"}
        adapter.submit_stop_order("QQQ", 2, 380.00, "sell", live_confirmed=True)
        self.assertEqual(len(posted), 1)
        self.assertEqual(posted[0]["type"], "stop")


class TestPaperModeUnaffected(_EnvIsolatedTestCase):
    def setUp(self):
        super().setUp()
        os.environ["ALPACA_TRADING_MODE"] = "paper"
        os.environ["ALPACA_BASE_URL"] = PAPER_URL

    def test_paper_submit_without_confirmation_proceeds(self):
        # Paper flow is unchanged: no live_confirmed needed.
        adapter = _fresh_adapter()
        posted = []
        adapter._post_order = lambda p: posted.append(p) or {"id": "paper"}
        adapter.submit_limit_order("SPY", 1, 455.00, "buy")
        adapter.submit_stop_order("QQQ", 2, 380.00, "sell")
        self.assertEqual(len(posted), 2)


class TestBrokerNotWebExposed(unittest.TestCase):
    """Source scan: no web-facing module may reach the order submit methods."""

    _SUBMIT_RE = re.compile(r"submit_(?:limit|stop)_order")

    def _scan_targets(self):
        targets = []
        # All Flask blueprint modules.
        targets.extend((REPO_ROOT / "tools").rglob("blueprint.py"))
        targets.extend((REPO_ROOT / "icdev" / "tools").rglob("blueprint.py"))
        # The main dashboard app.
        for app in (
            REPO_ROOT / "tools" / "dashboard" / "app.py",
            REPO_ROOT / "icdev" / "tools" / "dashboard" / "app.py",
        ):
            if app.exists():
                targets.append(app)
        # Generated child apps / apps tree, if present.
        apps_dir = REPO_ROOT / "apps"
        if apps_dir.exists():
            targets.extend(apps_dir.rglob("*.py"))
        return targets

    def test_no_web_route_reaches_broker_submit(self):
        offenders = []
        for path in self._scan_targets():
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if self._SUBMIT_RE.search(text):
                offenders.append(str(path.relative_to(REPO_ROOT)))
        self.assertEqual(
            offenders, [],
            "Broker order submission must remain web-unexposed; found "
            f"submit_*_order references in web modules: {offenders}",
        )

    def test_scan_actually_covered_files(self):
        # Guard against a silently-empty scan (e.g. wrong repo root).
        self.assertTrue(
            len(self._scan_targets()) > 0,
            "Route-exposure scan found no target files — check REPO_ROOT.",
        )


if __name__ == "__main__":
    unittest.main()

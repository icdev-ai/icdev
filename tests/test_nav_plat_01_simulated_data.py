# CUI // SP-CTI
"""nav-plat-01 — FathomDesk synthetic-data honesty flag + banner.

The FathomDesk chart pulls OHLCV bars from ``market_data.fetch_bars``, which
falls back to a deterministic synthetic ("sample") generator whenever the live
Alpaca provider is unavailable. Each bar carries a per-bar ``source`` marker,
but the chart frontend ignored it — synthetic data rendered as real market
data in a financial UI.

The fix surfaces provenance at the top level of the ``/api/trading/chart``
response (``data_source`` / ``simulated`` / ``as_of``) and the template renders
a prominent, persistent "SIMULATED DATA" banner when ``simulated`` is true.

These tests cover:
  * ``_derive_chart_provenance`` collapses per-bar ``source`` markers correctly.
  * The chart endpoint emits top-level provenance:
      - Alpaca-unavailable (sample fallback) -> ``simulated: true`` + ``data_source: "sample"``.
      - live path (alpaca bars)              -> ``simulated: false``.
  * The FathomDesk template contains the banner element + toggle logic.

Note: ``tools/trading/`` is gitignored (FathomDesk trading modules ship outside
the committed tree), so ``market_data``/``volume_profile`` are absent in CI.
The endpoint test injects deterministic fakes for those modules; this mirrors
the deployed environment where the real modules exist.
"""
from __future__ import annotations

import sys
import types
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

import pytest

BASE_DIR = Path(__file__).resolve().parents[1]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

def _bar(source: str, is_delayed: bool, i: int = 0) -> dict:
    """Build a well-formed OHLCV bar carrying provenance markers."""
    base = 100.0 + i
    return {
        "t": (datetime.now(timezone.utc)).isoformat(),
        "o": base,
        "h": base * 1.02,
        "l": base * 0.98,
        "c": base * 1.01,
        "v": 1_000_000 + i,
        "source": source,
        "as_of": datetime.now(timezone.utc).isoformat(),
        "is_delayed": is_delayed,
    }


def _sample_bars(n: int = 5) -> list[dict]:
    return [_bar("sample", True, i) for i in range(n)]


def _alpaca_bars(n: int = 5) -> list[dict]:
    return [_bar("alpaca", False, i) for i in range(n)]


# ---------------------------------------------------------------------------
# _derive_chart_provenance — unit tests (no Flask app required)
# ---------------------------------------------------------------------------

class TestDeriveChartProvenance:
    def _derive(self):
        from tools.dashboard.app import _derive_chart_provenance
        return _derive_chart_provenance

    def test_sample_bars_flagged_simulated(self):
        prov = self._derive()(_sample_bars(3))
        assert prov["simulated"] is True
        assert prov["data_source"] == "sample"
        assert prov["as_of"]  # per-bar as_of surfaced

    def test_alpaca_bars_not_simulated(self):
        prov = self._derive()(_alpaca_bars(3))
        assert prov["simulated"] is False
        assert prov["data_source"] == "alpaca"

    def test_empty_bars_graceful(self):
        prov = self._derive()([])
        assert prov["simulated"] is False
        assert prov["data_source"] == "unknown"
        assert prov["as_of"] is None

    def test_mixed_bars_flag_simulated(self):
        prov = self._derive()(_alpaca_bars(2) + _sample_bars(2))
        assert prov["simulated"] is True
        assert prov["data_source"] == "mixed"

    def test_missing_source_key_defaults_unknown(self):
        prov = self._derive()([{"o": 1, "h": 1, "l": 1, "c": 1, "v": 1}])
        assert prov["simulated"] is False
        assert prov["data_source"] == "unknown"


# ---------------------------------------------------------------------------
# /api/trading/chart endpoint — integration tests
# ---------------------------------------------------------------------------

@contextmanager
def _fake_trading_modules(bars: list[dict]):
    """Inject deterministic fakes for the gitignored trading modules the chart
    route imports (market_data + the TA helpers), so the endpoint runs without
    the real (untracked) FathomDesk trading tree.
    """
    fakes: dict[str, types.ModuleType] = {}

    md = types.ModuleType("tools.trading.data.market_data")
    md.fetch_bars = lambda ticker, timeframe="1D", limit=120: list(bars)  # type: ignore[attr-defined]
    fakes["tools.trading.data.market_data"] = md

    vp = types.ModuleType("tools.trading.ta.volume_profile")
    vp.volume_profile = lambda bars, bucket_count=40: []  # type: ignore[attr-defined]
    fakes["tools.trading.ta.volume_profile"] = vp

    sw = types.ModuleType("tools.trading.ta.swings")
    sw.find_swings = lambda bars: []  # type: ignore[attr-defined]
    fakes["tools.trading.ta.swings"] = sw

    pt = types.ModuleType("tools.trading.ta.patterns")
    pt.detect_patterns = lambda bars: []  # type: ignore[attr-defined]
    fakes["tools.trading.ta.patterns"] = pt

    srmod = types.ModuleType("tools.trading.ta.support_resistance")
    srmod.compute_sr = lambda bars, swings=None: []  # type: ignore[attr-defined]
    fakes["tools.trading.ta.support_resistance"] = srmod

    saved = {name: sys.modules.get(name) for name in fakes}
    sys.modules.update(fakes)
    try:
        yield
    finally:
        for name, prev in saved.items():
            if prev is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = prev


@pytest.fixture(scope="module")
def client():
    app_mod = pytest.importorskip("tools.dashboard.app")
    auth_mod = pytest.importorskip("tools.dashboard.auth")

    # The /api/* auth hook validates session["user_id"] against dashboard_users.
    # A fresh worktree's data/icdev.db may not have that table, so stub the
    # lookup to return an active admin — auth is not what nav-plat-01 tests.
    _orig_get_user = auth_mod.get_user_by_id
    auth_mod.get_user_by_id = lambda uid: {  # type: ignore[assignment]
        "id": uid, "status": "active", "role": "admin",
        "tenant_id": "system", "clearance_level": "UNCLASSIFIED",
        "email": "admin@test.local", "display_name": "Test Admin",
    }

    app = app_mod.create_app(testing=True)
    app.config["TESTING"] = True
    c = app.test_client()
    with c.session_transaction() as sess:
        sess["user_id"] = "test-admin"
    try:
        yield c
    finally:
        auth_mod.get_user_by_id = _orig_get_user


class TestChartEndpointProvenance:
    def test_alpaca_unavailable_flags_simulated(self, client):
        """Sample fallback -> top-level simulated:true + data_source:'sample'."""
        with _fake_trading_modules(_sample_bars(4)):
            resp = client.get("/api/trading/chart/AAPL?tf=1D&limit=4")
        assert resp.status_code == 200, resp.get_data(as_text=True)
        data = resp.get_json()
        assert data.get("simulated") is True
        assert data.get("data_source") == "sample"
        assert data.get("as_of")

    def test_live_path_not_simulated(self, client):
        """Live alpaca bars -> simulated:false, data_source not 'sample'."""
        with _fake_trading_modules(_alpaca_bars(4)):
            resp = client.get("/api/trading/chart/AAPL?tf=1D&limit=4")
        assert resp.status_code == 200, resp.get_data(as_text=True)
        data = resp.get_json()
        assert data.get("simulated") is False
        assert data.get("data_source") == "alpaca"


# ---------------------------------------------------------------------------
# FathomDesk template — banner presence (source scan)
# ---------------------------------------------------------------------------

class TestFathomDeskBannerTemplate:
    def _template_text(self) -> str:
        path = BASE_DIR / "tools" / "dashboard" / "templates" / "fathomdesk.html"
        assert path.exists(), f"template missing: {path}"
        return path.read_text(encoding="utf-8")

    def test_banner_element_present(self):
        html = self._template_text()
        assert 'id="fd-sim-banner"' in html
        assert "Simulated data" in html or "SIMULATED DATA" in html.upper()

    def test_banner_toggle_logic_present(self):
        html = self._template_text()
        # JS reads the top-level `simulated` flag and toggles the banner.
        assert "fdUpdateSimBanner" in html
        assert "simulated" in html

    def test_banner_mirrored_to_icdev_twin(self):
        twin = BASE_DIR / "icdev" / "tools" / "dashboard" / "templates" / "fathomdesk.html"
        if not twin.exists():
            pytest.skip("icdev twin not present")
        text = twin.read_text(encoding="utf-8")
        assert 'id="fd-sim-banner"' in text
        assert "fdUpdateSimBanner" in text

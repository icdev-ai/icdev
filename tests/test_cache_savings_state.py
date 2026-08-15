#!/usr/bin/env python3
"""Cache savings must distinguish EMPTY from BROKEN. CUI // SP-CTI

The savings tile rendered a cold cache and a failed one identically: 0% in red,
zero entries, no explanation. On this platform cold is the COMMON case, because
``llm_response_cache`` is created UNLOGGED on PostgreSQL and PostgreSQL empties
unlogged tables on any unclean shutdown or crash recovery. So the reading a
human is most likely to see was the one carrying no information.

Deterministic by construction — every test drives a fake connection returning
exactly the rows it declares. No live database, no environment sensitivity.
"""
from __future__ import annotations

import pathlib
import sys

import pytest

_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from tools.cache_savings.savings import (  # noqa: E402
    STATE_COLD,
    STATE_DISABLED,
    STATE_EXPIRED,
    STATE_POPULATED,
    STATE_UNREACHABLE,
    _cache_state,
    get_savings_stats,
)


class FakeConn:
    """Returns `live` rows for the expiry-filtered query and `stored` for COUNT(*)."""

    def __init__(self, live=(), stored=0, raise_on_first=False):
        self.live, self.stored, self.raise_on_first = list(live), stored, raise_on_first
        self.calls = 0

    def execute(self, sql, params=()):
        self.calls += 1
        if self.raise_on_first and self.calls == 1:
            raise RuntimeError("relation llm_response_cache does not exist")
        rows = [(self.stored,)] if "COUNT(*) FROM llm_response_cache" in " ".join(sql.split()) else self.live

        class _Cur:
            def fetchall(self_inner):
                return rows

            def fetchone(self_inner):
                return rows[0] if rows else None
        return _Cur()

    def close(self):
        pass


# One live row: function, entries, hits, in_tok, out_tok, cache_write, cache_read
_LIVE = [("code_generation", 3, 9, 1000, 500, 200, 4000)]


# --------------------------------------------------------------------------- #
# The distinction that did not exist
# --------------------------------------------------------------------------- #

def test_cold_is_not_reported_the_same_as_broken():
    cold = get_savings_stats(conn=FakeConn(live=[], stored=0))
    broken = get_savings_stats(conn=FakeConn(raise_on_first=True))
    assert cold["state"] == STATE_COLD
    assert broken["state"] == STATE_UNREACHABLE
    assert cold["state"] != broken["state"], "the two must be distinguishable"


def test_expired_is_not_reported_as_cold():
    """Rows exist but all aged out — the cache is working, not empty.

    Only one of these is worth acting on, and before `stored_entries` existed
    both presented as a bare zero.
    """
    stats = get_savings_stats(conn=FakeConn(live=[], stored=42))
    assert stats["state"] == STATE_EXPIRED
    assert stats["stored_entries"] == 42
    assert stats["summary"]["total_entries"] == 0


def test_populated_reports_populated():
    stats = get_savings_stats(conn=FakeConn(live=_LIVE, stored=3))
    assert stats["state"] == STATE_POPULATED
    assert stats["summary"]["total_entries"] == 3


def test_every_state_carries_an_explanation_except_populated():
    """A state a human cannot act on is not much better than a bare zero."""
    from tools.cache_savings.savings import _STATE_DETAIL

    for state in (STATE_COLD, STATE_EXPIRED, STATE_DISABLED, STATE_UNREACHABLE):
        assert _STATE_DETAIL[state].strip(), state
    # populated needs no explanation - the numbers are the explanation.
    assert _STATE_DETAIL[STATE_POPULATED] == ""
    # And the live unreachable path really does carry its detail through.
    assert get_savings_stats(conn=FakeConn(raise_on_first=True))["state_detail"].strip()


def test_the_cold_explanation_names_the_actual_cause():
    """UNLOGGED is why this happens, and nobody would guess it from a 0%."""
    from tools.cache_savings.savings import _STATE_DETAIL

    detail = _STATE_DETAIL[STATE_COLD].lower()
    assert "unlogged" in detail
    assert "warmer" in detail, "tell the reader how to fix it"


@pytest.mark.parametrize("enabled,live,stored,expected", [
    (False, 0, 0, STATE_DISABLED),
    (False, 5, 5, STATE_DISABLED),   # disabled wins over any row count
    (True, 0, 0, STATE_COLD),
    (True, 0, 7, STATE_EXPIRED),
    (True, 2, 7, STATE_POPULATED),
])
def test_state_precedence(enabled, live, stored, expected):
    assert _cache_state(enabled, live, stored) == expected


def test_unreachable_never_claims_the_cache_is_merely_cold():
    """A failed query must not be laundered into 'nothing cached yet'."""
    stats = get_savings_stats(conn=FakeConn(raise_on_first=True))
    assert stats["state"] == STATE_UNREACHABLE
    assert stats["summary"]["total_entries"] == 0
    assert stats["enabled"] is False


# --------------------------------------------------------------------------- #
# The reflex
# --------------------------------------------------------------------------- #

def test_reflex_is_a_no_op_when_the_cache_is_not_cold(monkeypatch):
    """Warming makes real LLM calls; a populated cache must not pay for them."""
    import tools.cache_savings.savings as sv
    import tools.genesis.reflexes.cache_warm as cw

    monkeypatch.setattr(sv, "get_savings_stats",
                        lambda *a, **k: {"state": STATE_POPULATED, "summary": {"total_entries": 5},
                                         "stored_entries": 5})
    out = cw.run({}, None)
    assert out["success"] is True
    assert out["details"]["action"] == "skipped"
    assert out["metric_value"] == 0.0


def test_reflex_refuses_to_report_success_when_it_warmed_nothing(monkeypatch):
    """The defect this reflex would otherwise have shipped with.

    It decides the cache is cold, tries to fix it, warms zero — reporting
    success there is a reflex claiming work it did not do, green in
    genesis_audit with an empty cache behind it. Observed live: every seed
    refused with "Module 'generative_intelligence' budget exceeded".
    """
    import tools.cache_savings.savings as sv
    import tools.cache_savings.warmer as wm
    import tools.genesis.reflexes.cache_warm as cw

    monkeypatch.setattr(sv, "get_savings_stats",
                        lambda *a, **k: {"state": STATE_COLD, "summary": {"total_entries": 0},
                                         "stored_entries": 0})

    class _AllFail:
        def warm_on_deploy(self):
            return [{"status": "error", "error": "budget exceeded", "tokens_used": 0}] * 3

    monkeypatch.setattr(wm, "CacheWarmer", lambda *a, **k: _AllFail())
    out = cw.run({}, None)
    assert out["success"] is False
    assert out["details"]["action"] == "warm_failed"
    assert "budget exceeded" in out["error"]


def test_reflex_reports_success_when_it_actually_warms(monkeypatch):
    import tools.cache_savings.savings as sv
    import tools.cache_savings.warmer as wm
    import tools.genesis.reflexes.cache_warm as cw

    monkeypatch.setattr(sv, "get_savings_stats",
                        lambda *a, **k: {"state": STATE_COLD, "summary": {"total_entries": 0},
                                         "stored_entries": 0})

    class _Ok:
        def warm_on_deploy(self):
            return [{"status": "warmed", "tokens_used": 120},
                    {"status": "error", "error": "one bad seed", "tokens_used": 0}]

    monkeypatch.setattr(wm, "CacheWarmer", lambda *a, **k: _Ok())
    out = cw.run({}, None)
    assert out["success"] is True
    assert out["metric_value"] == 1.0
    assert out["details"]["errors"] == 1


def test_reflex_always_returns_the_success_key():
    """A dict without `success` is scored a FAILURE forever by the daemon."""
    import inspect

    import tools.genesis.reflexes.cache_warm as cw

    src = inspect.getsource(cw.run)
    assert src.count("return {") == src.count('"success"'), (
        "every return path must carry an explicit success key"
    )


def test_reflex_is_registered_in_both_places():
    """Dispatch needs REFLEX_NAMES *and* an enabled entry with a schedule."""
    import yaml

    from tools.genesis.daemon import REFLEX_NAMES

    assert "cache_warm" in REFLEX_NAMES
    cfg = yaml.safe_load((_ROOT / "args" / "genesis_config.yaml").read_text(encoding="utf-8"))
    entry = cfg["reflexes"]["cache_warm"]
    assert entry["enabled"] is True
    assert str(entry.get("schedule") or "").strip(), "no schedule = silently never dispatched"


# --------------------------------------------------------------------------- #
# The tile endpoint — the seam that actually broke
# --------------------------------------------------------------------------- #

def test_tile_endpoint_carries_the_state_through(monkeypatch):
    """The API knowing WHY is useless if the tile never receives it.

    savings.get_savings_stats() can report `cold` perfectly and the card still
    renders a bare red 0% unless api_cache_savings_tile forwards those fields.
    That gap is invisible in a unit test of either half alone.
    """
    from flask import Flask

    import tools.cache_savings.savings as sv
    from tools.cache_savings.blueprint import bp

    monkeypatch.setattr(sv, "get_savings_stats", lambda *a, **k: {
        "enabled": True, "backend": "postgresql",
        "state": STATE_COLD, "state_detail": "cache is cold — UNLOGGED reset",
        "stored_entries": 0,
        "summary": {"hit_rate_pct": 0.0, "total_entries": 0,
                    "cache_read_tokens": 0, "total_usd_saved": 0.0},
        "by_function": [],
    })

    app = Flask(__name__)
    app.register_blueprint(bp)
    with app.test_client() as c:
        payload = c.get("/api/cache-savings/tile").get_json()

    assert payload["state"] == STATE_COLD
    assert "UNLOGGED" in payload["state_detail"]
    assert payload["stored_entries"] == 0
    # And the legacy keys the tile already rendered are untouched.
    assert payload["hit_rate_pct"] == 0.0
    assert payload["backend"] == "postgresql"

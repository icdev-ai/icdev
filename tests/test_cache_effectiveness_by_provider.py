#!/usr/bin/env python3
# CUI // SP-CTI
"""cch-obs-01 — per-provider cache effectiveness, and the zeroes it refuses to merge.

The LLM Prompt Cache card reported ONE hit rate over a mix of providers that
cache by different mechanisms, report tokens under different accounting, and in
some cases are not billed at all. One average over that mix cannot answer the
only question anyone has — is prefix caching working, on WHICH provider — and
worse, it renders four different situations identically as ``0%`` / ``$0.00``:

* a provider nobody called,
* a provider whose transport never reports cache counters,
* a provider that was called and genuinely cached nothing,
* a provider that is not billed and therefore has no dollars to save.

Only the third is a defect. The other three are the platform's signature bug in
miniature: a number that reads like a measurement and is not one.

THE ACCEPTANCE CRITERIA ARE THE FIRST TWO TESTS. A provider with zero traffic
must be visibly distinct from one with zero cache hits, in the payload and not
merely in a tooltip: different ``status``, and ``cached_share_pct`` of ``None``
versus ``0.0``. Everything after them holds the rules that make those two
states survive contact with the real provider mix.
"""
from __future__ import annotations

import sqlite3
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.cache_savings import by_provider as bp  # noqa: E402
from tools.db.storage import translate_sql  # noqa: E402

#: A fixed "now" so the windows are deterministic. Never datetime.now(): a
#: window boundary that moves with the clock is how a green test starts failing
#: at midnight for reasons unrelated to the code.
NOW = datetime(2026, 8, 16, 12, 0, 0, tzinfo=timezone.utc)

_DDL = """
CREATE TABLE ai_telemetry (
    id TEXT PRIMARY KEY,
    model_id TEXT NOT NULL,
    provider TEXT NOT NULL,
    prompt_hash TEXT NOT NULL,
    input_tokens INTEGER DEFAULT 0,
    output_tokens INTEGER DEFAULT 0,
    cache_creation_input_tokens INTEGER NOT NULL DEFAULT 0,
    cache_read_input_tokens INTEGER NOT NULL DEFAULT 0,
    latency_ms INTEGER DEFAULT 0,
    created_at TEXT NOT NULL
)
"""


class _SqliteConn:
    """Minimal StorageConnection stand-in over sqlite3.

    Dialect translation is delegated to the REAL ``translate_sql`` rather than
    reimplemented here. A hand-rolled ``%s`` -> ``?`` substitution would let the
    test pass against a translation the product does not actually perform,
    which is a test that proves the test.
    """

    def __init__(self, raw: sqlite3.Connection) -> None:
        self._raw = raw

    def execute(self, sql: str, params=None):
        return self._raw.execute(translate_sql(sql, "sqlite"), params or ())


@pytest.fixture()
def conn():
    raw = sqlite3.connect(":memory:")
    raw.execute(_DDL)
    yield _SqliteConn(raw)
    raw.close()


def _insert(conn, provider, *, days_ago=1, calls=1, input_tokens=1000,
            cache_read=0, cache_write=0, latency_ms=100):
    """Write ``calls`` telemetry rows for one provider, ``days_ago`` back."""
    stamp = (NOW - timedelta(days=days_ago)).strftime("%Y-%m-%d %H:%M:%S")
    for _ in range(calls):
        conn.execute(
            "INSERT INTO ai_telemetry (id, model_id, provider, prompt_hash, "
            "input_tokens, output_tokens, cache_creation_input_tokens, "
            "cache_read_input_tokens, latency_ms, created_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (str(uuid.uuid4()), "test-model", provider, "hash",
             input_tokens, 100, cache_write, cache_read, latency_ms, stamp),
        )


def _by_name(result):
    return {p["provider"]: p for p in result["providers"]}


# ---------------------------------------------------------------------------
# THE ACCEPTANCE CRITERIA
# ---------------------------------------------------------------------------

def test_provider_with_no_traffic_reads_no_data_never_zero_percent(conn):
    """Zero calls is not a zero hit rate, and must not render as one."""
    # openai has traffic so the window is measurable; anthropic has none.
    _insert(conn, "openai", calls=3)

    result = bp.get_provider_effectiveness(conn=conn, window_days=7, now=NOW)
    anthropic = _by_name(result)["anthropic"]

    assert anthropic["calls"] == 0
    assert anthropic["status"] == bp.STATUS_NO_DATA
    # The load-bearing assertion: None, not 0.0. A caller that formats this
    # field cannot accidentally print "0%" for a provider nobody called.
    assert anthropic["cached_share_pct"] is None
    assert "not a zero hit rate" in anthropic["status_detail"]


def test_provider_with_zero_cache_hits_reads_a_measured_zero(conn):
    """Called, counters reported, all zero — that IS 0%, and says so."""
    _insert(conn, "openai", calls=4, input_tokens=2000, cache_read=0)

    openai = _by_name(bp.get_provider_effectiveness(conn=conn, window_days=7, now=NOW))["openai"]

    assert openai["calls"] == 4
    assert openai["status"] == bp.STATUS_NO_CACHE_HITS
    assert openai["cached_share_pct"] == 0.0
    # It is billed and it saved nothing, so $0.00 is the honest figure here —
    # unlike the local case below, where no dollar figure exists at all.
    assert openai["usd_saved"] == 0.0


def test_the_two_zeroes_are_visibly_distinct(conn):
    """The whole card in one assertion: these must never render identically."""
    _insert(conn, "openai", calls=4, cache_read=0)          # measured zero
    # anthropic: no rows at all                              # no data

    rows = _by_name(bp.get_provider_effectiveness(conn=conn, window_days=7, now=NOW))
    measured_zero, no_traffic = rows["openai"], rows["anthropic"]

    assert measured_zero["status"] != no_traffic["status"]
    assert measured_zero["cached_share_pct"] != no_traffic["cached_share_pct"]
    assert measured_zero["status_label"] != no_traffic["status_label"]


# ---------------------------------------------------------------------------
# The rules that keep those two states honest across the real provider mix
# ---------------------------------------------------------------------------

def test_provider_that_reports_no_counters_is_unreported_not_zero(conn):
    """The rule, now asserted against a provider that genuinely reports nothing.

    This used to use `claude-cli`, on the belief that "Claude Code caches aggressively on
    the far side of that transport" and reports nothing back. HALF of that was wrong, and
    cch-obs-04 measured which half: the CLI reports `cache_read_input_tokens` and
    `cache_creation_input_tokens` on every call, and ICDEV's own bridge was parsing `usage`
    and keeping only input/output. The claim `reports_cache_tokens: false` and the missing
    evidence agreed with each other because the same pipeline produced both.

    So claude-cli is no longer the example. `ollama` is: it is a local runtime with no
    prefix-cache concept at all, its zero really is the absence of a number, and calling it
    0% would fabricate a measurement.
    """
    _insert(conn, "ollama", calls=50, cache_read=0)

    row = _by_name(bp.get_provider_effectiveness(conn=conn, window_days=7, now=NOW))["ollama"]

    assert row["calls"] == 50
    assert row["status"] == bp.STATUS_UNREPORTED
    assert row["cached_share_pct"] is None


def test_claude_cli_zero_is_now_a_measurement_not_an_absence(conn):
    """The other side of cch-obs-04, and the reason the change is not free.

    Once the counters flow, a zero from claude-cli means "this call got no cache hit" — a
    real 0%. That is the point. The cost is that the 626 rows written BEFORE the fix also
    hold 0, and those zeros mean "the bridge dropped the number". They are indistinguishable
    at the row level, so a window wide enough to reach them reports a measured 0% for calls
    that were never measured.

    That is bounded rather than hidden: the default window is 7 days and the last claude-cli
    call was 2026-07-09, so no default view can reach them. The caveat is recorded beside the
    claim in args/cache_effectiveness.yaml for anyone widening the window.
    """
    _insert(conn, "claude-cli", calls=50, cache_read=0)

    row = _by_name(bp.get_provider_effectiveness(conn=conn, window_days=7, now=NOW))["claude-cli"]

    assert row["status"] == bp.STATUS_NO_CACHE_HITS
    assert row["cached_share_pct"] == 0.0


def test_claude_cli_reports_a_real_hit_rate_when_the_counters_are_present(conn):
    """What the fix actually buys: the subscription path becomes measurable."""
    _insert(conn, "claude-cli", calls=10, input_tokens=100, cache_read=900)

    row = _by_name(bp.get_provider_effectiveness(conn=conn, window_days=7, now=NOW))["claude-cli"]

    assert row["status"] == bp.STATUS_CACHING
    # DISJOINT accounting: total = input + read + write, so 900 of 1000 came from cache.
    assert row["cached_share_pct"] == pytest.approx(90.0, abs=0.1)


def test_local_provider_shows_latency_not_dollars(conn):
    """Ollama has no bill, so it has no dollars to save. $0.00 would be a lie."""
    _insert(conn, "ollama", calls=10, latency_ms=250)

    ollama = _by_name(bp.get_provider_effectiveness(conn=conn, window_days=7, now=NOW))["ollama"]

    assert ollama["usd_basis"] == bp.USD_LOCAL
    assert ollama["usd_saved"] is None          # NOT 0.0
    assert ollama["avg_latency_ms"] == 250.0    # the effect it does have
    assert "not billed per token" in ollama["usd_detail"]


def test_measurement_overrides_a_declaration_of_no_counters(conn):
    """If a provider declared silent starts reporting, believe the rows.

    The config is a claim; the ledger is evidence. A declaration may only
    decide how to read a ZERO, never how to read a number.
    """
    _insert(conn, "ollama", calls=5, input_tokens=1000, cache_read=400)

    ollama = _by_name(bp.get_provider_effectiveness(conn=conn, window_days=7, now=NOW))["ollama"]

    assert ollama["status"] == bp.STATUS_CACHING
    assert ollama["cached_share_pct"] is not None


def test_inclusive_and_disjoint_accounting_are_not_summed_together(conn):
    """The correctness rule the single aggregate got silently wrong.

    OpenAI reports cached_tokens as a SUBSET of prompt_tokens; Anthropic
    reports them separately. Same raw numbers, different true share — and
    adding the two shapes together double-counts every OpenAI cached token.
    """
    _insert(conn, "openai", calls=1, input_tokens=1000, cache_read=400)
    _insert(conn, "anthropic", calls=1, input_tokens=1000, cache_read=400)

    rows = _by_name(bp.get_provider_effectiveness(conn=conn, window_days=7, now=NOW))

    # Inclusive: 1000 IS the whole prompt; 400 of it came from cache.
    assert rows["openai"]["prompt_tokens"] == 1000
    assert rows["openai"]["uncached_input_tokens"] == 600
    assert rows["openai"]["cached_share_pct"] == 40.0

    # Disjoint: the prompt was 1400 tokens, 1000 of them uncached.
    assert rows["anthropic"]["prompt_tokens"] == 1400
    assert rows["anthropic"]["uncached_input_tokens"] == 1000
    assert rows["anthropic"]["cached_share_pct"] == pytest.approx(28.57, abs=0.01)


def test_savings_net_off_the_cache_write_premium(conn):
    """A cache write costs MORE than an uncached token; the saving is net."""
    _insert(conn, "anthropic", calls=1, input_tokens=0, cache_read=1_000_000, cache_write=0)

    row = _by_name(bp.get_provider_effectiveness(conn=conn, window_days=7, now=NOW))["anthropic"]
    # 1M read tokens at $3.00/MTok input and a 0.1x read multiplier => $2.70.
    assert row["usd_saved"] == pytest.approx(2.70, abs=0.01)

    # Same reads, but a million write tokens at a 1.25x premium claw back $0.75.
    _insert(conn, "anthropic", calls=1, input_tokens=0, cache_read=0, cache_write=1_000_000)
    row = _by_name(bp.get_provider_effectiveness(conn=conn, window_days=7, now=NOW))["anthropic"]
    assert row["usd_saved"] == pytest.approx(1.95, abs=0.01)


def test_trend_compares_against_the_previous_equal_window(conn):
    """'Better or worse than last week' is the question; answer it with a sign."""
    # Previous window (8-14 days ago): 20% cached.
    _insert(conn, "openai", days_ago=9, calls=1, input_tokens=1000, cache_read=200)
    # Current window: 60% cached.
    _insert(conn, "openai", days_ago=1, calls=1, input_tokens=1000, cache_read=600)

    row = _by_name(bp.get_provider_effectiveness(conn=conn, window_days=7, now=NOW))["openai"]

    assert row["cached_share_pct"] == 60.0
    assert row["trend"]["previous_cached_share_pct"] == 20.0
    assert row["trend"]["direction"] == bp.TREND_IMPROVED
    assert row["trend"]["delta_pct_points"] == pytest.approx(40.0)


def test_trend_reports_no_baseline_rather_than_inventing_one(conn):
    """No comparable previous measurement is not a 0% previous measurement."""
    _insert(conn, "openai", days_ago=1, calls=1, input_tokens=1000, cache_read=600)

    row = _by_name(bp.get_provider_effectiveness(conn=conn, window_days=7, now=NOW))["openai"]

    assert row["trend"]["direction"] == bp.TREND_NO_BASELINE
    assert row["trend"]["previous_cached_share_pct"] is None
    assert row["trend"]["delta_pct_points"] is None


def test_a_database_with_no_history_is_unmeasurable_not_all_zero(conn):
    """A fresh worktree or ephemeral CI database must not fabricate findings.

    Every declared provider would otherwise report `no_data` and the card would
    read as a wall of problems on a database that has simply never been used.
    """
    result = bp.get_provider_effectiveness(conn=conn, window_days=7, now=NOW)

    assert result["measurable"] is False
    assert result["providers"] == []
    assert "no operating history" in result["unmeasurable_reason"]
    # Not 0.0 — nothing was measured, so there is no total to report.
    assert result["totals"]["usd_saved_total"] is None


def test_totals_carry_no_blended_hit_rate(conn):
    """The aggregate this card exists to delete must not reappear in totals."""
    _insert(conn, "openai", calls=1, input_tokens=1000, cache_read=400)
    _insert(conn, "anthropic", calls=1, input_tokens=1000, cache_read=400)

    totals = bp.get_provider_effectiveness(conn=conn, window_days=7, now=NOW)["totals"]

    assert "hit_rate_pct" not in totals
    assert "cached_share_pct" not in totals
    # Counts per state instead, so the four zeroes stay separable at the top level.
    assert totals["providers_caching"] == 2
    assert totals["providers_no_data"] >= 1


def test_an_unreadable_config_does_not_fabricate_zero_percent(monkeypatch, conn):
    """Losing the claims file must degrade to 'unknown', never to a measured 0%."""
    monkeypatch.setattr(bp, "_load_config", lambda: {})
    _insert(conn, "openai", calls=3, cache_read=0)

    openai = _by_name(bp.get_provider_effectiveness(conn=conn, window_days=7, now=NOW))["openai"]

    # Without the config we no longer know openai reports counters, so its zero
    # is unreadable rather than a measurement.
    assert openai["status"] == bp.STATUS_UNREPORTED
    assert openai["cached_share_pct"] is None


def test_config_declares_the_accounting_shape_for_every_priced_provider():
    """A priced provider with the wrong accounting silently mis-states its share."""
    cfg = bp._load_config()
    for name, claim in (cfg.get("providers") or {}).items():
        if claim.get("usd_basis") != bp.USD_PRICED:
            continue
        assert claim.get("token_accounting") in (bp.ACCT_DISJOINT, bp.ACCT_INCLUSIVE), (
            f"{name} is priced but declares no token accounting shape"
        )
        assert claim.get("input_usd_per_mtok"), f"{name} is priced but declares no rate"


# ---------------------------------------------------------------------------
# The surfaces. The distinction above is worthless if the route or the tile
# flattens it on the way out.
# ---------------------------------------------------------------------------

@pytest.fixture()
def client(monkeypatch):
    """A bare Flask app carrying only the cache_savings blueprint.

    Deliberately NOT `tools.dashboard.app`: that module registers ~200
    blueprints at import time, so a test that reaches for it pays the whole
    dashboard's import cost to exercise two routes.
    """
    import importlib

    from flask import Flask

    # Resolved by dotted string on purpose. `tools.` is a shim that redirects
    # to `icdev.tools.`, so a plain `from tools.x import y` can hand a test a
    # different module object than the one the product mutates; going through
    # importlib pins the same entry in sys.modules that the route resolves.
    cs_bp = importlib.import_module("tools.cache_savings.blueprint")

    app = Flask(__name__)
    app.register_blueprint(cs_bp.bp)
    app.config["TESTING"] = True
    return app.test_client()


def _fake_effectiveness(**overrides):
    payload = {
        "window_days": 7, "window_start": "a", "window_end": "b",
        "measurable": True, "unmeasurable_reason": "",
        "providers": [
            {"provider": "openai", "status": bp.STATUS_NO_CACHE_HITS,
             "cached_share_pct": 0.0, "calls": 4},
            {"provider": "anthropic", "status": bp.STATUS_NO_DATA,
             "cached_share_pct": None, "calls": 0},
        ],
        "totals": {"providers_caching": 0, "providers_no_cache_hits": 1,
                   "providers_unreported": 0, "providers_no_data": 1,
                   "usd_saved_total": 0.0, "usd_saved_basis": "priced providers only"},
    }
    payload.update(overrides)
    return payload


def test_by_provider_route_preserves_the_null_share(client, monkeypatch):
    """The API must not coerce a missing rate into a zero on the way out."""
    monkeypatch.setattr(bp, "get_provider_effectiveness", lambda **kw: _fake_effectiveness())

    body = client.get("/api/cache-savings/by-provider").get_json()
    rows = {p["provider"]: p for p in body["providers"]}

    assert rows["anthropic"]["cached_share_pct"] is None
    assert rows["openai"]["cached_share_pct"] == 0.0


def test_by_provider_route_rejects_a_non_integer_window(client):
    """A bad window is a 400, not a silent fallback to the default window."""
    resp = client.get("/api/cache-savings/by-provider?window_days=lots")
    assert resp.status_code == 400


def test_tile_carries_the_four_counts_separately(client, monkeypatch):
    """The home tile keeps the states apart instead of blending them."""
    monkeypatch.setattr(bp, "get_provider_effectiveness", lambda **kw: _fake_effectiveness())

    block = client.get("/api/cache-savings/tile").get_json()["by_provider"]

    assert block["measurable"] is True
    assert (block["caching"], block["no_cache_hits"],
            block["unreported"], block["no_data"]) == (0, 1, 0, 1)
    # No blended rate may reappear on the tile — that is the number this card
    # exists to delete, and the tile is where it lived.
    assert "hit_rate_pct" not in block
    assert "cached_share_pct" not in block


def test_tile_degrades_to_unmeasurable_when_the_view_raises(client, monkeypatch):
    """A broken per-provider view must not render as 'nothing is caching'."""
    def _boom(**kw):
        raise RuntimeError("telemetry unreachable")

    monkeypatch.setattr(bp, "get_provider_effectiveness", _boom)

    block = client.get("/api/cache-savings/tile").get_json()["by_provider"]

    assert block["measurable"] is False
    assert block["usd_saved"] is None      # not 0.0
    # The response-cache half of the tile is unaffected — one view failing must
    # not take down the card that already worked.
    assert "hit_rate_pct" in client.get("/api/cache-savings/tile").get_json()


def _render_page(by_provider: dict) -> str:
    """Render cache_savings/page.html against stub parents.

    The real template extends base.html and includes the IQE widget, both of
    which need the whole dashboard app context. Stubbing just those two lets
    the per-provider block render on its own, so a Jinja error in a branch the
    LIVE data never reaches — `no_cache_hits` was absent from the 7-day window
    on the day this shipped — still fails a test instead of waiting for the
    first week somebody's cache actually misses.
    """
    from jinja2 import ChoiceLoader, DictLoader, Environment, FileSystemLoader

    templates = REPO_ROOT / "tools" / "dashboard" / "templates"
    env = Environment(  # noqa: S701 - test harness, not a response path
        loader=ChoiceLoader([
            DictLoader({
                "base.html": "{% block content %}{% endblock %}",
                "includes/iqe_query_widget.html": "",
            }),
            FileSystemLoader(str(templates)),
        ]),
    )
    stats = {
        "enabled": True, "backend": "postgresql", "by_function": [],
        "summary": {
            "hit_rate_pct": 0.0, "total_entries": 0, "total_hits": 0,
            "cache_read_tokens": 0, "cache_write_tokens": 0,
            "total_usd_saved": 0.0, "resp_cache_usd_saved": 0.0,
            "context_cache_usd_saved": 0.0,
        },
    }
    return env.get_template("cache_savings/page.html").render(
        stats=stats, by_provider=by_provider
    )


def test_page_renders_all_four_states_distinctly():
    """Every state must reach the page, and none may render as another's twin."""
    html = _render_page({
        "measurable": True, "window_days": 7,
        "window_start": "2026-08-09 00:00:00", "window_end": "2026-08-16 00:00:00",
        "providers": [
            {"provider": "anthropic", "capability": "explicit", "status": bp.STATUS_CACHING,
             "status_label": "caching", "status_detail": "", "calls": 10,
             "cached_input_tokens": 4000, "uncached_input_tokens": 6000,
             "cached_share_pct": 40.0, "usd_saved": 0.0108, "usd_basis": bp.USD_PRICED,
             "usd_detail": "", "avg_latency_ms": 900.0,
             "trend": {"direction": bp.TREND_IMPROVED, "delta_pct_points": 12.0,
                       "previous_cached_share_pct": 28.0}},
            {"provider": "openai", "capability": "automatic", "status": bp.STATUS_NO_CACHE_HITS,
             "status_label": "no cache hits", "status_detail": "a measured 0%", "calls": 6,
             "cached_input_tokens": 0, "uncached_input_tokens": 2000,
             "cached_share_pct": 0.0, "usd_saved": 0.0, "usd_basis": bp.USD_PRICED,
             "usd_detail": "", "avg_latency_ms": 700.0,
             "trend": {"direction": bp.TREND_WORSENED, "delta_pct_points": -5.0,
                       "previous_cached_share_pct": 5.0}},
            {"provider": "ollama", "capability": "server_kv", "status": bp.STATUS_UNREPORTED,
             "status_label": "not reported", "status_detail": "no counters", "calls": 10863,
             "cached_input_tokens": 0, "uncached_input_tokens": 900000,
             "cached_share_pct": None, "usd_saved": None, "usd_basis": bp.USD_LOCAL,
             "usd_detail": "not billed per token", "avg_latency_ms": 250.0,
             "trend": {"direction": bp.TREND_NO_BASELINE, "delta_pct_points": None,
                       "previous_cached_share_pct": None}},
            {"provider": "bedrock", "capability": "explicit", "status": bp.STATUS_NO_DATA,
             "status_label": "no data", "status_detail": "nobody called it", "calls": 0,
             "cached_input_tokens": 0, "uncached_input_tokens": 0,
             "cached_share_pct": None, "usd_saved": None, "usd_basis": bp.USD_PRICED,
             "usd_detail": "", "avg_latency_ms": 0.0,
             "trend": {"direction": bp.TREND_NO_BASELINE, "delta_pct_points": None,
                       "previous_cached_share_pct": None}},
        ],
        "totals": {"providers_caching": 1, "providers_no_cache_hits": 1,
                   "providers_unreported": 1, "providers_no_data": 1,
                   "usd_saved_total": 0.0108, "usd_saved_basis": "priced providers only"},
    })

    for label in ("caching", "no cache hits", "not reported", "no data"):
        assert label in html, f"state {label!r} never reached the page"
    # The measured zero prints a percentage; the two unmeasured ones print an
    # em dash. If a future edit made them share a renderer this fails.
    assert "0.0%" in html
    assert "&mdash;" in html or "—" in html
    # The local provider shows latency where a priced provider shows dollars.
    assert "250 ms avg" in html
    assert "$0.0108" in html


def test_a_local_provider_with_no_traffic_shows_no_latency_either():
    """'0 ms avg' would be the same fabricated zero, one column over."""
    html = _render_page({
        "measurable": True, "window_days": 7,
        "window_start": "", "window_end": "",
        "providers": [
            {"provider": "vllm", "capability": "server_kv", "status": bp.STATUS_NO_DATA,
             "status_label": "no data", "status_detail": "nobody called it", "calls": 0,
             "cached_input_tokens": 0, "uncached_input_tokens": 0,
             "cached_share_pct": None, "usd_saved": None, "usd_basis": bp.USD_LOCAL,
             "usd_detail": "not billed per token", "avg_latency_ms": 0.0,
             "trend": {"direction": bp.TREND_NO_BASELINE, "delta_pct_points": None,
                       "previous_cached_share_pct": None}},
        ],
        "totals": {"providers_caching": 0, "providers_no_cache_hits": 0,
                   "providers_unreported": 0, "providers_no_data": 1,
                   "usd_saved_total": 0.0, "usd_saved_basis": "priced providers only"},
    })

    assert "0 ms avg" not in html


def test_page_renders_the_unmeasurable_state_without_a_table():
    """No history must produce a stated reason, not an empty table of zeroes."""
    html = _render_page({
        "measurable": False,
        "unmeasurable_reason": "ai_telemetry holds no rows in this window",
        "providers": [], "totals": {},
        "window_days": 7, "window_start": "", "window_end": "",
    })

    assert "Unmeasurable" in html
    assert "ai_telemetry holds no rows" in html
    assert "Cached Share" not in html   # the table itself must not render


def test_config_declares_no_model_ids():
    """Provider-keyed, never model-keyed — the platform stays LLM-agnostic.

    A model id here would pin one vendor's naming into an observability config
    the same way a hardcoded `model=` pins it into code, and this file is not
    covered by the AST gate that catches the code half.
    """
    text = bp.CONFIG_PATH.read_text(encoding="utf-8")
    for line in text.splitlines():
        # Comments may name a model in prose; a VALUE may not.
        code = line.split("#", 1)[0]
        assert "claude-sonnet" not in code and "gpt-4" not in code and "gemini-1" not in code, (
            f"model id in a config value: {line.strip()}"
        )

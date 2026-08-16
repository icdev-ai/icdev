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
    """A 0% hit rate explains nothing on its own; the tile must say why.

    This asserted `"unlogged" in detail` until 2026-08-16, and that was correct
    while UNLOGGED was the cause: PostgreSQL truncates an unlogged table on crash
    recovery, so a restart emptied the cache AND — because every figure on the
    card is derived `FROM llm_response_cache`, with no separate savings table —
    reset the cumulative dollars-saved to $0.0000.

    Migration 20260816123233 made the table LOGGED, so naming UNLOGGED would now
    send the reader after a cause that no longer exists. What the explanation
    must still do is state a real cause and a remedy.
    """
    from tools.cache_savings.savings import _STATE_DETAIL

    detail = _STATE_DETAIL[STATE_COLD].lower()
    assert "warmer" in detail, "tell the reader how to fix it"
    assert "logged" in detail, (
        "say that entries now survive a restart — otherwise a cold reading after "
        "a restart still looks like the old expected reset"
    )
    assert "unlogged is" not in detail and "is unlogged" not in detail, (
        "the table is LOGGED as of 20260816123233; naming UNLOGGED as the cause "
        "sends the reader after something that is no longer true"
    )


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


# ---------------------------------------------------------------------------
# The savings ledger must not be volatile (2026-08-16)
# ---------------------------------------------------------------------------
# tools/cache_savings/savings.py derives EVERY number on the LLM Prompt Cache
# card with `FROM llm_response_cache` and nothing else. The table was created
# UNLOGGED, and PostgreSQL truncates unlogged tables on crash recovery — so an
# unclean shutdown did not merely drop cached responses (fine, they regenerate),
# it reset a cumulative business metric to $0.0000 with no record it had ever
# been anything else.


def test_the_pg_ddl_does_not_create_an_unlogged_table():
    """The table is the ledger, so it has to survive a restart."""
    from tools.llm import response_cache

    ddl = response_cache._PG_DDL.upper()
    assert "CREATE TABLE" in ddl
    assert "UNLOGGED" not in ddl, (
        "llm_response_cache is the ONLY source of the dashboard's cumulative "
        "dollars-saved; UNLOGGED means PostgreSQL truncates it on crash "
        "recovery and the metric silently resets to $0.0000"
    )


def test_savings_still_read_from_the_cache_table():
    """Guards the premise of the test above rather than assuming it holds.

    If the savings ever move to their own durable table, UNLOGGED becomes a
    defensible choice again for the cache — and this test failing is the signal
    to revisit that, instead of the ledger assertion quietly protecting nothing.
    """
    import inspect

    from tools.cache_savings import savings

    src = inspect.getsource(savings)
    assert "FROM llm_response_cache" in src, (
        "savings no longer derive from llm_response_cache — re-examine whether "
        "the LOGGED requirement above still applies"
    )


def test_the_warmer_runs_by_path_without_PYTHONPATH():
    """The cold tile tells operators to run this exact command.

    warmer.py had no sys.path bootstrap, so `python tools/cache_savings/warmer.py
    --warm` — the remedy printed on the card — died with ModuleNotFoundError:
    No module named 'tools'. The one moment that command is reached is the one
    moment it has to work.
    """
    import os
    import subprocess
    import sys

    root = pathlib.Path(__file__).resolve().parents[1]
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}

    proc = subprocess.run(
        [sys.executable, str(root / "tools" / "cache_savings" / "warmer.py"),
         "--warm", "--dry-run"],
        capture_output=True, text=True, cwd=str(root), env=env, timeout=300,
    )
    combined = (proc.stdout or "") + (proc.stderr or "")
    assert "ModuleNotFoundError" not in combined, (
        f"the documented command fails without PYTHONPATH:\n{combined[-500:]}"
    )
    assert proc.returncode == 0, f"exit {proc.returncode}:\n{combined[-500:]}"


# ---------------------------------------------------------------------------
# Cache-token parity across providers — ICDEV is LLM-agnostic
# ---------------------------------------------------------------------------
# Caching is a provider CAPABILITY expressed four different ways, not an
# Anthropic feature other vendors lack. OpenAI and Azure cache prefixes
# AUTOMATICALLY (>=1024 tokens) with nothing to request — the only job is
# reading the count back. Azure did not, so every cached token it served was
# invisible, and prefix caching that works but is unrecorded is
# indistinguishable from prefix caching that never fired.


@pytest.mark.parametrize("module_path", [
    "tools.llm.openai_provider",
    "tools.llm.azure_openai_provider",
])
def test_openai_family_providers_read_cached_tokens(module_path):
    """Both use the same SDK object and report caching in the same place."""
    import importlib
    import inspect

    src = inspect.getsource(importlib.import_module(module_path))
    assert "prompt_tokens_details" in src, (
        f"{module_path} never reads prompt_tokens_details — automatic prefix "
        "caching is invisible, so the platform cannot tell it from no caching"
    )
    assert "cache_read_input_tokens" in src, (
        f"{module_path} must normalise into the shared LLMResponse field so "
        "savings are computed the same way for every provider"
    )


def test_the_response_carries_provider_neutral_cache_fields():
    """The normalisation point: every provider reports into the SAME fields."""
    import inspect

    from tools.llm.provider import LLMResponse

    params = inspect.signature(LLMResponse.__init__).parameters
    for field in ("cache_read_input_tokens", "cache_creation_input_tokens"):
        assert field in params, (
            f"LLMResponse.{field} is where every provider's caching — explicit "
            "(Anthropic/Bedrock), automatic (OpenAI/Azure) or managed (Gemini) — "
            "has to converge for the savings card to be provider-agnostic"
        )


# --------------------------------------------------------------------------- #
# Gemini (cch-prov-01) — the reporting half only
# --------------------------------------------------------------------------- #
# Gemini returns cachedContentTokenCount in usageMetadata for BOTH its implicit
# caching and its explicit cachedContents API. The provider read
# prompt_token_count and candidates_token_count and dropped that field, so any
# caching Gemini did on this platform's behalf was indistinguishable from none.
# Nothing is requested here — cachedContents is cch-prov-02.
#
# Driven through the real invoke() against a fake SDK object, so it is the
# PARSE that is under test, not a source grep — and through BOTH module copies,
# because `tools.llm.gemini_provider` and `icdev.tools.llm.gemini_provider` are
# two distinct module objects and a fix landed in only one of them is a fix the
# other half of the platform does not have.

_GEMINI_MODULES = ["tools.llm.gemini_provider", "icdev.tools.llm.gemini_provider"]


def _invoke_gemini(monkeypatch, usage_fields, module_path="tools.llm.gemini_provider"):
    """Run GeminiProvider.invoke() against a stand-in for the SDK response."""
    import importlib
    from types import SimpleNamespace

    from tools.llm.provider import LLMRequest

    gp = importlib.import_module(module_path)

    usage = SimpleNamespace(**usage_fields) if usage_fields is not None else None
    fake_response = SimpleNamespace(candidates=[], usage_metadata=usage)

    class _FakeModel:
        def __init__(self, **kwargs):
            pass

        def generate_content(self, messages):
            return fake_response

    monkeypatch.setattr(gp, "genai", SimpleNamespace(GenerativeModel=_FakeModel))

    provider = gp.GeminiProvider(api_key="test-key")
    provider._configured = True  # the fake stands in for genai.configure()
    request = LLMRequest(messages=[{"role": "user", "content": "hello"}])
    return provider.invoke(request, "gemini-2.0-flash", {})


@pytest.mark.parametrize("module_path", _GEMINI_MODULES)
@pytest.mark.parametrize("cached_field", [
    "cached_content_token_count",   # google-generativeai / google-genai SDK
    "cachedContentTokenCount",      # the raw REST spelling
])
def test_gemini_reports_the_cached_tokens_it_already_receives(
    monkeypatch, cached_field, module_path
):
    resp = _invoke_gemini(monkeypatch, {
        "prompt_token_count": 1200,
        "candidates_token_count": 40,
        cached_field: 1024,
    }, module_path)

    assert resp.cache_read_input_tokens == 1024, (
        "Gemini sent the count and the provider dropped it — caching that "
        "fires and is not recorded reads exactly like caching that never fired"
    )
    assert resp.input_tokens == 1200
    assert resp.output_tokens == 40


@pytest.mark.parametrize("module_path", _GEMINI_MODULES)
def test_gemini_reports_zero_when_nothing_was_cached(monkeypatch, module_path):
    """A response with no cache field is 0, not a raise and not a None."""
    resp = _invoke_gemini(monkeypatch, {
        "prompt_token_count": 300,
        "candidates_token_count": 10,
    }, module_path)

    assert resp.cache_read_input_tokens == 0
    assert resp.input_tokens == 300


@pytest.mark.parametrize("module_path", _GEMINI_MODULES)
def test_gemini_survives_a_response_with_no_usage_metadata_at_all(monkeypatch, module_path):
    """Every token field stays 0; the parse must not depend on usage existing."""
    resp = _invoke_gemini(monkeypatch, None, module_path)

    assert resp.cache_read_input_tokens == 0
    assert resp.input_tokens == 0
    assert resp.output_tokens == 0


def test_gemini_does_not_claim_a_cache_WRITE_it_cannot_observe(monkeypatch):
    """cachedContentTokenCount is a READ count.

    Gemini bills cache storage by time, not by a creation-token count, and
    reports no equivalent of Anthropic's cache_creation_input_tokens. Filling
    that field from the read count would invent a write cost the vendor never
    charged and double the apparent cache volume on the savings card.
    """
    resp = _invoke_gemini(monkeypatch, {
        "prompt_token_count": 1200,
        "cached_content_token_count": 1024,
    })

    assert resp.cache_read_input_tokens == 1024
    assert resp.cache_creation_input_tokens == 0

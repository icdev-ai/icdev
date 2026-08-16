#!/usr/bin/env python3
"""Ollama's prefix cache pays in LATENCY; the card must not price it. CUI // SP-CTI

cch-prov-03. Three claims are under test, and they are separable:

1. Ollama DECLARES ``local`` with a written reason (inherited from cch-cap-01 —
   pinned here so the reason cannot quietly be deleted).
2. The savings card reports **not applicable** for a local provider rather than a
   dollar figure. It was reporting a *non-zero* one: measured on this deployment
   2026-08-16, a single `ollama` entry served twice was credited $0.0040 of
   "savings" against Anthropic's rate card, for inference that was never billed.
   ``None`` and ``0.0`` are different claims and the tests keep them apart.
3. Prompt-eval duration is read off the wire, in the right unit, with ``None``
   meaning "not reported" rather than a measured zero.

Deterministic: no Ollama process, no database, no network. The one test that
would need a live server is skipped explicitly rather than silently passing.
"""
from __future__ import annotations

import pathlib
import sys

import pytest

_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from tools.cache_savings import savings as savings_mod  # noqa: E402
from tools.llm.ollama_provider import OllamaProvider, _prompt_eval_ms  # noqa: E402
from tools.llm.provider import (  # noqa: E402
    PREFIX_CACHE_AUTOMATIC,
    PREFIX_CACHE_EXPLICIT,
    PREFIX_CACHE_LOCAL,
    PREFIX_CACHE_NONE,
    LLMResponse,
    PrefixCacheCapability,
    prefix_cache_savings_are_monetary,
)


# --------------------------------------------------------------------------- #
# 1. The declaration
# --------------------------------------------------------------------------- #
def test_local_ollama_declares_local_with_a_written_reason():
    cap = OllamaProvider(base_url="http://localhost:11434").prefix_cache_capability
    assert cap.support == PREFIX_CACHE_LOCAL
    assert cap.verified is True
    assert len(cap.reason.strip()) > 40, "an undocumented 'local' is an unasked question"


def test_the_reason_says_latency_and_denies_a_dollar_saving():
    """The reason is what the card SHOWS a reader. It has to carry the point."""
    reason = OllamaProvider(base_url="http://localhost:11434").prefix_cache_capability.reason.lower()
    assert "latency" in reason or "prompt-eval" in reason
    assert "no per-token price" in reason or "nothing to bill" in reason


def test_a_hosted_ollama_is_not_declared_local():
    """ollama.com IS billed, so 'nothing to bill' would be a false claim there."""
    cap = OllamaProvider(base_url="https://ollama.com").prefix_cache_capability
    assert cap.support != PREFIX_CACHE_LOCAL
    assert cap.verified is False, "unverified must not read as an assessed 'none'"


# --------------------------------------------------------------------------- #
# 2. Dollars vs latency as UNITS
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "support,monetary",
    [
        (PREFIX_CACHE_LOCAL, False),
        (PREFIX_CACHE_NONE, True),
        (PREFIX_CACHE_AUTOMATIC, True),
        (PREFIX_CACHE_EXPLICIT, True),
    ],
)
def test_only_local_is_non_monetary(support, monetary):
    """`none` is a real $0.00 — a billed provider that cached nothing saved nothing.

    Only `local` is the wrong UNIT. Collapsing the two would relabel every
    unverified provider 'not applicable' and hide genuine misses.
    """
    cap = PrefixCacheCapability(support=support, reason="x" * 50, verified=False)
    assert prefix_cache_savings_are_monetary(cap) is monetary


# --------------------------------------------------------------------------- #
# 3. The card
# --------------------------------------------------------------------------- #
class _Conn:
    """Serves the per-function query, the stored COUNT, and the per-provider query."""

    def __init__(self, by_function=(), by_provider=(), stored=1):
        self.by_function, self.by_provider, self.stored = list(by_function), list(by_provider), stored

    def execute(self, sql, params=()):
        flat = " ".join(sql.split())
        if "COUNT(*) FROM llm_response_cache" in flat:
            rows = [(self.stored,)]
        elif "GROUP BY provider" in flat:
            rows = self.by_provider
        else:
            rows = self.by_function

        class _Cur:
            def fetchall(self_inner):
                return rows

            def fetchone(self_inner):
                return rows[0] if rows else None

        return _Cur()

    def close(self):
        pass


def _fake_caps(monkeypatch, mapping):
    monkeypatch.setattr(savings_mod, "_capability", lambda name: mapping.get(name))


# provider, entries, hits, in_tok, out_tok, cache_write, cache_read
_OLLAMA_ROW = ("ollama", 1, 2, 193, 226, 0, 0)
_ANTHROPIC_ROW = ("anthropic", 2, 6, 1000, 500, 200, 4000)

_LOCAL_CAP = PrefixCacheCapability(support=PREFIX_CACHE_LOCAL, reason="l" * 50)
_EXPLICIT_CAP = PrefixCacheCapability(support=PREFIX_CACHE_EXPLICIT, reason="e" * 50)


def test_a_local_provider_reports_not_applicable_not_zero(monkeypatch):
    """The acceptance criterion. `None` is the only value that means 'wrong unit'."""
    _fake_caps(monkeypatch, {"ollama": _LOCAL_CAP})
    stats = savings_mod.get_savings_stats(conn=_Conn(by_provider=[_OLLAMA_ROW]))

    row = next(p for p in stats["by_provider"] if p["provider"] == "ollama")
    assert row["usd_saved"] is None, "a local provider must not carry a dollar figure"
    assert row["usd_applicable"] is False
    assert row["usd_saved"] != 0.0 or row["usd_saved"] is None
    assert row["prefix_cache_support"] == PREFIX_CACHE_LOCAL
    assert row["prefix_cache_reason"].strip(), "'not applicable' needs its reason shown"


def test_the_fabricated_local_dollars_are_withheld_and_declared(monkeypatch):
    """The regression this fixes: $0.0040 credited to a call nobody was billed for.

    Withholding it silently would be its own defect — a headline that shrinks with
    no explanation. The amount and the providers are both reported.
    """
    _fake_caps(monkeypatch, {"ollama": _LOCAL_CAP})
    stats = savings_mod.get_savings_stats(conn=_Conn(by_provider=[_OLLAMA_ROW]))
    summary = stats["summary"]

    # 1 avoided call x (193 in x $3/MTok + 226 out x $15/MTok) == $0.0040
    assert summary["usd_withheld_local"] == pytest.approx(0.0040, abs=5e-5)
    assert summary["local_providers"] == ["ollama"]
    assert summary["total_usd_saved"] == 0.0, "local dollars must not reach the headline"


def test_a_billed_provider_still_gets_a_dollar_figure(monkeypatch):
    """The fix must not blank out the providers where dollars ARE the right unit."""
    _fake_caps(monkeypatch, {"anthropic": _EXPLICIT_CAP})
    stats = savings_mod.get_savings_stats(conn=_Conn(by_provider=[_ANTHROPIC_ROW]))

    row = next(p for p in stats["by_provider"] if p["provider"] == "anthropic")
    assert row["usd_saved"] is not None
    assert row["usd_saved"] > 0
    assert row["usd_applicable"] is True


def test_headline_keeps_billed_dollars_while_dropping_local_ones(monkeypatch):
    """Mixed board: exactly the local contribution is removed, nothing else."""
    _fake_caps(monkeypatch, {"ollama": _LOCAL_CAP, "anthropic": _EXPLICIT_CAP})
    conn = _Conn(
        by_function=[("code_generation", 3, 8, 1193, 726, 200, 4000)],
        by_provider=[_ANTHROPIC_ROW, _OLLAMA_ROW],
    )
    stats = savings_mod.get_savings_stats(conn=conn)
    summary = stats["summary"]

    assert summary["local_providers"] == ["ollama"]
    assert summary["usd_withheld_local"] > 0
    assert summary["gross_usd_saved"] > summary["total_usd_saved"]
    assert summary["total_usd_saved"] == pytest.approx(
        summary["gross_usd_saved"] - summary["usd_withheld_local"], abs=1e-4
    )


def test_an_unconfigured_provider_is_not_silently_called_local(monkeypatch):
    """Fail safe: 'nobody declared this' must not become a free 'not applicable'."""
    _fake_caps(monkeypatch, {})  # capability lookup returns None
    stats = savings_mod.get_savings_stats(conn=_Conn(by_provider=[_OLLAMA_ROW]))
    row = stats["by_provider"][0]
    assert row["usd_applicable"] is True, "unknown must stay monetary, not be excused"
    assert row["usd_saved"] is not None


def test_by_provider_survives_a_table_without_the_column(monkeypatch):
    """An older schema must degrade to an empty section, never a 500."""
    class _Broken(_Conn):
        def execute(self, sql, params=()):
            if "GROUP BY provider" in " ".join(sql.split()):
                raise RuntimeError("no such column: provider")
            return super().execute(sql, params)

    _fake_caps(monkeypatch, {})
    stats = savings_mod.get_savings_stats(conn=_Broken(by_function=[("f", 1, 2, 10, 10, 0, 0)]))
    assert stats["by_provider"] == []
    assert stats["summary"]["usd_withheld_local"] == 0.0


# --------------------------------------------------------------------------- #
# 4. Reading prompt-eval time off the wire
# --------------------------------------------------------------------------- #
def test_prompt_eval_duration_is_converted_from_nanoseconds():
    assert _prompt_eval_ms({"prompt_eval_duration": 5_147_900_000}) == pytest.approx(5147.9)
    assert _prompt_eval_ms({"prompt_eval_duration": 8_300_000}) == pytest.approx(8.3)


def test_a_missing_duration_is_none_not_zero():
    """Structural: 'the provider did not report it' != 'the prefill took no time'."""
    assert _prompt_eval_ms({}) is None
    assert _prompt_eval_ms({"prompt_eval_duration": None}) is None
    assert _prompt_eval_ms({"prompt_eval_duration": "not-a-number"}) is None


def test_llm_response_defaults_prompt_eval_to_none():
    assert LLMResponse().prompt_eval_ms is None, (
        "a default of 0.0 would claim every non-Ollama provider measured a zero prefill"
    )


def test_invoke_populates_prompt_eval_ms(monkeypatch):
    """The field has to arrive on the response, not just exist on the dataclass."""
    # importlib, not `import tools.llm.ollama_provider`: the root `tools` package
    # is a shim onto `icdev.tools`, and the attribute form resolves against
    # `icdev.tools.llm.__init__` where the submodule name is not bound.
    import importlib

    mod = importlib.import_module("tools.llm.ollama_provider")

    class _Resp:
        status_code = 200
        text = ""

        def raise_for_status(self):
            pass

        def json(self):
            return {
                "message": {"content": "ok"},
                "done": True,
                "done_reason": "stop",
                "prompt_eval_count": 1914,
                "prompt_eval_duration": 9_200_000,
                "eval_count": 3,
            }

    monkeypatch.setattr(mod, "_http_request", lambda *a, **k: _Resp())
    monkeypatch.setattr(mod, "HAS_REQUESTS", True)

    resp = OllamaProvider(base_url="http://localhost:11434").invoke(
        mod.LLMRequest(messages=[{"role": "user", "content": "hi"}]),
        "qwen3:0.6b",
        {"max_output_tokens": 256},
    )
    assert resp.prompt_eval_ms == pytest.approx(9.2)
    assert resp.input_tokens == 1914


def test_prompt_eval_count_is_not_used_as_a_cache_hit_signal():
    """Measured 2026-08-16: the count is CONSTANT across cold and warm calls.

    Ollama reports the full prompt length whether or not it re-evaluated it, so a
    'cached tokens' figure derived from it would be fabricated. Only the duration
    moves. This pins the two responses apart: identical counts, different prefill.
    """
    cold = {"prompt_eval_count": 1914, "prompt_eval_duration": 77_600_000}
    warm = {"prompt_eval_count": 1914, "prompt_eval_duration": 8_300_000}
    assert cold["prompt_eval_count"] == warm["prompt_eval_count"]
    assert _prompt_eval_ms(cold) > _prompt_eval_ms(warm) * 5


# --------------------------------------------------------------------------- #
# 5. The measurement tool
# --------------------------------------------------------------------------- #
def test_tool_reports_unmeasurable_rather_than_inventing_a_number(monkeypatch):
    """An unreachable Ollama must not read as 'caching does not help'."""
    from tools.llm import ollama_prefix_latency as tool

    def _boom(*a, **k):
        raise ConnectionError("Cannot connect to Ollama at http://localhost:11434")

    monkeypatch.setattr(tool, "_one_call", _boom)
    r = tool.measure(base_url="http://localhost:11434", repeats=2)

    assert r["status"] == tool.STATUS_UNMEASURABLE
    assert r["reason"].strip(), "UNMEASURABLE must say why"
    assert "cold_median_ms" not in r, "no median may be reported from zero samples"


def test_tool_refuses_a_non_local_endpoint(monkeypatch):
    """Pointed at ollama.com it must decline, not report a bogus 'local' win."""
    from tools.llm import ollama_prefix_latency as tool

    r = tool.measure(base_url="https://ollama.com", repeats=1)
    assert r["status"] == tool.STATUS_UNMEASURABLE
    assert "local" in r["reason"].lower()


def test_tool_never_reports_a_dollar_figure(monkeypatch):
    """The whole point: no fabricated saving for local inference."""
    from tools.llm import ollama_prefix_latency as tool

    # Keyed on the prefix, not a call sequence: measure() spends three calls
    # warming the model up before the first sample, and a positional fake would
    # silently shift the legs past each other (it did — cold and warm swapped).
    def _timed(_provider, _model, prefix, _question):
        if "COLD" in prefix:
            return 80.0
        if "SHARED" in prefix:
            return 8.0
        return 1.0  # the discarded warm-up calls

    monkeypatch.setattr(tool, "_one_call", _timed)
    r = tool.measure(base_url="http://localhost:11434", repeats=3)

    assert r["status"] == tool.STATUS_MEASURED
    assert r["usd_saved"] is None
    assert r["unit"].startswith("milliseconds")
    assert r["speedup_x"] == pytest.approx(10.0)
    assert r["saved_ms_per_call"] == pytest.approx(72.0)


def test_cold_prefixes_are_actually_distinct():
    """A 'cold' leg that re-sent a seen prefix would measure a warm call."""
    from tools.llm.ollama_prefix_latency import build_prefix

    a, b = build_prefix("COLD0"), build_prefix("COLD1")
    assert a != b
    # The seed must differ in the FIRST characters: KV reuse matches a leading
    # prefix, so a seed only at the end would leave the body cached.
    assert a[:40] != b[:40]

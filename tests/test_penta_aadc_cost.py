# CUI // SP-CTI
"""Cost-estimator schema + pricing-source tests for AADC (penta-aadc-05).

Regressions fixed:
  * The AIMC-catalog branch of ``_cost_for_model`` returned
    ``{input_per_1k, output_per_1k}`` while every caller reads
    ``avg_in/avg_out/input/output`` → ``KeyError`` for any model resolved via
    the AIMC catalog. Now every branch returns one canonical schema.
  * ``aimc_model.get("cost_per_1k_tokens", 0) >= 0`` was always true (default 0,
    0 >= 0). Now a real presence + non-negative-number check.
  * ``expensive_swaps`` hard-coded alternative prices that drifted from
    ``AADC_MODEL_COSTS``. Now the swap mapping lives in constants
    (``AADC_MODEL_SWAPS``) and prices are derived from ``AADC_MODEL_COSTS``.
  * Unspecified LLM nodes defaulted to a hard-coded ``'gpt-4o'``. Now the
    default is configurable via the ``AADC_DEFAULT_LLM_MODEL`` env var.
"""
from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.agentic_ai_canvas import cost_estimator as ce  # noqa: E402
from tools.agentic_ai_canvas.constants import (  # noqa: E402
    AADC_DEFAULT_LLM_MODEL,
    AADC_MODEL_COSTS,
    AADC_MODEL_SWAPS,
)

CANONICAL_KEYS = {"input", "output", "avg_in", "avg_out"}


def _find_aimc_only_model(min_cost: float = 0.0, positive: bool = True):
    """Return an (id, cost) from FOUNDATION_MODELS that AADC cannot resolve.

    'AADC cannot resolve' means the id is neither a key nor a fuzzy prefix of
    a key in AADC_MODEL_COSTS, so _cost_for_model must go through the AIMC
    branch for it.
    """
    from tools.aiml_canvas.constants import FOUNDATION_MODELS
    aadc = set(AADC_MODEL_COSTS)
    for m in FOUNDATION_MODELS:
        mid = m.get("id", "")
        cost = m.get("cost_per_1k_tokens")
        if not isinstance(cost, (int, float)):
            continue
        if positive and cost <= min_cost:
            continue
        if mid in aadc:
            continue
        if any(mid.startswith(k) or k.startswith(mid) for k in aadc):
            continue
        return mid, cost
    return None, None


# ---------------------------------------------------------------------------
# Canonical schema — every branch returns the same keys
# ---------------------------------------------------------------------------

def test_aadc_table_branch_canonical():
    entry = ce._cost_for_model("gpt-4o")
    assert CANONICAL_KEYS <= set(entry)


def test_aimc_branch_returns_canonical_schema_no_keyerror():
    mid, cost = _find_aimc_only_model()
    assert mid is not None, "expected a positive-cost AIMC-only model in the catalog"
    entry = ce._cost_for_model(mid)
    # The exact bug: callers read these keys; missing them raised KeyError.
    assert CANONICAL_KEYS <= set(entry), f"AIMC entry missing keys: {entry}"
    assert "input_per_1k" not in entry and "output_per_1k" not in entry
    # Derived from the blended AIMC rate: input == cost, output == 2 * cost.
    assert entry["input"] == pytest.approx(float(cost))
    assert entry["output"] == pytest.approx(float(cost) * 2)
    assert entry["avg_in"] == ce._DEFAULT_AVG_IN
    assert entry["avg_out"] == ce._DEFAULT_AVG_OUT


def test_fuzzy_prefix_branch_canonical():
    # 'gpt-4o-2024-08-06' is not a table key but fuzzy-prefix-matches 'gpt-4o'.
    entry = ce._cost_for_model("gpt-4o-2024-08-06")
    assert CANONICAL_KEYS <= set(entry)


def test_unknown_model_defaults_to_gpt_4o_mini():
    entry = ce._cost_for_model("totally-unknown-model-xyz")
    assert entry == AADC_MODEL_COSTS["gpt-4o-mini"]


def test_estimate_design_cost_via_aimc_model_no_keyerror():
    mid, _ = _find_aimc_only_model()
    graph = {"nodes": [{"id": "n1", "type": "llm", "properties": {"model": mid}}],
             "edges": []}
    result = ce.estimate_design_cost(graph, runs_per_month=1000)
    assert mid in result["model_breakdown"]
    assert result["total_per_run"] > 0
    assert result["total_monthly"] == pytest.approx(result["total_per_run"] * 1000, rel=1e-3)


# ---------------------------------------------------------------------------
# Real presence check — the always-true condition is fixed
# ---------------------------------------------------------------------------

def test_aimc_presence_check_ignores_model_without_cost_key(monkeypatch):
    """A model lacking cost_per_1k_tokens must NOT resolve via the AIMC branch.

    Under the old `.get(k, 0) >= 0` it would have (default 0 >= 0). It must now
    fall through to the fuzzy/default branch instead.
    """
    aimc_const = importlib.import_module("tools.aiml_canvas.constants")
    fake = [{"id": "nocost-model-xyz", "name": "No Cost"}]  # no cost_per_1k_tokens
    monkeypatch.setattr(aimc_const, "FOUNDATION_MODELS", fake, raising=True)

    entry = ce._cost_for_model("nocost-model-xyz")
    # Falls through to the gpt-4o-mini default (no fuzzy match for this id).
    assert entry == AADC_MODEL_COSTS["gpt-4o-mini"]


def test_aimc_zero_cost_model_still_resolves(monkeypatch):
    """A present, zero-cost AIMC model (local) is valid and resolves to $0."""
    aimc_const = importlib.import_module("tools.aiml_canvas.constants")
    fake = [{"id": "free-local-xyz", "cost_per_1k_tokens": 0.0}]
    monkeypatch.setattr(aimc_const, "FOUNDATION_MODELS", fake, raising=True)

    entry = ce._cost_for_model("free-local-xyz")
    assert entry["input"] == 0.0
    assert entry["output"] == 0.0
    assert CANONICAL_KEYS <= set(entry)


# ---------------------------------------------------------------------------
# Swap suggestions derived from the constants pricing table
# ---------------------------------------------------------------------------

def test_swap_map_alternatives_all_priced_in_table():
    """Both sides of every swap must be keys in the single pricing source."""
    for expensive, alt in AADC_MODEL_SWAPS.items():
        assert expensive in AADC_MODEL_COSTS, f"{expensive} missing from AADC_MODEL_COSTS"
        assert alt in AADC_MODEL_COSTS, f"{alt} missing from AADC_MODEL_COSTS"


def test_swap_hint_uses_constants_pricing():
    """The gpt-4o → gpt-4o-mini hint's savings derive from AADC_MODEL_COSTS."""
    graph = {"nodes": [{"id": "n1", "type": "llm", "properties": {"model": "gpt-4o"}}],
             "edges": []}
    runs = 1000
    result = ce.estimate_design_cost(graph, runs_per_month=runs)
    hints = result["optimization_hints"]
    swap_hints = [h for h in hints if h.startswith("Swap gpt-4o → gpt-4o-mini")]
    assert swap_hints, f"expected a gpt-4o swap hint, got: {hints}"

    # Recompute expected savings straight from the constants table.
    cur = AADC_MODEL_COSTS["gpt-4o"]
    alt = AADC_MODEL_COSTS["gpt-4o-mini"]
    cur_cost = cur["avg_in"] / 1000.0 * cur["input"] + cur["avg_out"] / 1000.0 * cur["output"]
    alt_cost = cur["avg_in"] / 1000.0 * alt["input"] + cur["avg_out"] / 1000.0 * alt["output"]
    expected_pct = round((1 - alt_cost / cur_cost) * 100)
    assert f"~{expected_pct}%" in swap_hints[0]


def test_no_swap_hint_when_only_cheap_model_present():
    graph = {"nodes": [{"id": "n1", "type": "llm", "properties": {"model": "gpt-4o-mini"}}],
             "edges": []}
    result = ce.estimate_design_cost(graph, runs_per_month=1000)
    assert not any(h.startswith("Swap ") for h in result["optimization_hints"])


# ---------------------------------------------------------------------------
# Configurable default model (no hard-coded literal)
# ---------------------------------------------------------------------------

def test_default_model_falls_back_to_constant(monkeypatch):
    monkeypatch.delenv("AADC_DEFAULT_LLM_MODEL", raising=False)
    assert ce._default_llm_model() == AADC_DEFAULT_LLM_MODEL


def test_default_model_env_override(monkeypatch):
    monkeypatch.setenv("AADC_DEFAULT_LLM_MODEL", "claude-haiku-4")
    assert ce._default_llm_model() == "claude-haiku-4"

    # A bare LLM node (no model prop) resolves to the override.
    node = {"id": "n1", "type": "llm"}
    assert ce._resolve_model(node) == "claude-haiku-4"


def test_unspecified_llm_node_uses_default(monkeypatch):
    monkeypatch.delenv("AADC_DEFAULT_LLM_MODEL", raising=False)
    node = {"id": "n1", "type": "llm"}
    assert ce._resolve_model(node) == AADC_DEFAULT_LLM_MODEL.lower()


def test_local_llm_node_resolves_to_ollama_local():
    node = {"id": "n1", "type": "llm-local"}
    assert ce._resolve_model(node) == "ollama-local"

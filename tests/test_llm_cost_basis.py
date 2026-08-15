#!/usr/bin/env python3
"""LLM cost must be derived, and a $0.00 must say WHY. CUI // SP-CTI

`LLMResponse.cost_usd` was documented "when provider computes it" and NO
provider computes it — all nine adapters leave the 0.0 default — so the router
recorded 0.0 for every call ever made. Measured on the live board 2026-08-15:
module_budget_usage held 1,391 rows whose `amount` summed to EXACTLY 0.00, with
max(amount) = 0.0 and not one row above zero, including 557 calls on
kimi-k2.6:cloud. generative_intelligence's $150 monthly USD cap therefore sat at
0% and could never fire, leaving the token cap as the only working control — and
that cap then blocked work on free local inference (183,862 of 418,801 tokens
were ollama-local).

Deterministic: every test passes an explicit config dict. No live database, no
network, no dependence on what args/llm_config.yaml happens to contain today.
"""
from __future__ import annotations

import pathlib
import sys

import pytest

_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from tools.llm.cost_intelligence import (  # noqa: E402
    COST_BASIS_LOCAL_ZERO,
    COST_BASIS_PRICED,
    COST_BASIS_UNPRICED,
    compute_cost_usd,
)

_CFG = {
    "models": {
        "local-model": {"provider": "ollama",
                        "pricing": {"input_per_1k": 0.0, "output_per_1k": 0.0}},
        "cloud-unpriced": {"provider": "ollama_cloud",
                           "pricing": {"input_per_1k": 0.0, "output_per_1k": 0.0}},
        "cloud-no-pricing-key": {"provider": "anthropic"},
        "cloud-priced": {"provider": "openai",
                         "pricing": {"input_per_1k": 0.005, "output_per_1k": 0.015}},
        "vllm-model": {"provider": "mistral_vllm",
                       "pricing": {"input_per_1k": 0.0, "output_per_1k": 0.0}},
    }
}


# --------------------------------------------------------------------------- #
# THE distinction — a zero that means "free" vs a zero that means "unknown"
# --------------------------------------------------------------------------- #

def test_local_zero_and_unpriced_are_both_zero_but_NOT_the_same_fact():
    """The single reason this function returns a basis at all.

    Both cost 0.00. One is the truth (local inference is free); the other is an
    absence of data on a model that certainly is not. Collapsing them is the
    original defect.
    """
    local_cost, local_basis = compute_cost_usd("local-model", 100_000, 20_000, config=_CFG)
    cloud_cost, cloud_basis = compute_cost_usd("cloud-unpriced", 100_000, 20_000, config=_CFG)

    assert local_cost == cloud_cost == 0.0
    assert local_basis == COST_BASIS_LOCAL_ZERO
    assert cloud_basis == COST_BASIS_UNPRICED
    assert local_basis != cloud_basis, "a $0 must say which kind of $0 it is"


def test_unpriced_never_invents_a_number():
    """An invented price is worse than an honest unknown.

    A fabricated spend figure would flow straight into a budget cap and into
    whatever decision that cap drives.
    """
    cost, basis = compute_cost_usd("cloud-unpriced", 10_000_000, 5_000_000, config=_CFG)
    assert cost == 0.0
    assert basis == COST_BASIS_UNPRICED


def test_a_priced_model_produces_a_real_number():
    # 100k in @ $0.005/1k = $0.50; 20k out @ $0.015/1k = $0.30
    cost, basis = compute_cost_usd("cloud-priced", 100_000, 20_000, config=_CFG)
    assert basis == COST_BASIS_PRICED
    assert cost == pytest.approx(0.80)


def test_a_model_with_no_pricing_key_at_all_is_unpriced_not_a_crash():
    cost, basis = compute_cost_usd("cloud-no-pricing-key", 1000, 1000, config=_CFG)
    assert (cost, basis) == (0.0, COST_BASIS_UNPRICED)


def test_an_unknown_model_is_unpriced_rather_than_an_error():
    """A model absent from config must not break the call it is costing."""
    cost, basis = compute_cost_usd("no-such-model", 1000, 1000, config=_CFG)
    assert (cost, basis) == (0.0, COST_BASIS_UNPRICED)


@pytest.mark.parametrize("provider", ["ollama", "mistral_vllm"])
def test_every_local_provider_is_local_zero(provider):
    cfg = {"models": {"m": {"provider": provider, "pricing": {}}}}
    assert compute_cost_usd("m", 999_999, 999_999, config=cfg)[1] == COST_BASIS_LOCAL_ZERO


def test_ollama_cloud_is_not_treated_as_local():
    """The trap: the provider name starts with 'ollama' but bills like a cloud.

    557 of the live usage rows are on kimi-k2.6:cloud via ollama_cloud. Folding
    it in with local would relabel real spend as known-free.
    """
    assert compute_cost_usd("cloud-unpriced", 1000, 1000, config=_CFG)[1] != COST_BASIS_LOCAL_ZERO


# --------------------------------------------------------------------------- #
# Arithmetic edges
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("inp,out", [(0, 0), (None, None), (-5, -5)])
def test_degenerate_token_counts_do_not_produce_negative_or_crashing_costs(inp, out):
    cost, basis = compute_cost_usd("cloud-priced", inp, out, config=_CFG)
    assert cost >= 0.0
    assert basis == COST_BASIS_PRICED


def test_output_only_pricing_still_counts():
    cfg = {"models": {"m": {"provider": "openai",
                            "pricing": {"input_per_1k": 0.0, "output_per_1k": 0.02}}}}
    cost, basis = compute_cost_usd("m", 50_000, 1_000, config=cfg)
    assert basis == COST_BASIS_PRICED
    assert cost == pytest.approx(0.02)


# --------------------------------------------------------------------------- #
# The wiring — a derived cost nobody records is the same bug again
# --------------------------------------------------------------------------- #

def test_llm_response_carries_the_basis_field():
    from tools.llm.provider import LLMResponse

    assert hasattr(LLMResponse(content=""), "cost_basis")


def test_record_module_usage_accepts_and_persists_the_basis():
    import inspect

    from tools.budget import module_budget_tracker as t

    assert "cost_basis" in inspect.signature(t.record_module_usage).parameters
    src = inspect.getsource(t.record_module_usage)
    assert "cost_basis" in src.split("INSERT INTO module_budget_usage")[1][:400], (
        "the basis must reach the INSERT, not just the signature"
    )


def test_the_ddl_and_the_insert_agree_on_cost_basis():
    """Every column named in an INSERT must exist in the schema (CLAUDE.md).

    record_module_usage previously passed a value into a column of the wrong
    type on EVERY call, and because each caller wraps it in `except Exception:
    pass` the table stayed empty while enforcement read it. Same failure shape.
    """
    from tools.budget import module_budget_tracker as t

    assert "cost_basis TEXT" in t.CREATE_MODULE_BUDGET_USAGE_SQL


def test_router_derives_cost_when_the_provider_left_it_zero():
    import inspect

    import tools.llm.router as router

    src = inspect.getsource(router)
    assert "compute_cost_usd" in src, "the router must derive cost, not read a dead field"
    assert "cost_basis=_cost_basis" in src, "and pass the basis to the budget tracker"

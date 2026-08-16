# [TEMPLATE: CUI // SP-CTI]
"""Integration tests: router-level context cache activation + cache token tracking.

Verifies that:
- _apply_context_cache() sets the neutral cache_prefix intent for all 12 canvases
  (cch-cap-01: it no longer stamps Anthropic's cache_control on the request)
- TTX functions (ttx_judge, persona_gen) activate context cache after config fix
- Functions in the excluded list never get the cache_prefix intent
- LLMResponse carries cache_creation_input_tokens and cache_read_input_tokens
- _row_to_response round-trips new cache token fields
"""

from __future__ import annotations

import pytest

try:
    from tools.llm.router import LLMRouter
    from tools.llm.provider import LLMRequest, LLMResponse
    from tools.llm.response_cache import _response_to_row, _row_to_response
    HAS_LLM = True
except ImportError:
    HAS_LLM = False


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def router():
    """Return a fresh LLMRouter (no LLM providers required)."""
    if not HAS_LLM:
        pytest.skip("LLM module unavailable")
    return LLMRouter()


@pytest.fixture
def base_req():
    """Minimal LLMRequest for testing cache hints."""
    if not HAS_LLM:
        pytest.skip("LLM module unavailable")
    return LLMRequest(
        messages=[{"role": "user", "content": "test"}],
        system_prompt="You are helpful.",
    )


# ---------------------------------------------------------------------------
# Context cache activation — 12 canvases
# ---------------------------------------------------------------------------

CANVAS_FUNCTIONS = [
    "ndc_design",
    "sdc_analyze",
    "bdc_generate",
    "pdc_pipeline",
    "odc_observe",
    "idc_integrate",
    "qdc_query",
    "mdc_migrate",
    "aadc_generate",
    "aimc_model",
    "ohc_operate",
    "ddc_data",
]


@pytest.mark.parametrize("function", CANVAS_FUNCTIONS)
def test_canvas_context_cache_activated(router, base_req, function):
    """Every canvas function must activate ephemeral context caching."""
    router._apply_context_cache(function, base_req)
    assert base_req.cache_prefix is True, (
        f"Canvas function '{function}' (prefix '{function.split('_')[0]}') "
        "did not activate context cache — check per_canvas in llm_config.yaml"
    )
    assert base_req.cache_control == "", (
        "the router must not set Anthropic's wire field on a neutral request"
    )
    base_req.cache_prefix = False  # reset for parametrize reuse


# ---------------------------------------------------------------------------
# Context cache activation — TTX / GameDay
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("function", ["ttx_judge", "persona_gen"])
def test_ttx_context_cache_activated(router, base_req, function):
    """TTX functions must activate context cache after config fix."""
    router._apply_context_cache(function, base_req)
    assert base_req.cache_prefix is True, (
        f"TTX function '{function}' did not activate context cache — "
        "ensure it appears in per_function with context_cache: true in llm_config.yaml"
    )
    base_req.cache_prefix = False


# ---------------------------------------------------------------------------
# Context cache activation — core per_function entries
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("function", ["code_generation", "tfw_narrative", "network_diagram_extraction"])
def test_per_function_context_cache_activated(router, base_req, function):
    """Core per_function entries with context_cache: true must activate."""
    router._apply_context_cache(function, base_req)
    assert base_req.cache_prefix is True, (
        f"Per-function '{function}' did not activate context cache"
    )
    base_req.cache_prefix = False


# ---------------------------------------------------------------------------
# Excluded functions must NOT get context cache
# ---------------------------------------------------------------------------

EXCLUDED_FUNCTIONS = [
    "pulse_generation",
    "news_oracle",
    "market_scan",
    "fathomdesk_trap",
    "screenshot_validation",
    "browser_automation",
]


@pytest.mark.parametrize("function", EXCLUDED_FUNCTIONS)
def test_excluded_function_no_context_cache(router, base_req, function):
    """Excluded functions must NOT have context cache enabled."""
    # Excluded list applies to response cache, not context cache directly.
    # But these functions should NOT be in per_function with context_cache:true.
    router._apply_context_cache(function, base_req)
    assert base_req.cache_prefix is False, (
        f"Excluded function '{function}' should not have context cache activated"
    )
    base_req.cache_prefix = False


# ---------------------------------------------------------------------------
# LLMResponse cache token fields
# ---------------------------------------------------------------------------

def test_llmresponse_has_cache_token_fields():
    """LLMResponse must carry cache token tracking fields."""
    if not HAS_LLM:
        pytest.skip("LLM module unavailable")
    resp = LLMResponse()
    assert hasattr(resp, "cache_creation_input_tokens"), "Missing cache_creation_input_tokens field"
    assert hasattr(resp, "cache_read_input_tokens"), "Missing cache_read_input_tokens field"
    assert resp.cache_creation_input_tokens == 0
    assert resp.cache_read_input_tokens == 0


def test_llmresponse_cache_tokens_roundtrip():
    """Cache token counts survive _response_to_row → _row_to_response."""
    if not HAS_LLM:
        pytest.skip("LLM module unavailable")
    resp = LLMResponse(
        content="cached response",
        provider="anthropic",
        model_id="claude-sonnet",
        input_tokens=1000,
        output_tokens=200,
        cache_creation_input_tokens=800,
        cache_read_input_tokens=600,
    )
    row = _response_to_row(resp, "code_generation", "claude-sonnet")
    assert row["cache_creation_input_tokens"] == 800
    assert row["cache_read_input_tokens"] == 600

    # Round-trip through dict (simulates sqlite3.Row as dict)
    restored = _row_to_response({
        **row,
        "tool_calls_json": None,
        "structured_output_json": None,
        "thinking_tokens": 0,
        "duration_ms": 0,
        "stop_reason": "end_turn",
    })
    assert restored.cache_creation_input_tokens == 800
    assert restored.cache_read_input_tokens == 600


def test_llmresponse_cache_tokens_missing_column_graceful():
    """_row_to_response handles rows without cache token columns (old schema)."""
    if not HAS_LLM:
        pytest.skip("LLM module unavailable")
    old_row = {
        "content": "old response",
        "provider": "anthropic",
        "model_id": "claude",
        "input_tokens": 100,
        "output_tokens": 50,
        "thinking_tokens": 0,
        "duration_ms": 300,
        "stop_reason": "end_turn",
        "tool_calls_json": None,
        "structured_output_json": None,
        # Deliberately omitting cache_creation_input_tokens and cache_read_input_tokens
    }
    restored = _row_to_response(old_row)
    assert restored.cache_creation_input_tokens == 0
    assert restored.cache_read_input_tokens == 0


# ---------------------------------------------------------------------------
# TTX LLMRequest construction sanity check
# ---------------------------------------------------------------------------

def test_ttx_llmrequest_valid_construction():
    """LLMRequest must be constructable with messages+system_prompt (TTX pattern)."""
    if not HAS_LLM:
        pytest.skip("LLM module unavailable")
    req = LLMRequest(
        messages=[{"role": "user", "content": "Score this response."}],
        system_prompt="You are a TTX judge.",
        max_tokens=512,
    )
    assert req.messages[0]["role"] == "user"
    assert req.system_prompt == "You are a TTX judge."
    assert req.max_tokens == 512


def test_ttx_llmrequest_rejects_invalid_fields():
    """LLMRequest must reject unknown fields (guards against regression)."""
    if not HAS_LLM:
        pytest.skip("LLM module unavailable")
    with pytest.raises(TypeError):
        LLMRequest(
            function="ttx_judge",        # invalid field
            user_message="hello",         # invalid field
        )

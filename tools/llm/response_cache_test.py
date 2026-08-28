# [TEMPLATE: CUI // SP-CTI]
"""Unit tests for tools/llm/response_cache.py.

Run: python -m pytest tools/llm/response_cache_test.py -v
"""

import json
from unittest.mock import patch

import pytest

from tools.llm.response_cache import (
    LLMResponseCache,
    canonical_key,
    _response_to_row,
    _row_to_response,
)

# Graceful fallback if LLMResponse is unavailable
try:
    from tools.llm.provider import LLMResponse
except ImportError:
    LLMResponse = None


class DummyRequest:
    def __init__(self, **kwargs):
        self.messages = kwargs.get("messages", [])
        self.system_prompt = kwargs.get("system_prompt", "")
        self.temperature = kwargs.get("temperature", 1.0)
        self.max_tokens = kwargs.get("max_tokens", 4096)
        self.tools = kwargs.get("tools", None)
        self.output_schema = kwargs.get("output_schema", None)


@pytest.fixture
def cache():
    """Return a fresh LLMResponseCache instance with test config."""
    cfg = {
        "enabled": True,
        "ttl_seconds": 3600,
        "max_entries": 100,
        "excluded_functions": ["excluded_fn"],
        "per_function": {},
        "per_canvas": {},
    }
    # LLMResponseCache is a PROCESS SINGLETON whose __init__ returns early once
    # `_initialized` is set, so `config=cfg` was SILENTLY IGNORED whenever anything
    # earlier in the same process had already constructed it -- and the cache is
    # constructed on the ordinary serving path. This fixture then believed it had
    # `max_entries: 100` and `excluded_functions: ["excluded_fn"]` while running
    # against the deployment's real config, so `test_lru_eviction` and
    # `test_excluded_functions_not_cached` passed ALONE and failed IN-SUITE
    # (measured 2026-08-27: 105 entries against an asserted ceiling of 100).
    # Dropping the instance is what makes the declared config actually apply.
    LLMResponseCache._instance = None
    c = LLMResponseCache(config=cfg)
    assert c._config is cfg, "the fixture's config must be the one under test"
    # Clear any existing entries
    c.clear()
    yield c
    # Leave the process as we found it, so the next module builds its own.
    LLMResponseCache._instance = None


def test_canonical_key_determinism():
    """Same request must produce identical key."""
    req = DummyRequest(
        messages=[{"role": "user", "content": "hello"}],
        system_prompt="You are helpful.",
        temperature=0.5,
    )
    k1 = canonical_key("code_generation", "claude-sonnet", req)
    k2 = canonical_key("code_generation", "claude-sonnet", req)
    assert k1 == k2
    assert len(k1) == 64  # SHA-256 hex


def test_canonical_key_order_independence():
    """Message order should not affect key (canonical sorting)."""
    req1 = DummyRequest(
        messages=[
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi"},
        ]
    )
    req2 = DummyRequest(
        messages=[
            {"role": "assistant", "content": "hi"},
            {"role": "user", "content": "hello"},
        ]
    )
    k1 = canonical_key("fn", "model", req1)
    k2 = canonical_key("fn", "model", req2)
    assert k1 == k2


def test_cache_hit_miss(cache):
    """Store then retrieve returns identical data; miss returns None."""
    if LLMResponse is None:
        pytest.skip("LLMResponse unavailable")

    resp = LLMResponse(content="hello world", provider="anthropic", model_id="claude")
    resp.input_tokens = 10
    resp.output_tokens = 5

    key = canonical_key("test_fn", "model", DummyRequest(messages=[{"role": "user", "content": "hi"}]))

    # Miss before store
    assert cache.get(key) is None

    cache.set(key, resp)
    hit = cache.get(key)
    assert hit is not None
    assert hit.content == "hello world"
    assert hit.input_tokens == 10
    assert hit.output_tokens == 5


def test_cache_ttl_expiry(cache):
    """Expired entries return None."""
    if LLMResponse is None:
        pytest.skip("LLMResponse unavailable")

    resp = LLMResponse(content="expire me", provider="anthropic")
    key = canonical_key("ttl_fn", "model", DummyRequest())

    # Store with negative TTL so it expires immediately
    cache.set(key, resp, ttl_seconds=-1)
    assert cache.get(key) is None


def test_excluded_functions_not_cached(cache):
    """Excluded functions are never stored."""
    if LLMResponse is None:
        pytest.skip("LLMResponse unavailable")

    resp = LLMResponse(content="excluded", provider="anthropic")
    key = canonical_key("excluded_fn", "model", DummyRequest())

    cache.set(key, resp, function="excluded_fn")
    assert cache.get(key) is None


def test_cache_jitter(cache):
    """Cache hit applies timing jitter."""
    if LLMResponse is None:
        pytest.skip("LLMResponse unavailable")

    resp = LLMResponse(content="jitter", provider="anthropic")
    key = canonical_key("jitter_fn", "model", DummyRequest())
    cache.set(key, resp)

    with patch("time.sleep") as mock_sleep:
        cache.get(key, jitter_ms=50)
        mock_sleep.assert_called_once()
        args, _ = mock_sleep.call_args
        assert 0 <= args[0] <= 0.05


def test_lru_eviction(cache):
    """LRU sweep evicts oldest when max_entries exceeded."""
    if LLMResponse is None:
        pytest.skip("LLMResponse unavailable")

    # Insert 105 items (max_entries=100)
    for i in range(105):
        req = DummyRequest(messages=[{"role": "user", "content": str(i)}])
        key = canonical_key("evict", "model", req)
        resp = LLMResponse(content=str(i), provider="anthropic")
        cache.set(key, resp)

    stats = cache.stats()
    assert stats["total_entries"] <= 100


def test_invalidate_by_function(cache):
    """invalidate(function=...) removes only matching rows."""
    if LLMResponse is None:
        pytest.skip("LLMResponse unavailable")

    cache.set("key1", LLMResponse(content="a", provider="anthropic"), ttl_seconds=3600)
    cache.set("key2", LLMResponse(content="b", provider="anthropic"), ttl_seconds=3600)

    # We need proper keys
    k1 = canonical_key("fn_a", "model", DummyRequest())
    k2 = canonical_key("fn_b", "model", DummyRequest())
    cache.set(k1, LLMResponse(content="a", provider="anthropic"), function="fn_a")
    cache.set(k2, LLMResponse(content="b", provider="anthropic"), function="fn_b")

    deleted = cache.invalidate(function="fn_a")
    assert deleted == 1
    assert cache.get(k1) is None
    assert cache.get(k2) is not None


def test_stats(cache):
    """stats() returns expected shape."""
    stats = cache.stats()
    assert "total_entries" in stats
    assert "total_hits" in stats
    assert "enabled" in stats
    assert "backend" in stats


def test_response_to_row_roundtrip():
    """Serialization round-trip preserves data."""
    if LLMResponse is None:
        pytest.skip("LLMResponse unavailable")

    resp = LLMResponse(
        content="test",
        provider="anthropic",
        model_id="claude",
        input_tokens=10,
        output_tokens=5,
        tool_calls=[{"id": "1", "name": "fn", "input": {}}],
        structured_output={"foo": "bar"},
    )
    row = _response_to_row(resp, "fn", "model")
    assert row["content"] == "test"
    assert row["input_tokens"] == 10
    assert json.loads(row["tool_calls_json"]) == [{"id": "1", "name": "fn", "input": {}}]
    assert json.loads(row["structured_output_json"]) == {"foo": "bar"}


def test_row_to_response_dict_fallback():
    """_row_to_response works with dict-like rows."""
    row = {
        "content": "fallback",
        "provider": "openai",
        "model_id": "gpt",
        "input_tokens": 3,
        "output_tokens": 2,
        "tool_calls_json": None,
        "structured_output_json": None,
        "thinking_tokens": 0,
        "duration_ms": 100,
        "stop_reason": "stop",
    }
    result = _row_to_response(row)
    assert result.content == "fallback"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

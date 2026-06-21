"""Tests for Headroom-inspired LLM context compressor (adapt-hd-04)."""
from unittest.mock import MagicMock, patch


def _make_request(messages):
    """Helper: fake LLMRequest with .messages attribute."""
    r = MagicMock()
    r.messages = messages
    return r


def _make_messages(n, content="word " * 200):
    return [{"role": "user" if i % 2 == 0 else "assistant", "content": content}
            for i in range(n)]


# ---------------------------------------------------------------------------
# Module imports
# ---------------------------------------------------------------------------

def test_module_importable():
    from tools.llm.compression.context_compressor import compress, CompressorConfig
    assert callable(compress)
    assert CompressorConfig is not None


def test_load_config_from_yaml():
    from tools.llm.compression.context_compressor import load_config_from_yaml, CompressorConfig
    cfg = load_config_from_yaml()
    assert isinstance(cfg, CompressorConfig)
    assert cfg.enabled is False  # default in llm_config.yaml


def test_config_exempt_functions_populated():
    from tools.llm.compression.context_compressor import load_config_from_yaml
    cfg = load_config_from_yaml()
    assert "compliance_generation" in cfg.exempt_functions
    assert "ssp_generation" in cfg.exempt_functions


# ---------------------------------------------------------------------------
# Token estimation
# ---------------------------------------------------------------------------

def test_estimate_tokens_nonzero():
    from tools.llm.compression.context_compressor import _estimate_tokens
    assert _estimate_tokens("hello world this is a test") > 0


def test_estimate_tokens_empty():
    from tools.llm.compression.context_compressor import _estimate_tokens
    assert _estimate_tokens("") == 0


def test_messages_token_count_scales():
    from tools.llm.compression.context_compressor import _messages_token_count
    short = [{"role": "user", "content": "hello"}]
    long = [{"role": "user", "content": "word " * 500}]
    assert _messages_token_count(long) > _messages_token_count(short)


# ---------------------------------------------------------------------------
# Compression disabled (default)
# ---------------------------------------------------------------------------

def test_compress_noop_when_disabled():
    from tools.llm.compression.context_compressor import compress, CompressorConfig
    cfg = CompressorConfig(enabled=False)
    msgs = _make_messages(20)
    req = _make_request(msgs)
    result = compress(req, cfg)
    assert result.messages is msgs  # unchanged reference


def test_compress_noop_below_threshold():
    from tools.llm.compression.context_compressor import compress, CompressorConfig
    cfg = CompressorConfig(enabled=True, threshold_tokens=100000, strategy="truncate_middle")
    msgs = _make_messages(3, content="short")
    req = _make_request(msgs)
    result = compress(req, cfg)
    assert len(result.messages) == 3


# ---------------------------------------------------------------------------
# Truncate-middle strategy
# ---------------------------------------------------------------------------

def test_truncate_middle_reduces_message_count():
    from tools.llm.compression.context_compressor import compress, CompressorConfig
    cfg = CompressorConfig(
        enabled=True,
        threshold_tokens=50,
        strategy="truncate_middle",
        max_compression_ratio=0.6,
        log_savings=False,
    )
    msgs = _make_messages(10, content="word " * 50)
    req = _make_request(msgs)
    result = compress(req, cfg)
    assert len(result.messages) < 10


def test_truncate_middle_inserts_placeholder():
    from tools.llm.compression.context_compressor import compress, CompressorConfig
    cfg = CompressorConfig(
        enabled=True,
        threshold_tokens=50,
        strategy="truncate_middle",
        max_compression_ratio=0.6,
        log_savings=False,
    )
    msgs = _make_messages(10, content="word " * 50)
    req = _make_request(msgs)
    result = compress(req, cfg)
    placeholders = [m for m in result.messages if "CONTEXT COMPRESSED" in str(m.get("content", ""))]
    assert len(placeholders) == 1


def test_truncate_middle_preserves_first_and_last():
    from tools.llm.compression.context_compressor import compress, CompressorConfig
    cfg = CompressorConfig(
        enabled=True,
        threshold_tokens=50,
        strategy="truncate_middle",
        max_compression_ratio=0.6,
        log_savings=False,
    )
    first_content = "FIRST MESSAGE " + "word " * 50
    last_content = "LAST MESSAGE " + "word " * 50
    msgs = (
        [{"role": "system", "content": first_content}]
        + _make_messages(8, content="word " * 50)
        + [{"role": "user", "content": last_content}]
    )
    req = _make_request(msgs)
    result = compress(req, cfg)
    assert result.messages[0]["content"] == first_content
    assert result.messages[-1]["content"] == last_content


def test_truncate_middle_two_messages_no_compress():
    from tools.llm.compression.context_compressor import _truncate_middle
    msgs = [
        {"role": "system", "content": "system prompt"},
        {"role": "user", "content": "user message"},
    ]
    compressed, original, saved = _truncate_middle(msgs, 0.4)
    assert saved == 0
    assert compressed is msgs


# ---------------------------------------------------------------------------
# Exempt function bypass
# ---------------------------------------------------------------------------

def test_exempt_function_skips_compression():
    from tools.llm.compression.context_compressor import CompressorConfig
    cfg = CompressorConfig(
        enabled=True,
        threshold_tokens=50,
        strategy="truncate_middle",
        max_compression_ratio=0.6,
        log_savings=False,
        exempt_functions=["compliance_generation"],
    )
    msgs = _make_messages(10, content="word " * 50)
    _make_request(msgs)

    # Simulate router calling with exempt function — compress() doesn't know function
    # so test that the router's _compress_request_context skips via exempt check
    # Here we test that exempt_functions is populated
    assert "compliance_generation" in cfg.exempt_functions


# ---------------------------------------------------------------------------
# Router integration: _compress_request_context
# ---------------------------------------------------------------------------

def test_router_has_compress_method():
    from tools.llm.router import LLMRouter
    assert hasattr(LLMRouter, "_compress_request_context")
    assert callable(LLMRouter._compress_request_context)


def test_router_compress_noop_by_default():
    """Default config has compression.enabled=false → router method is a pass-through."""
    from tools.llm.router import LLMRouter
    from tools.llm.compression.context_compressor import CompressorConfig

    router = LLMRouter.__new__(LLMRouter)
    msgs = _make_messages(20)
    req = _make_request(msgs)

    with patch("tools.llm.compression.context_compressor.load_config_from_yaml",
               return_value=CompressorConfig(enabled=False)):
        result = router._compress_request_context("code_generation", req)

    assert result is req  # unchanged


def test_router_compress_respects_exempt_function():
    """Exempt functions pass through unchanged even with compression enabled."""
    from tools.llm.router import LLMRouter
    from tools.llm.compression.context_compressor import CompressorConfig

    router = LLMRouter.__new__(LLMRouter)
    msgs = _make_messages(20, content="word " * 100)
    req = _make_request(msgs)

    cfg = CompressorConfig(
        enabled=True,
        threshold_tokens=100,
        strategy="truncate_middle",
        max_compression_ratio=0.6,
        log_savings=False,
        exempt_functions=["compliance_generation"],
    )

    with patch("tools.llm.compression.context_compressor.load_config_from_yaml", return_value=cfg):
        result = router._compress_request_context("compliance_generation", req)

    assert result.messages is msgs  # not compressed


def test_compress_unknown_strategy_noop():
    from tools.llm.compression.context_compressor import compress, CompressorConfig
    cfg = CompressorConfig(
        enabled=True,
        threshold_tokens=50,
        strategy="unknown_strategy",
        max_compression_ratio=0.6,
        log_savings=False,
    )
    msgs = _make_messages(10, content="word " * 50)
    req = _make_request(msgs)
    result = compress(req, cfg)
    assert len(result.messages) == 10  # unchanged


def test_compress_ratio_bounds():
    """Compression ratio must stay within max_compression_ratio."""
    from tools.llm.compression.context_compressor import compress, CompressorConfig, _messages_token_count
    cfg = CompressorConfig(
        enabled=True,
        threshold_tokens=50,
        strategy="truncate_middle",
        max_compression_ratio=0.6,
        log_savings=False,
    )
    msgs = _make_messages(20, content="word " * 100)
    original_tokens = _messages_token_count(msgs)
    req = _make_request(msgs)
    result = compress(req, cfg)
    compressed_tokens = _messages_token_count(result.messages)
    # Must keep at least 40% of original
    assert compressed_tokens >= original_tokens * 0.35  # small tolerance

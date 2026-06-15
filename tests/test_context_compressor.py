# CUI // SP-CTI
"""Tests for tools/llm/context_compressor.py"""

import json
import os
import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ["ICDEV_STORAGE_BACKEND"] = "sqlite"

from tools.llm.context_compressor import (
    ContextCompressor,
    CompressedContext,
    compress_messages,
    estimate_tokens,
    global_stats,
    handle_compress_context,
    _smart_crush_text,
    _code_compress,
    _cache_align_prefix,
    main,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

SIMPLE_MESSAGES = [
    {"role": "user", "content": "Hello, please explain NIST RMF."},
    {"role": "assistant", "content": "NIST RMF is a risk management framework.\n\n\nIt has six steps."},
]

CODE_MESSAGES = [
    {
        "role": "user",
        "content": (
            "def foo():\n"
            "    # A comment\n"
            "    x = 1\n"
            "\n\n"
            "    return x\n"
        ),
    }
]

LARGE_MESSAGES = [
    {
        "role": "user",
        "content": "This is a repeating test sentence about AI agents. " * 200,
    }
]


# ---------------------------------------------------------------------------
# estimate_tokens
# ---------------------------------------------------------------------------

def test_estimate_tokens_empty():
    assert estimate_tokens("") == 0


def test_estimate_tokens_basic():
    # ~4 chars per token heuristic
    assert estimate_tokens("abcd") == 1
    assert estimate_tokens("a" * 100) == 25


# ---------------------------------------------------------------------------
# _smart_crush_text
# ---------------------------------------------------------------------------

def test_smart_crush_removes_excess_blanks():
    text = "line one\n\n\n\nline two"
    compressed, original = _smart_crush_text(text)
    assert "\n\n\n" not in compressed
    assert original == text


def test_smart_crush_collapses_spaces():
    text = "word   another"
    compressed, original = _smart_crush_text(text)
    assert "   " not in compressed
    assert original == text


def test_smart_crush_preserves_content():
    text = "important content here"
    compressed, _ = _smart_crush_text(text)
    assert "important" in compressed
    assert "content" in compressed


# ---------------------------------------------------------------------------
# _code_compress
# ---------------------------------------------------------------------------

def test_code_compress_strips_comments():
    code = "def foo():\n    # comment line\n    return 1\n"
    compressed, original = _code_compress(code)
    assert "# comment line" not in compressed
    assert "def foo():" in compressed
    assert original == code


def test_code_compress_folds_blank_lines():
    code = "x = 1\n\n\n\ny = 2\n"
    compressed, _ = _code_compress(code)
    assert "\n\n\n" not in compressed


def test_code_compress_keeps_shebang():
    code = "#!/usr/bin/env python3\nprint('hello')\n"
    compressed, _ = _code_compress(code)
    assert "#!/usr/bin/env python3" in compressed


# ---------------------------------------------------------------------------
# _cache_align_prefix
# ---------------------------------------------------------------------------

def test_cache_align_strips_trailing_whitespace():
    messages = [{"role": "user", "content": "  hello   "}]
    aligned = _cache_align_prefix(messages)
    assert aligned[0]["content"] == "hello"


def test_cache_align_preserves_role():
    messages = [{"role": "system", "content": "You are a helper.  "}]
    aligned = _cache_align_prefix(messages)
    assert aligned[0]["role"] == "system"


# ---------------------------------------------------------------------------
# ContextCompressor.compress
# ---------------------------------------------------------------------------

def test_compress_returns_compressed_context():
    c = ContextCompressor(log_to_db=False)
    result = c.compress(SIMPLE_MESSAGES, budget_tokens=4000)
    assert isinstance(result, CompressedContext)
    assert result.original_tokens > 0
    assert result.compressed_tokens > 0
    assert result.compression_ratio >= 1.0
    assert result.reversible is True


def test_compress_large_content_reduces_tokens():
    c = ContextCompressor(log_to_db=False)
    result = c.compress(LARGE_MESSAGES, budget_tokens=2000)
    assert result.compressed_tokens <= result.original_tokens


def test_compress_code_content():
    c = ContextCompressor(log_to_db=False)
    result = c.compress(CODE_MESSAGES, budget_tokens=4000, content_type="code")
    assert result.compressed_tokens > 0
    assert result.method in ("headroom", "builtin")


def test_compress_auto_detects_code():
    c = ContextCompressor(log_to_db=False)
    result = c.compress(CODE_MESSAGES, budget_tokens=4000, content_type="auto")
    assert result.compressed_tokens > 0


def test_compress_preserves_message_roles():
    c = ContextCompressor(log_to_db=False)
    result = c.compress(SIMPLE_MESSAGES, budget_tokens=4000)
    roles = [m["role"] for m in result.messages]
    assert roles == ["user", "assistant"]


def test_compress_empty_messages():
    c = ContextCompressor(log_to_db=False)
    result = c.compress([], budget_tokens=4000)
    assert result.messages == []
    assert result.original_tokens == 0


# ---------------------------------------------------------------------------
# ContextCompressor.decompress
# ---------------------------------------------------------------------------

def test_decompress_restores_original():
    c = ContextCompressor(log_to_db=False)
    result = c.compress(SIMPLE_MESSAGES, budget_tokens=4000)
    restored = c.decompress(result)
    assert len(restored) == len(SIMPLE_MESSAGES)
    for original, r in zip(SIMPLE_MESSAGES, restored):
        assert original["role"] == r["role"]
        assert original["content"] in r["content"] or r["content"] in original["content"]


def test_decompress_empty_context():
    c = ContextCompressor(log_to_db=False)
    empty_ctx = CompressedContext(
        messages=[],
        original_tokens=0,
        compressed_tokens=0,
        compression_ratio=1.0,
        method="builtin",
        reversible=True,
    )
    restored = c.decompress(empty_ctx)
    assert restored == []


# ---------------------------------------------------------------------------
# Module-level compress_messages
# ---------------------------------------------------------------------------

def test_compress_messages_convenience():
    result = compress_messages(SIMPLE_MESSAGES, budget_tokens=4000)
    assert isinstance(result, CompressedContext)
    assert result.compressed_tokens > 0


# ---------------------------------------------------------------------------
# MCP handler
# ---------------------------------------------------------------------------

def test_handle_compress_context_happy_path():
    response = handle_compress_context({
        "messages": SIMPLE_MESSAGES,
        "budget_tokens": 4000,
        "content_type": "auto",
    })
    assert response["status"] == "ok"
    assert "messages" in response
    assert response["reversible"] is True
    assert response["compression_ratio"] >= 1.0


def test_handle_compress_context_missing_messages():
    response = handle_compress_context({})
    assert response["status"] == "error"
    assert "messages" in response["error"].lower()


# ---------------------------------------------------------------------------
# global_stats
# ---------------------------------------------------------------------------

def test_global_stats_returns_dict():
    stats = global_stats()
    assert isinstance(stats, dict)
    assert "headroom_available" in stats
    assert "total_compressions" in stats


# ---------------------------------------------------------------------------
# CLI (--bench, --stats)
# ---------------------------------------------------------------------------

def test_cli_bench_json(capsys):
    rc = main(["--bench", "--json"])
    assert rc == 0
    out = capsys.readouterr().out
    data = json.loads(out)
    assert "compression_ratio" in data
    assert "original_tokens" in data


def test_cli_stats_json(capsys):
    rc = main(["--stats", "--json"])
    assert rc == 0
    out = capsys.readouterr().out
    data = json.loads(out)
    assert "headroom_available" in data


def test_cli_no_args(capsys):
    rc = main([])
    assert rc == 0


# ---------------------------------------------------------------------------
# DB unavailable graceful degradation
# ---------------------------------------------------------------------------

def test_compress_graceful_db_failure():
    """Compression must not crash when DB is unavailable."""
    with patch("tools.llm.context_compressor.get_connection", side_effect=Exception("DB down")):
        c = ContextCompressor(log_to_db=True)
        # compress should not raise even if logging fails
        result = c.compress(SIMPLE_MESSAGES, budget_tokens=4000)
        assert isinstance(result, CompressedContext)


def test_global_stats_graceful_db_failure():
    with patch("tools.llm.context_compressor.get_connection", side_effect=Exception("DB down")):
        stats = global_stats()
        assert stats["total_compressions"] == 0

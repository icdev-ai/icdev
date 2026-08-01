#!/usr/bin/env python3
# CUI // SP-CTI
"""Headroom-inspired LLM request context compressor (adapt-hd-02).

Applies reversible token-budget-aware compression to LLMRequest message lists
before they reach a provider. Uses the `truncate_middle` strategy: retain the
first 40% and last 40% of the message list by estimated token count, dropping
the middle section with a placeholder.

Integration point: called from router.py::invoke() when
  compression.enabled = true  (args/llm_config.yaml)
  AND function not in compression.exempt_functions

Usage (programmatic):
    from tools.llm.compression.context_compressor import compress, CompressorConfig
    cfg = CompressorConfig(enabled=True, threshold_tokens=6000, max_compression_ratio=0.6)
    compressed_request = compress(request, cfg)

Usage (standalone check):
    python tools/llm/compression/context_compressor.py --estimate "message text" --json
"""
from __future__ import annotations

import json
from tools.logging.icdev_logger import get_logger
from dataclasses import dataclass, field
from typing import Any

logger = get_logger(__name__)

_TOKENS_PER_WORD = 1.35  # rough estimate without tiktoken


@dataclass
class CompressorConfig:
    enabled: bool = False
    threshold_tokens: int = 6000
    strategy: str = "truncate_middle"
    max_compression_ratio: float = 0.6
    log_savings: bool = True
    exempt_functions: list[str] = field(default_factory=list)


def _estimate_tokens(text: str) -> int:
    if not text:
        return 0
    return max(1, int(len(text.split()) * _TOKENS_PER_WORD))


def _messages_token_count(messages: list[dict]) -> int:
    total = 0
    for msg in messages:
        content = msg.get("content") or ""
        if isinstance(content, list):
            content = " ".join(
                c.get("text", "") if isinstance(c, dict) else str(c)
                for c in content
            )
        total += _estimate_tokens(str(content)) + 4  # per-message overhead
    return total


def _truncate_middle(messages: list[dict], ratio: float) -> tuple[list[dict], int, int]:
    """Keep first 40% and last 40% of messages by token count; drop the middle.

    Returns:
        (compressed_messages, original_token_count, saved_token_count)
    """
    if len(messages) <= 2:
        return messages, _messages_token_count(messages), 0

    original_count = _messages_token_count(messages)
    keep_tokens = int(original_count * (1.0 - ratio))  # tokens to keep

    head_tokens = 0
    tail_tokens = 0
    head_idx = 0

    # Anchor first and last message (system prompt + latest user message)
    first = [messages[0]]
    last = [messages[-1]]
    head_tokens += _messages_token_count(first)
    tail_tokens += _messages_token_count(last)

    mid_start = 1
    mid_end = len(messages) - 1

    # Expand head from left
    for i in range(1, len(messages) - 1):
        cost = _messages_token_count([messages[i]])
        if head_tokens + tail_tokens + cost > keep_tokens:
            mid_start = i
            break
        head_tokens += cost
        head_idx = i
        mid_start = i + 1

    # Expand tail from right
    for i in range(len(messages) - 2, head_idx, -1):
        cost = _messages_token_count([messages[i]])
        if head_tokens + tail_tokens + cost > keep_tokens:
            mid_end = i
            break
        tail_tokens += cost
        mid_end = i

    dropped_count = mid_end - mid_start
    if dropped_count <= 0:
        return messages, original_count, 0

    placeholder = {
        "role": "system",
        "content": (
            f"[CONTEXT COMPRESSED: {dropped_count} messages omitted from conversation history "
            f"to fit token budget. Earlier context retained above; recent context below.]"
        ),
    }

    compressed = (
        messages[:mid_start]
        + [placeholder]
        + messages[mid_end:]
    )

    saved = original_count - _messages_token_count(compressed)
    return compressed, original_count, max(0, saved)


def compress(request: Any, config: CompressorConfig) -> Any:
    """Apply compression to an LLMRequest if enabled and threshold exceeded.

    Args:
        request: LLMRequest object (must have .messages attribute).
        config: CompressorConfig with strategy + thresholds.

    Returns:
        The request (mutated in-place if compressed; returned unchanged otherwise).
    """
    if not config.enabled:
        return request

    messages = getattr(request, "messages", None)
    if not messages or not isinstance(messages, list):
        return request

    token_count = _messages_token_count(messages)
    if token_count <= config.threshold_tokens:
        return request

    strategy = config.strategy or "truncate_middle"

    if strategy == "truncate_middle":
        ratio = min(0.9, max(0.1, 1.0 - config.max_compression_ratio))
        compressed_msgs, original_tokens, saved = _truncate_middle(messages, ratio)
    else:
        logger.warning("context_compressor: unknown strategy %r, skipping", strategy)
        return request

    if saved <= 0:
        return request

    request.messages = compressed_msgs

    if config.log_savings:
        try:
            from tools.db.storage import get_connection
            conn = get_connection()
            conn.execute(
                """INSERT INTO audit_trail
                   (id, action, details, classification)
                   VALUES (%s, %s, %s, %s)
                   ON CONFLICT DO NOTHING""",
                (
                    f"compress-{id(request)}-{original_tokens}",
                    "llm_context_compressed",
                    json.dumps({
                        "strategy": strategy,
                        "original_tokens": original_tokens,
                        "compressed_tokens": original_tokens - saved,
                        "saved_tokens": saved,
                        "compression_ratio": round(saved / original_tokens, 3),
                    }),
                    "CUI",
                ),
            )
            conn.commit()
            conn.close()
        except Exception as exc:  # noqa: BLE001 - best-effort persistence; logged, never raised
            # Logging is best-effort; never block the LLM call
            logger.warning("compress: best-effort INSERT into audit_trail failed (non-blocking): %s", exc)

    logger.info(
        "context_compressor: strategy=%s original=%d saved=%d (%.0f%%)",
        strategy, original_tokens, saved, 100 * saved / original_tokens
    )
    return request


def load_config_from_yaml() -> CompressorConfig:
    """Load compression config from args/llm_config.yaml."""
    try:
        import yaml
        from pathlib import Path
        cfg_path = Path(__file__).resolve().parent.parent.parent.parent / "args" / "llm_config.yaml"
        if not cfg_path.exists():
            return CompressorConfig()
        with open(cfg_path, encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}
        c = raw.get("compression") or {}
        return CompressorConfig(
            enabled=bool(c.get("enabled", False)),
            threshold_tokens=int(c.get("threshold_tokens", 6000)),
            strategy=str(c.get("strategy", "truncate_middle")),
            max_compression_ratio=float(c.get("max_compression_ratio", 0.6)),
            log_savings=bool(c.get("log_savings", True)),
            exempt_functions=list(c.get("exempt_functions") or []),
        )
    except Exception as exc:
        logger.warning("context_compressor: failed to load config: %s", exc)
        return CompressorConfig()


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Context Compressor (adapt-hd-02)")
    parser.add_argument("--estimate", metavar="TEXT", help="Estimate token count")
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args()

    if args.estimate:
        count = _estimate_tokens(args.estimate)
        if args.as_json:
            print(json.dumps({"estimated_tokens": count}))
        else:
            print(f"Estimated tokens: {count}")
    else:
        cfg = load_config_from_yaml()
        if args.as_json:
            print(json.dumps({
                "enabled": cfg.enabled,
                "threshold_tokens": cfg.threshold_tokens,
                "strategy": cfg.strategy,
                "max_compression_ratio": cfg.max_compression_ratio,
                "exempt_functions": cfg.exempt_functions,
            }))
        else:
            print(f"Compression config loaded: enabled={cfg.enabled}")

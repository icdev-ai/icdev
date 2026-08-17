#!/usr/bin/env python3
# CUI // SP-CTI
"""Measure Ollama's prefix-cache payoff in LATENCY, which is its only unit (cch-prov-03).

Ollama reuses the server-side KV cache when a request shares a prompt prefix with
the previous one. That is a real win, but a local model has no per-token price, so
there is nothing to bill and ``cache_read_input_tokens`` is the wrong instrument —
it stays 0 no matter how well caching works. The honest metric is **prompt-eval
(prefill) time**: how long the server spent chewing the prompt before the first
output token.

This tool measures it the only way that is defensible: run the same workload with a
prefix the server has just seen (warm) and with one it has never seen (cold), and
compare. It reports what it measured, and reports UNMEASURABLE when it cannot —
never a fabricated number, and never a dollar figure for local inference.

Method, and why each step is here:

1. **Warm the MODEL first.** The first call after a cold start pays for loading
   weights onto the accelerator. Measured on this deployment, that made an
   otherwise-warm call read 5,148 ms against a true ~8 ms. Left in, it would have
   dominated whichever leg happened to run first and produced a spectacular,
   meaningless number.
2. **Alternate cold and warm** rather than running each leg in a block, so thermal
   drift and background GPU contention hit both legs equally.
3. **Cold = a prefix never sent before, by ANY run** (per-run nonce, not a fixed
   seed). Ollama's KV cache outlives the process, so fixed seeds made this tool
   measure correctly exactly once and then silently compare warm against warm —
   an immediate re-run reported 0.7x. Re-sending an old prefix is not a cold
   measurement; it is a slower warm one. See :func:`cold_nonce`.
4. **Report the median**, not the mean, and print the raw samples. The first WARM
   sample is a cold call by construction — the shared prefix is new on iteration 0
   — and background GPU contention produces genuine outliers in the cold leg
   (observed 103, 235, 496 ms within one run). A mean lets either dominate at n=5.
5. **Do not use ``prompt_eval_count`` as the hit signal.** Measured constant at
   1,914 tokens across one cold and four warm calls: Ollama reports the FULL prompt
   length whether or not it re-evaluated it. Only the duration moves. A "cached
   tokens" figure derived from the count here would be fiction.

Usage:
    python tools/llm/ollama_prefix_latency.py --json
    python tools/llm/ollama_prefix_latency.py --model qwen3:4b --repeats 7
    python tools/llm/ollama_prefix_latency.py --base-url http://gpu-box:11434
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import uuid
from typing import Any, Dict, List, Optional

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from tools.llm.provider import (  # noqa: E402
    PREFIX_CACHE_LOCAL,
    LLMRequest,
    resolve_prefix_cache_capability,
)

#: Status values. UNMEASURABLE is a first-class outcome, not an error to swallow:
#: "Ollama is not running" and "caching does not help" must never read the same.
STATUS_MEASURED = "measured"
STATUS_UNMEASURABLE = "unmeasurable"

DEFAULT_MODEL = os.getenv("ICDEV_OLLAMA_PROBE_MODEL", "qwen3:0.6b")
DEFAULT_REPEATS = 5
DEFAULT_PARAGRAPHS = 60


def cold_nonce() -> str:
    """A value no previous run of this tool can have used.

    THE cold leg depends on this, and getting it wrong is silent. The seeds were
    originally fixed strings (``COLD0``..``COLD4``), which made the tool measure
    correctly exactly once: Ollama's KV cache outlives the process, so on every
    later run the "cold" prefixes had already been evaluated and came back warm.
    Observed directly — a first run read 64.5 ms cold against 9.1 ms warm, and an
    immediate re-run read ``cold_raw=[10.8, 9.4, 10.1, 70.6, 69.7]`` for a nominal
    0.7x "speedup". Warm-versus-warm, reported as though it were cold-versus-warm.

    A tool that only works the first time and then quietly reports noise is worse
    than no tool, because the second reading looks just as authoritative.
    """
    return uuid.uuid4().hex[:12]


def build_prefix(seed: str, paragraphs: int = DEFAULT_PARAGRAPHS) -> str:
    """A multi-KB prefix of fixed SHAPE, identified by ``seed``.

    Deterministic in size and structure so runs are comparable; the caller varies
    ``seed`` (see :func:`cold_nonce`) to control what the server has already seen.
    The seed appears in the FIRST sentence on purpose: KV reuse matches on a shared
    leading prefix, so a seed buried at the end would leave the whole body cached
    and the cold leg would not be cold.
    """
    header = f"Reference corpus {seed}. You are a compliance assistant.\n"
    body = "\n".join(
        f"Paragraph {i} of corpus {seed}. The systems engineer reviews control "
        f"AU-{i % 20} for the authorization boundary and records the finding."
        for i in range(paragraphs)
    )
    return header + body


def _provider(base_url: str):
    from tools.llm.ollama_provider import OllamaProvider

    return OllamaProvider(base_url=base_url)


def _one_call(provider, model: str, prefix: str, question: str) -> Optional[float]:
    """Invoke once; return the server's prompt-eval time in ms, or None.

    Goes through ``OllamaProvider.invoke`` rather than raw HTTP so this measures
    the code path ICDEV actually runs, and so ``LLMResponse.prompt_eval_ms`` has a
    consumer — a field nothing reads is indistinguishable from a field never set.
    """
    request = LLMRequest(
        messages=[{"role": "user", "content": question}],
        system_prompt=prefix,
        max_tokens=1,
        temperature=0.0,
    )
    response = provider.invoke(
        request,
        model,
        {"max_output_tokens": 4096, "disable_thinking": True},
    )
    return response.prompt_eval_ms


def measure(
    model: str = DEFAULT_MODEL,
    repeats: int = DEFAULT_REPEATS,
    base_url: str = "",
    paragraphs: int = DEFAULT_PARAGRAPHS,
) -> Dict[str, Any]:
    """Run the cold-vs-warm comparison. Never raises; reports UNMEASURABLE instead."""
    base_url = base_url or os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
    provider = _provider(base_url)
    cap = resolve_prefix_cache_capability(provider)

    result: Dict[str, Any] = {
        "status": STATUS_UNMEASURABLE,
        "model": model,
        "base_url": base_url,
        "repeats": repeats,
        "declared_support": cap.support,
        "declared_reason": cap.reason,
        # Stated up front, not as a footnote: this tool answers a latency
        # question and has no opinion about dollars.
        "unit": "milliseconds of server-side prompt-eval (prefill) time",
        "usd_saved": None,
        "usd_note": (
            "Not applicable. A locally hosted model has no per-token price, so a "
            "reused prefix costs less time and exactly zero less money."
        ),
        "cold_ms": [],
        "warm_ms": [],
        "reason": "",
    }

    if cap.support != PREFIX_CACHE_LOCAL:
        result["reason"] = (
            f"{base_url} does not declare 'local' prefix caching (declared "
            f"{cap.support!r}), so a latency comparison would not be measuring "
            "local KV reuse. This tool is for a locally hosted endpoint."
        )
        return result

    # Unique per run — see cold_nonce(). The WARM prefix is deliberately allowed
    # to persist across runs: a shared prefix the server already holds is exactly
    # what the warm leg is supposed to be.
    nonce = cold_nonce()
    result["cold_nonce"] = nonce
    shared_prefix = build_prefix("SHARED", paragraphs)

    # Step 1 — load the weights. Discarded on purpose (see module docstring).
    try:
        for _ in range(3):
            _one_call(provider, model, "You are terse.", "hi")
    except Exception as exc:  # noqa: BLE001
        result["reason"] = f"Ollama unreachable or model unavailable at {base_url}: {exc}"
        return result

    cold: List[float] = []
    warm: List[float] = []
    try:
        for i in range(repeats):
            # Step 3 — a prefix the server has never seen, in THIS run or any
            # earlier one (the nonce is what makes that true).
            c = _one_call(provider, model, build_prefix(f"COLD-{nonce}-{i}", paragraphs), f"Question {i}.")
            # Step 2 — alternate, so drift hits both legs.
            w = _one_call(provider, model, shared_prefix, f"Question {i}.")
            if c is not None:
                cold.append(round(c, 1))
            if w is not None:
                warm.append(round(w, 1))
    except Exception as exc:  # noqa: BLE001
        result["reason"] = f"measurement aborted after {len(cold)} cold / {len(warm)} warm: {exc}"
        return result

    if not cold or not warm:
        result["reason"] = (
            "Ollama returned no prompt_eval_duration on these calls, so prefill "
            "time cannot be read. Nothing is inferred from its absence."
        )
        return result

    cold_med = statistics.median(cold)
    warm_med = statistics.median(warm)
    result.update({
        "status": STATUS_MEASURED,
        "cold_ms": cold,
        "warm_ms": warm,
        # Step 4 — median, not mean.
        "cold_median_ms": round(cold_med, 1),
        "warm_median_ms": round(warm_med, 1),
        "speedup_x": round(cold_med / warm_med, 1) if warm_med > 0 else None,
        "saved_ms_per_call": round(cold_med - warm_med, 1),
    })
    return result


def _render(r: Dict[str, Any]) -> str:
    lines = [
        "Ollama prefix cache — LATENCY, not dollars (cch-prov-03)",
        f"  endpoint         {r['base_url']}",
        f"  model            {r['model']}",
        f"  declared support {r['declared_support']}",
        "",
    ]
    if r["status"] != STATUS_MEASURED:
        lines += [f"  UNMEASURABLE: {r['reason']}", ""]
        return "\n".join(lines)
    lines += [
        f"  cold prompt-eval (unique prefix)  {r['cold_median_ms']:>8.1f} ms  median of {len(r['cold_ms'])}",
        f"  warm prompt-eval (shared prefix)  {r['warm_median_ms']:>8.1f} ms  median of {len(r['warm_ms'])}",
        f"  saved per call                    {r['saved_ms_per_call']:>8.1f} ms  ({r['speedup_x']}x faster prefill)",
        "",
        f"  USD saved: {r['usd_note']}",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--model", default=DEFAULT_MODEL, help=f"Ollama model tag (default: {DEFAULT_MODEL})")
    ap.add_argument("--repeats", type=int, default=DEFAULT_REPEATS, help="cold/warm pairs to run")
    ap.add_argument("--base-url", default="", help="Ollama endpoint (default: $OLLAMA_BASE_URL)")
    ap.add_argument("--paragraphs", type=int, default=DEFAULT_PARAGRAPHS, help="prefix size, in paragraphs")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    args = ap.parse_args()

    r = measure(
        model=args.model,
        repeats=args.repeats,
        base_url=args.base_url,
        paragraphs=args.paragraphs,
    )
    print(json.dumps(r, indent=2) if args.json else _render(r))
    # Report-only: an unreachable Ollama is not a build failure.
    return 0


if __name__ == "__main__":
    sys.exit(main())

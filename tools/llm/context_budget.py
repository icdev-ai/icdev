#!/usr/bin/env python3
# CUI // SP-CTI
"""Token budgeting for evidence packing — fill the window, never truncate blind.

WHY THIS EXISTS

`DICSearchEngine` retrieves `vector_top_k: 50` candidates, fuses them, and hands
them on. `blueprint._llm_synthesize` then takes `results[:5]` and truncates each
to `max_chars=1000` — roughly 5 KB, about 1.2k tokens, **regardless of the
model**. Claude Sonnet 4.5 (~200k window) sees exactly what an 8k local model
sees, after the retriever already fetched and discarded 45 candidates.

That is the whole "corpus larger than the context window" problem, and it is not
a retrieval-quality problem: the answer is frequently already in the candidate
set and thrown away before the model is asked.

Two things were missing and this module supplies both:

  * A real token account. There is no tokenizer anywhere in DIC — every budget
    is a raw character slice (`[:300]`, `[:900]`, `[:1000]`, `[:6000]`) and the
    only token math in the stack is `chunker.CHARS_PER_TOKEN = 4`. A CJK or
    code-heavy document silently blows every one of them.
  * A per-model window. `args/llm_config.yaml` declares `context_window` for
    exactly ONE model; the rest carry only `max_output_tokens`, which is the
    wrong number (`claude-sonnet-api` says 64000 for a ~200k window). Nothing
    in the platform *can* budget against the real window, so nothing does.

DESIGN NOTES

Lives in `tools/llm/` and NOT in `tools/cortex/`. Cortex depends on the llm
layer, never the reverse, and the callers that most need budgeting include
`router._inject_rag_context` and DIC's no-LLM `_compile_grounded_answer` —
neither can import the governance facade without a cycle or a layering
inversion.

`floor_window_for_function()` takes the MINIMUM across the declared chain,
because the declared chain is not the last word: `two_tier` applies first, RL
re-ranking reorders after, and the CLI bridge prepends `claude-cli` at invoke
time. A budget computed for a 200k model that gets served by `qwen3-local`
(32k) overflows.

Estimation deliberately OVER-counts. Under-estimating tokens overflows a real
request; over-estimating merely packs one fewer chunk.

CLI::

    python tools/llm/context_budget.py --report --json
    python tools/llm/context_budget.py --check-config      # exit 1 on gaps
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent

# Deliberately conservative: ~3.3 chars/token rather than the chunker's 4.
# English prose averages ~4, but code, JSON, CJK and identifier-heavy text run
# far denser. The cost of over-estimating is one fewer chunk; the cost of
# under-estimating is a provider-side overflow the caller cannot recover from.
_CHARS_PER_TOKEN = 3.3

#: Used only when a model declares no `context_window`. Small on purpose — a
#: silent generous default would hide exactly the config gap this module exists
#: to surface. `--check-config` fails while any model relies on it.
DEFAULT_WINDOW_TOKENS = 8192

_WORD_RE = re.compile(r"\S+")


def estimate_tokens(text: str) -> int:
    """Conservative token estimate for ``text``.

    One definition for the whole platform. There are currently four disagreeing
    estimators (`compression/context_compressor.py` words*1.35,
    `context_compressor.py` len//4, `chunker.py`, `memory/history_compressor.py`);
    they should delegate here rather than each guess differently.
    """
    if not text:
        return 0
    by_chars = len(text) / _CHARS_PER_TOKEN
    # A token is never longer than a word, so word count is a hard floor.
    by_words = len(_WORD_RE.findall(text))
    return int(math.ceil(max(by_chars, by_words)))


def _load_llm_config() -> dict:
    try:
        import yaml

        from tools.llm.config_path import resolve_llm_config_path  # noqa: PLC0415

        path = resolve_llm_config_path()
    except Exception:
        path = BASE_DIR / "args" / "llm_config.yaml"
    try:
        import yaml

        return yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    except Exception:
        return {}


def model_windows() -> dict:
    """``{model_name: context_window}`` for every declared model.

    Missing entries are reported as ``None`` rather than defaulted, so callers
    and ``--check-config`` can tell "declared 8192" from "nobody said".
    """
    cfg = _load_llm_config()
    out = {}
    for name, spec in (cfg.get("models") or {}).items():
        if isinstance(spec, dict):
            out[name] = spec.get("context_window")
    return out


def context_window_for(model: str) -> int:
    """Declared window for ``model``, or the conservative default."""
    w = model_windows().get(model)
    return int(w) if w else DEFAULT_WINDOW_TOKENS


def chain_for_function(function: str) -> list:
    """Models that could serve ``function``, declared order first.

    Includes the CLI bridge, which `router` prepends at invoke time — a budget
    that ignores it is computed for a model that never runs.
    """
    cfg = _load_llm_config()
    routing = cfg.get("routing") or {}
    entry = routing.get(function) or routing.get("default") or {}
    chain = list(entry.get("chain") or []) if isinstance(entry, dict) else list(entry or [])
    models = cfg.get("models") or {}
    if "claude-cli" in models and "claude-cli" not in chain:
        chain.append("claude-cli")
    return chain


def floor_window_for_function(function: str) -> int:
    """Smallest window any model that might serve ``function`` could have.

    The declared chain is not the last word — `two_tier` applies before chain
    resolution, RL re-ranking reorders it after, and the CLI bridge is
    prepended at invoke time. Budgeting for the largest member and being served
    by the smallest is an overflow; the minimum is the only safe assumption.
    """
    chain = chain_for_function(function)
    if not chain:
        return DEFAULT_WINDOW_TOKENS
    return min(context_window_for(m) for m in chain)


@dataclass
class EvidencePack:
    """Result of packing evidence into a budget.

    ``dropped`` is the contract: nothing leaves the pack silently. A caller can
    always tell the user "N further sources not shown", which is the difference
    between a budget and a truncation.
    """

    included: list = field(default_factory=list)
    dropped: list = field(default_factory=list)
    tokens_used: int = 0
    budget: int = 0

    @property
    def complete(self) -> bool:
        return not self.dropped

    def to_dict(self) -> dict:
        return {
            "included_count": len(self.included),
            "dropped_count": len(self.dropped),
            "tokens_used": self.tokens_used,
            "budget": self.budget,
            "complete": self.complete,
        }


def available_input_tokens(
    function: str,
    *,
    system_prompt: str = "",
    question: str = "",
    reserved_output: int = 1024,
    safety_margin: float = 0.10,
) -> int:
    """Tokens left for evidence after everything else the request must carry."""
    window = floor_window_for_function(function)
    overhead = estimate_tokens(system_prompt) + estimate_tokens(question)
    usable = window - reserved_output - overhead
    usable = int(usable * (1.0 - safety_margin))
    return max(usable, 0)


def pack_evidence(candidates: list, budget: int, *, render=None, min_items: int = 1) -> EvidencePack:
    """Fill ``budget`` with as many candidates as fit, in order.

    ``render`` maps a candidate to the text that will actually be sent, so the
    accounting matches the payload rather than the raw object.

    ``min_items`` guarantees forward progress: if even the first candidate
    exceeds the budget it is still included, because returning an empty pack
    would silently produce an ungrounded answer — strictly worse than a tight
    one. The overflow is visible in ``tokens_used > budget``.
    """
    render = render or (lambda c: getattr(c, "content", None) or str(c))
    pack = EvidencePack(budget=max(int(budget), 0))
    for cand in candidates or []:
        cost = estimate_tokens(render(cand))
        if pack.included and pack.tokens_used + cost > pack.budget:
            pack.dropped.append(cand)
            continue
        if not pack.included and len(pack.included) < min_items:
            pack.included.append(cand)
            pack.tokens_used += cost
            continue
        pack.included.append(cand)
        pack.tokens_used += cost
    return pack


def routed_models() -> set:
    """Every model reachable through some ``routing:`` chain, plus the CLI bridge.

    Scoping the gap check to these is deliberate. `models:` declares many
    entries no chain references — experimental cloud models, spares. A window
    missing on an unreachable model cannot affect any budget, and failing CI on
    it would train people to add numbers they have not verified, which is worse
    than an honest default.
    """
    cfg = _load_llm_config()
    out = set()
    for entry in (cfg.get("routing") or {}).values():
        chain = entry.get("chain") if isinstance(entry, dict) else entry
        for m in (chain or []):
            out.add(str(m))
    if "claude-cli" in (cfg.get("models") or {}):
        out.add("claude-cli")
    return out


def config_gaps() -> list:
    """Routed models with no declared ``context_window``.

    A conservative default must never become load-bearing: it silently caps a
    200k model at 8k, which is the same "quietly answer from less" failure this
    module exists to remove. This makes that visible for models that can
    actually be selected.
    """
    windows = model_windows()
    return sorted(m for m in routed_models() if not windows.get(m))


def main(argv: list | None = None) -> int:
    ap = argparse.ArgumentParser(description="Context budgeting for evidence packing.")
    ap.add_argument("--report", action="store_true", help="Show declared windows.")
    ap.add_argument("--check-config", action="store_true",
                    help="Exit 1 if any model lacks context_window.")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    gaps = config_gaps()
    windows = model_windows()
    if args.json:
        print(json.dumps({"models": windows, "missing_context_window": gaps,
                          "default_window_tokens": DEFAULT_WINDOW_TOKENS}, indent=2))
    else:
        print(f"models declared: {len(windows)}  missing context_window: {len(gaps)}")
        for name in gaps:
            print(f"   {name}")
    if args.check_config and gaps:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

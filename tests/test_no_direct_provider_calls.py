#!/usr/bin/env python3
"""No module may call an LLM provider's HTTP API directly — CUI // SP-CTI.

Every LLM call must go through `LLMRouter` (or a facade over it, such as
`tools/cortex`). The router is where egress redaction, air-gap model exclusion,
budget and rate caps, provider fallback, and the append-only audit row live. A
module that POSTs to `api.anthropic.com` itself gets none of them.

This was not hypothetical. The PACKAGED copies of three network-canvas route
modules — `icdev/tools/network/routes/{ai,topology,twin_migration}.py` — POSTed
directly to `https://api.anthropic.com/v1/messages` with a raw
`ANTHROPIC_API_KEY`, six call sites in total, while their canonical twins had
zero and routed everything through `_route_llm`. The canonical fix had simply
never been mirrored.

So a wheel-installed deployment sent network-canvas content straight to
Anthropic with no redaction, no air-gap exclusion, no budget cap and no audit
trail — on a canvas whose content is CUI by default.

`docs/reference/agx-degradation-contract.md` already bans vendor-SDK imports in
architecture code; a raw HTTP call to the same endpoint defeats the intent, so
this checks the endpoint rather than the import.
"""
from __future__ import annotations

import pathlib
import re

import pytest

_ROOT = pathlib.Path(__file__).resolve().parents[1]

#: Provider API endpoints that must never be contacted outside the LLM layer.
_PROVIDER_ENDPOINTS = re.compile(
    r"https?://(?:api\.anthropic\.com|api\.openai\.com|generativelanguage\.googleapis\.com)"
)

#: The LLM layer itself legitimately talks to providers. Everything else routes.
_ALLOWED_PREFIXES = (
    "tools/llm/",
    "icdev/tools/llm/",
    # Provider abstractions + their tests/docs live here too.
    "tools/rag/pdf_provider.py",
    "icdev/tools/rag/pdf_provider.py",
)


def _scan(root_name: str) -> list[str]:
    root = _ROOT / root_name
    hits: list[str] = []
    if not root.is_dir():
        return hits
    for p in root.rglob("*.py"):
        if "__pycache__" in p.parts:
            continue
        rel = p.relative_to(_ROOT).as_posix()
        if rel.startswith(_ALLOWED_PREFIXES):
            continue
        try:
            src = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if _PROVIDER_ENDPOINTS.search(src):
            hits.append(rel)
    return hits


@pytest.mark.parametrize("tree", ["tools", "icdev/tools"], ids=["canonical", "packaged"])
def test_no_direct_provider_endpoint_calls(tree: str):
    """BOTH trees. The packaged copy is what a wheel install actually runs."""
    hits = _scan(tree)
    assert not hits, (
        f"module(s) under {tree}/ contact an LLM provider endpoint directly, "
        f"bypassing LLMRouter (redaction, air-gap exclusion, budget caps, "
        f"audit): {hits}"
    )


@pytest.mark.parametrize("mod", ["ai", "topology", "twin_migration"])
def test_network_routes_use_the_router_in_both_trees(mod: str):
    """Regression pin for the three that drifted."""
    for tree in ("tools", "icdev/tools"):
        p = _ROOT / tree / "network" / "routes" / f"{mod}.py"
        assert p.is_file(), f"{tree}/network/routes/{mod}.py missing"
        src = p.read_text(encoding="utf-8", errors="replace")
        assert "_route_llm" in src, f"{tree}/network/routes/{mod}.py does not route via _route_llm"
        assert "api.anthropic.com" not in src, (
            f"{tree}/network/routes/{mod}.py still calls Anthropic directly"
        )


def test_the_guard_can_actually_see_a_violation(tmp_path):
    """Guard the guard — a scanner that matches nothing proves nothing."""
    assert _PROVIDER_ENDPOINTS.search('requests.post("https://api.anthropic.com/v1/messages")')
    assert _PROVIDER_ENDPOINTS.search("url = 'https://api.openai.com/v1/chat/completions'")
    assert not _PROVIDER_ENDPOINTS.search("from tools.llm import get_router")

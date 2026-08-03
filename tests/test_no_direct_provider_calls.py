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

A raw HTTP POST is only the loudest way to leave the governed path. The quiet
one is to ask the router for a provider *object* and then drive it yourself::

    provider, model_id, cfg = router.get_provider_for_function("chat")
    resp = provider.invoke(req, model_id, cfg)     # <-- bypass

`LLMRouter.invoke()` is where the governance lives. `get_provider_for_function()`
only hands back the provider and its config, so a caller that invokes the
provider directly skips the same protections a raw POST skips. This is exactly
the shape of the chat intent-classifier bypass fixed in cxo-adopt-05.

Note the distinction this file has to draw carefully: asking for the provider is
not itself a bypass. Roughly twenty modules call `get_provider_for_function()`
purely to introspect capability — `model_cfg["supports_vision"]`, the resolved
model name — and then correctly call `router.invoke(...)`. Those are compliant
and must not be flagged. The violation is invoking *the returned provider*, so
the scan binds the name on the left of the assignment and looks for a call on
that specific name.

There are still nine such call sites, so this ships as a FROZEN BASELINE rather
than a hard gate: the known set is pinned, and the test fails when a NEW one
appears. Shrink `_PROVIDER_HANDOFF_BASELINE` as call sites migrate to the
governed facade — a pinned entry that no longer violates also fails, so the list
cannot silently rot into fiction.
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

#: Cheap literal gate. The bind regex below backtracks over every assignment in
#: a file, which costs ~40s across a tree this size; only ~30 files contain the
#: call at all, so filter on the substring before paying for the regex.
_PROVIDER_HANDOFF_LITERAL = "get_provider_for_function"

#: `<targets> = ...get_provider_for_function(`. The router returns
#: `(provider, model_id, config)`, so the provider is the FIRST target — group 1
#: is the whole target list and the caller takes the head of it. The target-list
#: class is `[\w, \t]` rather than `[\w,\s]` deliberately: `\s` matches newlines,
#: which lets the lazy quantifier chew backwards through the whole file.
_PROVIDER_HANDOFF_BIND = re.compile(
    r"^[ \t]*(\w[\w, \t]*?)\s*=\s*[\w.]*\bget_provider_for_function\s*\(",
    re.M,
)

#: Methods that actually spend the ungoverned handle.
_PROVIDER_HANDOFF_CALLS = ("invoke", "complete")


def _provider_handoff_vars(src: str) -> set[str]:
    """Names bound from `get_provider_for_function` that are then invoked directly.

    Returning the names (not just a bool) keeps the failure message actionable
    and lets the self-test assert on something more specific than truthiness.
    """
    bypassed: set[str] = set()
    if _PROVIDER_HANDOFF_LITERAL not in src:
        return bypassed
    for m in _PROVIDER_HANDOFF_BIND.finditer(src):
        var = m.group(1).split(",")[0].strip()
        if not var or var == "_":
            # `_, model, _ = ...` — a discarded provider cannot be invoked.
            continue
        call = re.compile(
            r"\b" + re.escape(var) + r"\s*\.\s*(?:" + "|".join(_PROVIDER_HANDOFF_CALLS) + r")\s*\("
        )
        if call.search(src):
            bypassed.add(var)
    return bypassed


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


def _scan_provider_handoff(root_name: str) -> set[str]:
    """Modules that invoke a provider handed back by `get_provider_for_function`."""
    root = _ROOT / root_name
    hits: set[str] = set()
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
        if _provider_handoff_vars(src):
            hits.add(rel)
    return hits


#: FROZEN BASELINE — the nine sites that already bypass the router this way, as
#: of cxo-adopt-06. This list may only ever SHRINK. Migrate a call site to the
#: governed facade (`router.invoke(...)` / `tools.cortex`) and drop its entry in
#: the same PR; never append to buy a green build.
_PROVIDER_HANDOFF_BASELINE = frozenset(
    {
        "tools/foundry/spec_generator.py",
        "tools/gameday/base_agent.py",
        "tools/gameday/judge_agent.py",
        "tools/studio/wne/narrative_generator.py",
        "tools/trading/dashboard/app.py",
        "icdev/tools/foundry/spec_generator.py",
        "icdev/tools/gameday/base_agent.py",
        "icdev/tools/gameday/judge_agent.py",
        "icdev/tools/studio/wne/narrative_generator.py",
    }
)


@pytest.mark.parametrize("tree", ["tools", "icdev/tools"], ids=["canonical", "packaged"])
def test_no_new_direct_provider_handoff(tree: str):
    """No NEW `get_provider_for_function` -> `provider.invoke/complete` site."""
    new = sorted(_scan_provider_handoff(tree) - _PROVIDER_HANDOFF_BASELINE)
    assert not new, (
        f"module(s) under {tree}/ take a provider from "
        f"get_provider_for_function() and invoke it directly, bypassing "
        f"LLMRouter.invoke() (redaction, air-gap exclusion, budget caps, "
        f"audit): {new}. Route through router.invoke(...) or the tools.cortex "
        f"facade. Do NOT add these to _PROVIDER_HANDOFF_BASELINE."
    )


def test_provider_handoff_baseline_has_no_stale_entries():
    """A baseline that outlives the bypass it pins is fiction. Shrink it."""
    live = _scan_provider_handoff("tools") | _scan_provider_handoff("icdev/tools")
    stale = sorted(
        rel
        for rel in _PROVIDER_HANDOFF_BASELINE
        # Only a file that still EXISTS but no longer bypasses is stale; a file
        # absent from a partial checkout says nothing about the call site.
        if rel not in live and (_ROOT / rel).is_file()
    )
    assert not stale, (
        f"these modules no longer hand off a provider directly — remove them "
        f"from _PROVIDER_HANDOFF_BASELINE: {stale}"
    )


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


def test_the_handoff_guard_can_actually_see_a_violation():
    """Same idiom for the second policy — prove it catches the real shape."""
    # The bypass, as written at gameday/base_agent.py and judge_agent.py.
    assert _provider_handoff_vars(
        "        provider, model_id, cfg = router.get_provider_for_function('chat')\n"
        "        resp = provider.invoke(req, model_id, cfg)\n"
    ) == {"provider"}
    # The single-target `.complete()` shape, as at studio/wne/narrative_generator.py.
    assert _provider_handoff_vars(
        "    provider = router.get_provider_for_function('narrative_generation')\n"
        "    text = provider.complete(prompt, max_tokens=1200)\n"
    ) == {"provider"}


def test_the_handoff_guard_does_not_flag_the_governed_path():
    """The ~20 compliant introspection sites must stay green, or the gate is noise.

    Asking the router which model it would pick is not a bypass; invoking the
    returned provider is. If this ever starts failing, the scan has widened into
    `router.invoke(...)` and will bury the real signal.
    """
    # Capability introspection + governed invoke — mbse/diagram_extractor.py.
    assert not _provider_handoff_vars(
        "        provider, model_id, model_cfg = router.get_provider_for_function('x')\n"
        "        if model_cfg.get('supports_vision'):\n"
        "            response = router.invoke('x', request)\n"
    )
    # Discarded provider — document_intelligence/output_generators.py.
    assert not _provider_handoff_vars(
        "        _, model, _ = router.get_provider_for_function(function)\n"
        "        result = router.invoke(function, req)\n"
    )
    # Underscore-prefixed throwaway — network/narrative_generator.py.
    assert not _provider_handoff_vars(
        "        _provider, _model_id, model_cfg = router.get_provider_for_function('tfw')\n"
        "        resp = router.invoke('tfw', req)\n"
    )

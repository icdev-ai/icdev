# CUI // SP-CTI
"""rmf-wp-02 -- proposal prose passes through sanitize_for_llm on EVERY path.

THE CARD SAID `govcon_sanitizer.sanitize_for_llm` was wired only into
response_drafter. Half right. `LLMRouter.invoke` has run `_pre_invoke_redaction`
on every call since D-RDT-1 and `cortex.complete` reaches `invoke`, so the
single-shot drafting paths in rfi_workbench and doc_generator were covered.
What was NOT covered was `invoke_for_role` -- the method ChainOrchestrator
hands every Chain-of-Thought / Chain-of-Debate step to. rfi_workbench's
`_generate_draft` (CoD for the judgment sections) and doc_generator's
`_cot_generate` / `_cod_compress` all go that way, so a debater or a reasoner
received the raw prompt.

#2028 closed that seam while this card was in flight, and its
tests/test_invoke_for_role_redaction.py pins every ROUTER door. What this file
pins is the CONSUMERS the card names: every LLM dispatch in the two modules must
go through a door that redacts (`router.invoke`, `cortex.complete`, or a
ChainOrchestrator entry, which now lands on the gated `invoke_for_role` /
`_invoke_model_direct`), and never around one. A future edit that calls a
provider directly, or reaches `_invoke_model_direct` with a request that
`invoke` already marked, is what this exists to catch.

Exempt from the red-first gate with a written reason (args/red_first_gate.yaml):
it asserts a property main already satisfies, by design.
"""
from __future__ import annotations

import ast
import importlib
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

#: Doors that run the redaction gate themselves (or land on one that does).
_GOVERNED = {
    "invoke",                      # LLMRouter.invoke / invoke_streaming
    "complete",                    # cortex.complete -> router.invoke
    "invoke_chain_of_thought",     # ChainOrchestrator -> invoke_for_role / _invoke_model_direct
    "invoke_chain_of_debate",
    "invoke_council",
}
#: Doors a CONSUMER must never open itself: the first two skip the gate
#: outright, the last two are router-internal and carry no caller-side guard.
_BYPASSES = {"_provider_invoke", "_invoke_chain", "_invoke_model_direct", "invoke_for_role"}


def _dispatches(relpath: str) -> list[str]:
    """Every door NAMED in the module, whether called (`router.invoke(fn, req)`)
    or handed on as a reference (doc_generator submits `router.invoke` to a
    thread pool for its timeout). Counting only calls would miss the second
    form, and a bypass handed to an executor is still a bypass."""
    tree = ast.parse((REPO_ROOT / relpath).read_text(encoding="utf-8"))
    return [
        node.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute) and node.attr in (_GOVERNED | _BYPASSES)
    ]


@pytest.mark.parametrize("relpath", [
    "tools/govcon/rfi_workbench.py",
    "tools/document_intelligence/doc_generator.py",
])
def test_named_modules_dispatch_only_through_redacting_doors(relpath):
    dispatches = _dispatches(relpath)
    assert dispatches, f"{relpath} makes no LLM call at all?"
    bypassed = sorted(set(dispatches) & _BYPASSES)
    assert not bypassed, f"{relpath} steps around the redaction seam via {bypassed}"


def test_the_chain_paths_the_card_names_are_still_wired():
    """The CoD/CoT entry points that used to egress raw must still be what
    the two modules call -- a module that quietly stopped using the
    orchestrator would pass the sweep above while changing the claim."""
    rfi = set(_dispatches("tools/govcon/rfi_workbench.py"))
    dic = set(_dispatches("tools/document_intelligence/doc_generator.py"))
    assert "invoke_chain_of_debate" in rfi and "complete" in rfi and "invoke" in rfi
    assert "invoke_chain_of_thought" in dic and "invoke" in dic


def test_invoke_for_role_source_carries_the_pre_and_post_hooks():
    """Structural belt under #2028's behavioural tests: the pair is in the
    method body, so a refactor that drops one is visible without a router."""
    router_mod = importlib.import_module("tools.llm.router")
    source = Path(router_mod.__file__).read_text(encoding="utf-8")
    fn = next(
        n for n in ast.walk(ast.parse(source))
        if isinstance(n, ast.FunctionDef) and n.name == "invoke_for_role"
    )
    src = ast.get_source_segment(source, fn)
    assert "_pre_invoke_redaction(" in src
    assert "_post_invoke_deanonymize(" in src
    assert "chain_key=role_key" in src, "the local-only skip must be judged on the ROLE chain"

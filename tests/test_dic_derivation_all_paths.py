#!/usr/bin/env python3
"""Every answer-bearing chat path must carry a derivation disclosure. CUI // SP-CTI.

`api_chat` has three answer-bearing exits:

  * Path 1 — high-confidence direct lookup, no LLM;
  * Path 2 — grounded answer compiled from top chunks, no LLM;
  * Path 3 — LLM synthesis.

The disclosure was originally computed at the shared tail, which Paths 2 and 3
reach but Path 1 returns before. A live query against the peering corpus took
Path 1 and came back with no `derivation` field at all.

That is worse than it sounds: a missing disclosure is indistinguishable from a
disclosure that ran and found nothing derived. Path 1 is also the path that most
often returns a near-verbatim extract, so "quoted vs restated" is precisely the
distinction a reader needs there.
"""
from __future__ import annotations

import ast
import inspect

from tools.document_intelligence import blueprint as bp


def _api_chat_source() -> str:
    return inspect.getsource(bp.api_chat)


def _answer_bearing_returns(src: str) -> list:
    """Return-statement nodes in api_chat that carry a non-empty 'answer'.

    The no-results exit returns a fixed "upload documents first" string with no
    sources; there is nothing to disclose about it, so it is excluded.
    """
    tree = ast.parse(inspect.cleandoc(src))
    out = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Return) or node.value is None:
            continue
        seg = ast.dump(node)
        if "'answer'" not in seg and '"answer"' not in seg:
            continue
        if "No relevant documents found" in ast.unparse(node):
            continue
        if "Error:" in ast.unparse(node):
            continue
        out.append(node)
    return out


def test_every_answer_path_returns_a_derivation_field():
    """The regression: Path 1 returned an answer with no disclosure."""
    src = _api_chat_source()
    missing = [
        ast.unparse(n)[:90]
        for n in _answer_bearing_returns(src)
        if "derivation" not in ast.unparse(n)
    ]
    assert not missing, f"answer paths with no derivation disclosure: {missing}"


def test_there_is_more_than_one_answer_path_to_cover():
    """Guards the test itself — if the AST walk finds nothing, it proves nothing."""
    assert len(_answer_bearing_returns(_api_chat_source())) >= 2


def test_direct_lookup_path_is_present_and_instrumented():
    """Pin the specific exit, by its distinguishing condition."""
    src = _api_chat_source()
    assert "_needs_synthesis(message)" in src, "Path 1 guard not found"
    head = src.split("_needs_synthesis(message)")[1].split("# ── Path 2")[0]
    assert "_derivation_disclosure" in head, "Path 1 returns without disclosure"


# --------------------------------------------------------------------------- #
# The disclosure itself still behaves on a single-result input
# --------------------------------------------------------------------------- #


class _R:
    def __init__(self, content, chunk_id="c1"):
        self.content = content
        self.chunk_id = chunk_id


def test_single_result_verbatim_extract_reports_nothing_derived():
    """A Path-1 style quotation must come back clean, not flagged."""
    text = "The contractor shall retain all records for seven years."
    rep = bp._derivation_disclosure(text + " [1]", [_R(text)])
    assert rep is not None
    assert rep["has_derived"] is False


def test_single_result_computed_figure_is_still_caught():
    rep = bp._derivation_disclosure(
        "Total obligation is 45. [1]", [_R("Phase A obligated 20. Phase B obligated 25.")])
    assert rep["counts"]["derived-numeric"] >= 1

#!/usr/bin/env python3
"""DIC chat reports per-claim grounding — CUI // SP-CTI.

`ground_claims` shipped in #864 with tests and **zero call sites**. A primitive
nobody calls changes nothing, so this wires it into the chat path.

Reporting, not gating. A corpus audit found 61% of AI-authored sections carry
no inline citation at all, so blocking chat on unsupported claims would reject
most content for a defect that lives upstream in generation. The verdicts make
that visible; `api_review_approve` is where a gate belongs.

The subtle part is source keying. `_llm_synthesize` instructs the model to cite
`[N]` ordinals, while `citations_json` records chunk ids. Keying the source map
one way only would report every claim as citing an unavailable source — the
grounding would look catastrophic and mean nothing.
"""
from __future__ import annotations

import pytest

from tools.document_intelligence.blueprint import _claim_grounding


class _R:
    """Minimal stand-in for a DIC search result."""

    def __init__(self, content, chunk_id="chunk-abc"):
        self.content = content
        self.chunk_id = chunk_id


RETENTION = (
    "The contractor shall retain all records for a period of seven years "
    "following contract closeout, as required by the retention clause."
)


def test_ordinal_citations_resolve():
    """`[1]` is what the synthesis prompt actually tells the model to emit."""
    rep = _claim_grounding(
        "The contractor shall retain records for seven years [1].", [_R(RETENTION)]
    )
    assert rep is not None
    assert rep["supported"] == 1, rep["claims"]


def test_chunk_id_citations_also_resolve():
    """doc_generator emits `[source: chunk <id>]`; both dialects must key."""
    rep = _claim_grounding(
        "The contractor shall retain records for seven years [source: chunk chunk-abc].",
        [_R(RETENTION, chunk_id="chunk-abc")],
    )
    assert rep["supported"] == 1, rep["claims"]


def test_fabricated_number_is_reported_unsupported():
    """The case the whole layer exists for — and it must NOT be silent."""
    rep = _claim_grounding(
        "The contractor shall retain records for forty-seven years [1].", [_R(RETENTION)]
    )
    assert rep["unsupported"] == 1
    v = rep["claims"][0]
    assert v["method"] == "anchor"
    assert "forty-seven" in v["missing_anchors"]


def test_supported_claim_carries_the_span_that_backs_it():
    """A verdict without its span cannot be rendered — that is the deliverable."""
    rep = _claim_grounding(
        "The contractor shall retain records for seven years [1].", [_R(RETENTION)]
    )
    span = rep["claims"][0]["bound_spans"][0]
    assert "seven years" in span["quote"]
    assert span["start"] < span["end"]


def test_uncited_sentence_is_not_reported_as_unsupported():
    rep = _claim_grounding("Records are retained for some period.", [_R(RETENTION)])
    assert rep["uncited"] == 1
    assert rep["unsupported"] == 0


@pytest.mark.parametrize("results", [[], [_R("")], [_R(None)]])
def test_degrades_to_none_without_usable_sources(results):
    """No sources means no report — never an exception on the answer path."""
    assert _claim_grounding("Anything [1].", results) is None


def test_never_raises_on_malformed_results():
    class Bad:
        @property
        def content(self):
            raise RuntimeError("boom")

    assert _claim_grounding("Claim [1].", [Bad()]) is None


def test_runs_without_an_llm(monkeypatch):
    """Deterministic path — identical behaviour air-gapped."""
    monkeypatch.setenv("ICDEV_NO_LLM", "1")
    rep = _claim_grounding(
        "The contractor shall retain records for seven years [1].", [_R(RETENTION)]
    )
    assert rep["supported"] == 1


def test_chat_route_surfaces_the_summary():
    """Pin the wiring: the primitive existing is not the same as it being used."""
    import inspect

    from tools.document_intelligence import blueprint

    src = inspect.getsource(blueprint.api_chat)
    assert "_claim_grounding(" in src, "api_chat does not compute claim grounding"
    assert "claim_summary" in src, "api_chat does not return the summary"

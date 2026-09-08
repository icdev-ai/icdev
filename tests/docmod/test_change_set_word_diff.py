# CUI // SP-CTI
"""Word-level diff spans, computed server-side (dwr-ws-01).

THE LOAD-BEARING TEST IS THE ROUND TRIP. A diff renderer that silently drops a
character is worse than no diff at all: the reader believes they have seen the
change. So the property asserted over every fixture — and over an adversarial
corpus of unicode, punctuation, blank lines and whitespace runs — is that the
spans reassemble BOTH sides byte for byte::

    equal + delete, in order  ->  before
    equal + insert, in order  ->  after

The rest guards the distinctions the change set exists to keep apart: an
anchored span is not a drafter's before-text, a missing diff is not an empty
one, and an entity nobody has resolved is ``unmeasured`` and never ``ok``.
"""
from __future__ import annotations

import pytest

from tools.document_intelligence import change_set, word_diff

# Adversarial corpus. Every one of these must survive tokenize -> join, and
# every ordered pair of them must survive the span round trip.
CORPUS = [
    "",
    "a",
    "FIPS 140-2",
    "FIPS 140-3 [source: rule:policy-fips-1402]",
    "Systems SHALL use FIPS 140-2 validated cryptographic modules.",
    "Systems SHALL use FIPS 140-3 validated cryptographic modules.",
    "  \t\n  ",
    "line one\n\nline three\r\n",
    "café — naïve; 3.14! ¿qué?",
    "日本語のテキストと English mixed",
    "trailing space ",
    " leading space",
    "a" * 300 + " the " * 120,          # long enough to trip autojunk if it were on
    "punctuation....,,,;;;!!!???",
    "emoji 🚀 and a zero-width​join",
]


# ── The tokenizer ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("text", CORPUS)
def test_tokenize_reassembles_exactly(text):
    """``"".join(tokenize(t)) == t`` — the property the span round trip rests on."""
    assert "".join(word_diff.tokenize(text)) == text


def test_tokenize_none_is_empty_list():
    assert word_diff.tokenize(None) == []


def test_tokenize_keeps_whitespace_runs_as_tokens():
    """Whitespace is a TOKEN, not a separator. A tokenizer that split on it and
    threw it away would render ``a  b`` and ``a b`` as identical and silently
    normalise a document's spacing on the way through the diff."""
    assert word_diff.tokenize("a  b") == ["a", "  ", "b"]
    assert word_diff.tokenize("a b") == ["a", " ", "b"]
    assert word_diff.word_opcodes("a  b", "a b") != []


# ── The round trip ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("before", CORPUS)
@pytest.mark.parametrize("after", CORPUS)
def test_span_round_trip_over_every_pair(before, after):
    """Every ordered pair of the corpus reassembles both sides EXACTLY."""
    spans = word_diff.word_opcodes(before, after)
    assert word_diff.reassemble(spans, "before") == before
    assert word_diff.reassemble(spans, "after") == after
    assert word_diff.round_trip(before, after, spans) is True


def test_reassemble_refuses_an_unknown_side():
    """A typo must not silently return a plausible-looking string."""
    with pytest.raises(ValueError):
        word_diff.reassemble([], "left")


def test_round_trip_is_false_when_a_span_is_dropped():
    """The guard has to be able to FAIL, or it proves nothing."""
    before, after = "alpha beta gamma", "alpha delta gamma"
    spans = word_diff.word_opcodes(before, after)
    mangled = [s for s in spans if s["tag"] != "delete"]
    assert word_diff.round_trip(before, after, mangled) is False


def test_diff_words_withholds_spans_when_the_round_trip_fails(monkeypatch):
    """A lossy diff is not rendered at all. ``spans`` is None -- never a
    truncated best effort -- and the counts go None with it."""
    monkeypatch.setattr(word_diff, "round_trip", lambda *a, **k: False)
    out = word_diff.diff_words("alpha", "beta")
    assert out["spans"] is None
    assert out["round_trip"] is False
    assert out["added_words"] is None and out["removed_words"] is None


# ── The tags and the shape ────────────────────────────────────────────────────

def test_only_the_three_tags_are_emitted():
    """``replace`` is decomposed into delete-then-insert, never emitted."""
    for before in CORPUS:
        for after in CORPUS:
            for span in word_diff.word_opcodes(before, after):
                assert span["tag"] in word_diff.SPAN_TAGS


def test_replace_emits_delete_before_insert():
    spans = word_diff.word_opcodes("use TLS 1.1 here", "use TLS 1.3 here")
    tags = [s["tag"] for s in spans]
    assert tags.index("delete") < tags.index("insert")


def test_no_empty_span_is_emitted():
    for before in CORPUS:
        for after in CORPUS:
            assert all(s["text"] for s in word_diff.word_opcodes(before, after))


def test_equal_run_is_one_span_not_one_per_token():
    """A 50-token unchanged run is ONE equal span — the granularity a reader
    wants, and what keeps the payload small."""
    text = " ".join(f"word{i}" for i in range(50))
    spans = word_diff.word_opcodes(text, text)
    assert spans == [{"tag": "equal", "text": text}]


def test_a_real_change_renders_word_level():
    """The change this whole card exists for. A line differ shows the entire
    sentence deleted and re-inserted; the word differ shows the ONE token."""
    before = "Systems SHALL use FIPS 140-2 validated cryptographic modules."
    after = "Systems SHALL use FIPS 140-3 validated cryptographic modules."
    spans = word_diff.word_opcodes(before, after)
    assert [s["tag"] for s in spans] == ["equal", "delete", "insert", "equal"]
    assert spans[1] == {"tag": "delete", "text": "2"}
    assert spans[2] == {"tag": "insert", "text": "3"}
    assert spans[0]["text"].endswith("FIPS 140-")


def test_counts_are_words_not_tokens():
    """"3 words added" must not become "11" because the phrase carried spaces
    and a full stop."""
    out = word_diff.diff_words("alpha gamma", "alpha beta delta gamma")
    assert out["added_words"] == 2
    assert out["removed_words"] == 0


def test_punctuation_only_change_counts_zero_words_and_is_still_visible():
    """The count is a summary, never the evidence."""
    out = word_diff.diff_words("done.", "done!")
    assert out["added_words"] == 0 and out["removed_words"] == 0
    assert out["changed"] is True
    assert any(s["tag"] in ("insert", "delete") for s in out["spans"])


def test_identical_text_has_no_insert_or_delete():
    out = word_diff.diff_words("unchanged text", "unchanged text")
    assert out["changed"] is False
    assert {s["tag"] for s in out["spans"]} == {"equal"}


# ── Which text is the before side ─────────────────────────────────────────────

def test_appliable_basis_takes_the_anchor_text():
    """An addressable proposal diffs the SPAN the accept path would splice."""
    sug = {"anchor_basis": "exact", "anchor_text": "TLS 1.1",
           "current_content": "the whole section, which is not the span",
           "suggested_content": "TLS 1.3"}
    assert change_set.before_side(sug) == ("TLS 1.1", change_set.BEFORE_ANCHOR)


def test_an_empty_anchor_text_is_a_legitimate_insertion_point():
    """``0:0`` in a section is an exact anchor with an empty span, and it must
    not fall through to ``current_content``."""
    sug = {"anchor_basis": "exact", "anchor_text": "",
           "current_content": "some section body", "suggested_content": "new sentence."}
    assert change_set.before_side(sug) == ("", change_set.BEFORE_ANCHOR)


def test_unanchored_falls_back_to_current_content_and_says_so():
    """The live board's shape: a drafter's before-text with no verified span."""
    sug = {"anchor_basis": "unanchored", "anchor_text": "FIPS 140-2",
           "current_content": "FIPS 140-2", "suggested_content": "FIPS 140-3"}
    assert change_set.before_side(sug) == ("FIPS 140-2", change_set.BEFORE_CURRENT)


def test_no_before_text_at_all_is_none_never_empty_string():
    sug = {"anchor_basis": "unanchored", "current_content": "",
           "suggested_content": "a draft"}
    assert change_set.before_side(sug) == (None, None)


def test_applied_text_outranks_the_draft_on_the_after_side():
    """On edit-then-accept THAT is what shipped; the AI draft did not."""
    sug = {"suggested_content": "the AI draft", "applied_text": "what the human wrote"}
    assert change_set.after_side(sug) == ("what the human wrote", change_set.AFTER_APPLIED)


# ── change_view: reads, never a second opinion ────────────────────────────────

_FINDING = {
    "finding_id": "fnd-001", "entity_label": "FIPS 140-2",
    "currency_verdict": "retired", "severity": "medium", "confidence": 0.7,
}


class _Resolution:
    evidence_health = "degraded"
    state = "superseded"
    verdict = "superseded"
    citation_count = 11
    resolved_at = "2026-09-01T00:00:00+00:00"


def _sug(**over):
    base = {"suggestion_id": "sug_1", "doc_id": "doc_1", "section_id": "sec_1",
            "canvas_source": "doc_modernization", "status": "pending",
            "anchor_basis": "unanchored", "current_content": "FIPS 140-2",
            "suggested_content": "FIPS 140-3 [source: rule:policy-fips-1402]",
            "rationale": "FIPS 140-2 validation sunset."}
    base.update(over)
    return base


def test_change_view_reads_the_finding_row_for_verdict_and_confidence():
    view = change_set.change_view(_sug(), finding=_FINDING, resolution=_Resolution())
    assert view["currency_verdict"] == "retired"
    assert view["currency_verdict_source"] == "docmod_findings"
    assert view["confidence"] == 0.7
    assert view["confidence_source"] == "docmod_findings"
    assert view["entity_label"] == "FIPS 140-2"
    assert view["finding_id"] == "fnd-001"


def test_confidence_band_comes_from_the_shared_classifier():
    """The band is the TRUST gate's own function applied to the STORED score --
    imported, never respelled, so there is one statement of the thresholds."""
    from tools.quality.citation_grounding import classify_confidence
    for score in (0.0, 0.39, 0.4, 0.69, 0.7, 1.0):
        view = change_set.change_view(_sug(), finding=dict(_FINDING, confidence=score))
        assert view["confidence_band"] == classify_confidence(score)


def test_no_finding_reports_none_with_a_none_source_never_a_default_verdict():
    view = change_set.change_view(_sug(), finding=None, resolution=_Resolution())
    assert view["currency_verdict"] is None
    assert view["currency_verdict_source"] is None
    assert view["confidence"] is None and view["confidence_band"] is None
    assert view["confidence_source"] is None


def test_citations_are_the_inline_tags_of_the_text_that_would_ship():
    view = change_set.change_view(_sug(), finding=_FINDING)
    assert view["citations"] == ["rule:policy-fips-1402"]
    assert view["citations_source"] == "inline_tags"


def test_no_citations_reports_an_empty_list_and_a_none_source():
    view = change_set.change_view(_sug(suggested_content="FIPS 140-3"))
    assert view["citations"] == []
    assert view["citations_source"] is None


def test_an_entity_with_no_resolution_reads_unmeasured_never_ok():
    """Four of the ten entity labels carrying a drafted redline on the live
    board have no stored resolution at all. Rendering them healthy would be the
    fabrication docdrift_evidence was written to refuse."""
    view = change_set.change_view(_sug(), finding=_FINDING, resolution=None)
    assert view["evidence_health"] == change_set.HEALTH_UNMEASURED
    assert view["evidence_state"] == "not_resolved"
    assert view["evidence_source"] is None
    assert view["evidence_health"] != "ok"


def test_the_two_currency_derivations_are_kept_apart():
    """The scan's verdict and the resolution's are two answers to one question.
    The change set carries both under their own names and picks no winner."""
    resolution = _Resolution()
    resolution.verdict = "superseded"
    view = change_set.change_view(_sug(), finding=dict(_FINDING, currency_verdict="retired"),
                                  resolution=resolution)
    assert view["currency_verdict"] == "retired"
    assert view["evidence_verdict"] == "superseded"
    assert view["currency_verdict"] != view["evidence_verdict"]


def test_unanchored_change_is_diffed_but_reports_appliable_false():
    """The live board's shape: renderable as a preview, and never presented as
    something the accept route would apply."""
    view = change_set.change_view(_sug(), finding=_FINDING)
    assert view["appliable"] is False
    assert view["before_source"] == change_set.BEFORE_CURRENT
    assert view["spans"] is not None and view["round_trip"] is True
    assert view["anchor_verified"] is None


def test_a_change_with_no_before_text_reports_none_spans_and_a_reason():
    """NEVER an empty span list -- an empty diff reads as "no change"."""
    view = change_set.change_view(_sug(current_content="", anchor_basis="unanchored"))
    assert view["spans"] is None
    assert view["spans_reason"] == "no_before_text"
    assert view["added_words"] is None and view["removed_words"] is None
    assert view["changed"] is None


def test_anchor_verification_runs_only_when_the_section_was_read():
    """``anchor_verified`` is None for "nobody looked" and False for "we looked
    and it no longer holds" -- two different findings."""
    sug = _sug(anchor_basis="exact", anchor_section_id="sec_1",
               anchor_start=4, anchor_end=14, anchor_text="FIPS 140-2",
               current_content="Use FIPS 140-2 today.")
    unread = change_set.change_view(sug)
    assert unread["anchor_verified"] is None and unread["appliable"] is True

    holds = change_set.change_view(sug, section_read=True,
                                   section_content="Use FIPS 140-2 today.")
    assert holds["anchor_verified"] is True and holds["anchor_verify_reason"] is None

    drifted = change_set.change_view(sug, section_read=True,
                                     section_content="Use FIPS 140-3 today.")
    assert drifted["anchor_verified"] is False
    assert drifted["anchor_verify_reason"] == "anchor_stale"


def test_change_view_round_trip_holds_on_the_rendered_payload():
    """The property, asserted where a renderer actually reads it."""
    view = change_set.change_view(_sug(), finding=_FINDING)
    assert word_diff.reassemble(view["spans"], "before") == view["before"]
    assert word_diff.reassemble(view["spans"], "after") == view["after"]


# ── The four run states ───────────────────────────────────────────────────────

def test_run_state_unmeasured_is_never_a_clean_board():
    assert change_set.run_state(False, []) == change_set.STATE_UNMEASURED
    assert change_set.run_state(False, []) != change_set.STATE_NONE


def test_run_state_none_anchored_is_not_no_suggestions():
    """58 proposals, none of them addressable, is a DIFFERENT board from a
    board with nothing proposed -- and an empty change list would read as the
    second."""
    changes = [{"appliable": False, "spans": []}]
    assert change_set.run_state(True, changes) == change_set.STATE_NONE_ANCHORED
    assert change_set.run_state(True, []) == change_set.STATE_NONE


def test_run_state_changes_needs_one_appliable_proposal():
    changes = [{"appliable": False}, {"appliable": True}]
    assert change_set.run_state(True, changes) == change_set.STATE_CHANGES

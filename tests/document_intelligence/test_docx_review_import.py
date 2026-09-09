# CUI // SP-CTI
"""dwr-word-02 — reading a reviewer's Word revisions back in.

WHAT THIS FILE CAN AND CANNOT PROVE. It asserts the parse against OOXML this
repo builds, and it asserts every reconciliation verdict against constructed
bundles and rails. It CANNOT prove that WORD's own serialisation parses — Word
re-writes the package on save, splits runs where it likes, and renumbers
comment ids — and a parser that only ever reads back the XML its sibling wrote
proves nothing about a real round trip.

That half was done by hand and is recorded in
``docs/audits/dwr-word-02-round-trip.md``: a seeded version exported through
the gated ``export_version``, opened in Word 16.0 over COM with track changes
on, edited, saved BY WORD, and reconciled. Both halves are needed. Three real
defects in this module were found by that run and by nothing here, and each of
them now has a test below that fails without the fix:

  * a returned revision compared against a suggestion's own columns rather
    than against the WORD-LEVEL diff the exporter emits — three of three of
    our own redlines came back reported as rivals to themselves;
  * a comment thread searched for roots only — our own author's reply came
    back as a stranger's new remark;
  * an item named as a CONTESTER also counted as ABSENT from the upload.
"""
from __future__ import annotations

import ast
import pathlib
import sys
import zipfile

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.document_intelligence import docx_review_import as ri  # noqa: E402
from tools.document_intelligence import docx_revisions as dr  # noqa: E402

DATE = "2026-09-08T10:00:00+00:00"
S1 = ("All traffic shall use FIPS 140-2 validated cryptography.\n"
      "The enclave boundary is defined in Appendix B.")
S2 = "Accounts are reviewed every 90 days by the ISSO."


# ── fixtures ──────────────────────────────────────────────────────────────────

def _bundle(*sections) -> dict:
    return {
        "version": {"version_id": "v1", "doc_id": "d1", "version_no": 3,
                    "status": "in_review", "tenant_id": None,
                    "classification": "CUI"},
        "doc": {"doc_id": "d1", "title": "Test Doc"},
        "sections": [{"section_id": sid, "heading": heading, "content": content,
                      "citations_json": None, "status": "draft", "origin": "ai"}
                     for sid, heading, content in sections],
    }


def _change(content, anchor, suggested, *, sid="sug-1", status="pending",
            section_id="s1"):
    start = content.index(anchor)
    return {"kind": "change", "id": sid, "section_id": section_id,
            "position": start, "anchor": {"state": "verified", "reason": None},
            "anchor_text": anchor, "author": "docmod redline",
            "created_at": DATE, "status": status,
            "origin_kind": "docmod_redline", "body": "why",
            "suggested_content": suggested, "current_content": anchor,
            "replies": [], "reply_count": 0}


def _comment(content, anchor, body, *, sid="ann-1", replies=(),
             section_id="s1", category="question"):
    start = content.index(anchor)
    return {"kind": "comment", "id": sid, "section_id": section_id,
            "position": start, "anchor": {"state": "verified", "reason": None},
            "anchor_text": anchor, "author": "Reviewer A", "created_at": DATE,
            "status": "open", "category": category, "body": body,
            "replies": list(replies), "reply_count": len(replies),
            "thread_broken": False}


def _rail(*sections, state="items", unplaced=()) -> dict:
    return {"doc_id": "d1", "version_id": "v1", "state": state, "reason": None,
            "sections": [{"section_id": sid, "heading": "", "positioned": list(items),
                          "unpositioned": [], "counts": {}}
                         for sid, items in sections],
            "unplaced": list(unplaced), "counts": {}, "unplaced_count": 0}


def _export(tmp_path, bundle, rail, name="out.docx") -> pathlib.Path:
    """A real dwr-word-01 tracked export — the artifact a reviewer receives."""
    out = tmp_path / name
    dr.build_tracked_docx(bundle, rail, out, title="Test Doc",
                          classification="CUI")
    return out


def _findings(report: dict) -> list[dict]:
    return report["revisions"] + report["comments"]


def _by_reason(report: dict) -> dict:
    return {f["reason"]: f for f in _findings(report)}


def _reconcile(monkeypatch, path, bundle, rail) -> dict:
    from tools.document_intelligence import exporter, review_rail
    monkeypatch.setattr(exporter, "load_version", lambda vid: bundle)
    monkeypatch.setattr(review_rail, "build_rail",
                        lambda doc_id, **kw: rail)
    return ri.reconcile(path, "v1")


# ── the declared dependency ───────────────────────────────────────────────────

def test_python_docx_is_present_rather_than_skipped_around():
    """The FIXTURES need python-docx (they build a real export); the module
    under test needs only the stdlib. A module-level skipif would make this
    file report green having asserted nothing, and would cost a skip-census
    entry against a ceiling that may only go down."""
    assert dr.DOCX_AVAILABLE, (
        "python-docx is declared in requirements.txt but is not importable; "
        "this is a broken environment, not a skippable condition")


# ── the parse: what is in the file ────────────────────────────────────────────

def test_reads_back_the_revisions_dwr_word_01_wrote(tmp_path):
    """The round trip's first half, over this repo's own export."""
    bundle = _bundle(("s1", "Scope", S1))
    rail = _rail(("s1", [_change(S1, "FIPS 140-2", "FIPS 140-3")]))
    parsed = ri.read_revisions(_export(tmp_path, bundle, rail))

    assert parsed["state"] == "parsed"
    assert parsed["counts"]["revisions"] == 1
    groups = [g for p in parsed["paragraphs"] for g in p["groups"]]
    assert len(groups) == 1
    # WORD-level, as the exporter emits it: the digit, not the clause.
    assert (groups[0]["before_text"], groups[0]["after_text"]) == ("2", "3")
    assert groups[0]["authors"] == ["docmod redline"]
    assert groups[0]["kinds"] == ["delete", "insert"]


def test_a_paragraph_folds_to_both_sides_and_the_before_side_is_the_export(tmp_path):
    """``before`` is the paragraph with every revision REJECTED — the text that
    was in the .docx when it left here, and the only space an ICDEV offset
    means anything in."""
    bundle = _bundle(("s1", "Scope", S1))
    rail = _rail(("s1", [_change(S1, "FIPS 140-2", "FIPS 140-3")]))
    parsed = ri.read_revisions(_export(tmp_path, bundle, rail))
    para = parsed["paragraphs"][0]
    assert para["before"] == S1.split("\n")[0]
    assert para["after"] == S1.split("\n")[0].replace("140-2", "140-3")


def test_a_deletion_nested_inside_an_insertion_is_in_neither_side():
    """Word writes text that was inserted and then struck again as a ``w:del``
    INSIDE a ``w:ins``. It was never in the document the reviewer received and
    is not in the document they propose, so it belongs to neither side; a
    non-recursive walk would read it as an ordinary deletion and put text into
    ``before`` that was never there."""
    xml = (
        f'<w:p xmlns:w="{dr.NS_W}">'
        '<w:r><w:t xml:space="preserve">keep </w:t></w:r>'
        '<w:ins w:id="1" w:author="A" w:date="2026-01-01T00:00:00Z">'
        '  <w:r><w:t>new</w:t></w:r>'
        '  <w:del w:id="2" w:author="B" w:date="2026-01-01T00:00:00Z">'
        '    <w:r><w:delText>typo</w:delText></w:r>'
        '  </w:del>'
        '</w:ins>'
        '</w:p>')
    folded = ri.fold_tokens(ri.token_stream(ri.parse_xml(xml.encode(), "p")))
    assert folded["before"] == "keep "
    assert folded["after"] == "keep new"
    assert "ins_del" in folded["groups"][0]["kinds"]


def test_adjacent_delete_and_insert_are_ONE_group():
    """Word writes a replacement as ``w:del`` then ``w:ins``. Reporting those
    as two unrelated revisions asks a human to adjudicate half a sentence
    twice."""
    xml = (
        f'<w:p xmlns:w="{dr.NS_W}">'
        '<w:r><w:t xml:space="preserve">a </w:t></w:r>'
        '<w:del w:id="1" w:author="A" w:date="d"><w:r><w:delText>old</w:delText></w:r></w:del>'
        '<w:ins w:id="2" w:author="A" w:date="d"><w:r><w:t>new</w:t></w:r></w:ins>'
        '<w:r><w:t xml:space="preserve"> b</w:t></w:r>'
        '</w:p>')
    folded = ri.fold_tokens(ri.token_stream(ri.parse_xml(xml.encode(), "p")))
    assert len(folded["groups"]) == 1
    group = folded["groups"][0]
    assert (group["before_text"], group["after_text"]) == ("old", "new")
    assert (group["before_start"], group["before_end"]) == (2, 5)
    assert folded["before"] == "a old b"
    assert folded["after"] == "a new b"


def test_a_paragraph_mark_revision_is_seen():
    """A deleted paragraph mark merges two paragraphs. ICDEV anchors offsets
    INSIDE one section's content and has no representation for it, so it is
    reported rather than ignored — dropping it loses a reviewer's edit."""
    xml = (f'<w:p xmlns:w="{dr.NS_W}"><w:pPr><w:rPr>'
           '<w:del w:id="9" w:author="R" w:date="d"/></w:rPr></w:pPr>'
           '<w:r><w:t>text</w:t></w:r></w:p>')
    mark = ri.paragraph_mark_revision(ri.parse_xml(xml.encode(), "p"))
    assert mark == {"kind": "delete", "author": "R", "date": "d"}


def test_comment_threads_come_back_with_their_parentage(tmp_path):
    """Parentage is in ``commentsExtended.xml``, joined on ``w14:paraId`` and
    NOT on comment id — the way dwr-word-01 emits it. Without it every reply
    reads as its own top-level remark."""
    bundle = _bundle(("s1", "Scope", S1))
    rail = _rail(("s1", [_comment(S1, "Appendix B", "Which one?",
                                  replies=[{"ann_id": "ann-2", "author": "Auth",
                                            "comment": "B.", "created_at": DATE}])]))
    parsed = ri.read_revisions(_export(tmp_path, bundle, rail))
    comments = parsed["comments"]
    assert len(comments) == 2
    root = [c for c in comments.values() if c["parent_id"] is None][0]
    reply = [c for c in comments.values() if c["parent_id"] is not None][0]
    assert reply["parent_id"] == root["comment_id"]
    assert reply["body"] == "B."
    ranges = [r for p in parsed["paragraphs"] for r in p["comment_ranges"]]
    assert ranges and ranges[0]["spans_paragraphs"] is False


def test_a_document_with_no_revisions_reports_MEASURED_zeroes(tmp_path):
    """A measured zero and an unreadable file must never be spelled the same."""
    bundle = _bundle(("s1", "Scope", S1))
    parsed = ri.read_revisions(_export(tmp_path, bundle, _rail(("s1", []))))
    assert parsed["state"] == "parsed"
    assert parsed["counts"]["revisions"] == 0
    assert parsed["counts"]["comments"] == 0


def test_an_unreadable_file_reports_None_counts_and_a_reason(tmp_path):
    bad = tmp_path / "not-a-docx.docx"
    bad.write_bytes(b"this is not a zip")
    parsed = ri.read_revisions(bad)
    assert parsed["state"] == "unreadable"
    assert parsed["reason"]
    assert all(v is None for v in parsed["counts"].values())


def test_a_declared_DOCTYPE_is_refused_unparsed():
    """Entity expansion. No legitimate OOXML part carries a DTD, so refusing
    the declaration costs nothing real and removes the surface entirely."""
    payload = (b'<?xml version="1.0"?><!DOCTYPE lolz [<!ENTITY a "aaaa">]>'
               b'<w:p xmlns:w="' + dr.NS_W.encode() + b'"/>')
    with pytest.raises(ri.DocxReadError, match="DOCTYPE"):
        ri.parse_xml(payload, "word/document.xml")


def test_an_oversized_part_is_refused_on_its_DECLARED_size(tmp_path, monkeypatch):
    """Refused BEFORE decompression — checking afterwards would already have
    paid the cost the cap exists to refuse."""
    bomb = tmp_path / "bomb.docx"
    with zipfile.ZipFile(bomb, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(ri.PART_DOCUMENT, b"A" * 200_000)
    monkeypatch.setenv(ri.MAX_PART_BYTES_ENV, "1024")
    parsed = ri.read_revisions(bomb)
    assert parsed["state"] == "unreadable"
    assert "cap" in parsed["reason"]
    assert parsed["counts"]["revisions"] is None


def test_a_truncated_parse_SAYS_it_is_short(tmp_path, monkeypatch):
    bundle = _bundle(("s1", "Scope", S1))
    rail = _rail(("s1", [_change(S1, "FIPS 140-2", "FIPS 140-3"),
                         _change(S1, "Appendix B", "Annex B", sid="sug-2")]))
    path = _export(tmp_path, bundle, rail)
    monkeypatch.setenv(ri.MAX_REVISIONS_ENV, "1")
    parsed = ri.read_revisions(path)
    assert parsed["truncated"] is True
    assert parsed["limits"]["max_revisions"] == 1


# ── locating ──────────────────────────────────────────────────────────────────

def test_a_paragraph_matching_TWICE_does_not_locate():
    """An ambiguous match is never resolved by picking one — ``resolve_anchor``'s
    rule, and the reason a rail never shows a fabricated position."""
    bundle = _bundle(("s1", "A", "same line\nother"), ("s2", "B", "same line"))
    verdict = ri.locate_paragraph("same line", ri.section_paragraphs(bundle),
                                  bundle)
    assert verdict["state"] == "ambiguous"
    assert verdict["paragraph"] is None


def test_an_empty_paragraph_is_ambiguous_not_bound_to_the_first_blank_line():
    bundle = _bundle(("s1", "A", "one\n\ntwo\n\nthree"))
    verdict = ri.locate_paragraph("", ri.section_paragraphs(bundle), bundle)
    assert verdict["state"] == "ambiguous"
    assert verdict["reason"] == "empty_paragraph_text"


def test_a_heading_is_its_own_column_and_says_so():
    bundle = _bundle(("s1", "Cryptography", S1))
    verdict = ri.locate_paragraph("Cryptography", ri.section_paragraphs(bundle),
                                  bundle)
    assert verdict["state"] == "heading"
    assert verdict["section_id"] == "s1"


def test_a_zero_length_span_inside_another_span_overlaps_it():
    """Half-open overlap is False for every zero-length interval, which would
    make a pure insertion inside a proposed replacement read as uncontested."""
    assert ri.spans_overlap(5, 5, 0, 10) is True
    assert ri.spans_overlap(0, 10, 5, 5) is True
    # Touching endpoints are not an overlap.
    assert ri.spans_overlap(0, 5, 5, 10) is False
    assert ri.spans_overlap(5, 5, 5, 10) is False


# ── the three verdicts ────────────────────────────────────────────────────────

def test_our_own_exported_redline_comes_back_MATCHED_not_as_its_own_rival(tmp_path, monkeypatch):
    """THE DEFECT THE LIVE ROUND TRIP FOUND. The exporter emits a WORD-level
    diff, so ``FIPS 140-2 -> FIPS 140-3`` returns as a deletion of ``2`` and an
    insertion of ``3``. Comparing that against the suggestion's own columns
    matches nothing, and all three of our redlines reported as
    ``competing_proposal`` against themselves."""
    bundle = _bundle(("s1", "Scope", S1))
    change = _change(S1, "FIPS 140-2", "FIPS 140-3")
    rail = _rail(("s1", [change]))
    report = _reconcile(monkeypatch, _export(tmp_path, bundle, rail), bundle, rail)

    assert report["counts"]["matched"] == 1
    assert report["counts"]["conflicting"] == 0
    finding = report["revisions"][0]
    assert finding["verdict"] == "matched"
    assert finding["reason"] == "own_proposal_unchanged"
    assert finding["origin"] == "own_proposal"
    assert finding["icdev_item_id"] == "sug-1"
    # And it is never listed as its own contester.
    assert finding["contested_by"] == []


def test_expected_groups_are_re_derived_through_the_exporters_own_diff():
    change = _change(S1, "FIPS 140-2", "FIPS 140-3")
    groups = ri.expected_revision_groups(change)
    assert [(g["before_text"], g["after_text"]) for g in groups] == [("2", "3")]
    start = S1.index("FIPS 140-2")
    assert groups[0]["start"] == start + len("FIPS 140-")
    # A diff that cannot round-trip is DEFERRED by the exporter, so there is
    # nothing to expect back — never a best-effort guess.
    assert ri.expected_revision_groups({"position": None}) is None


def test_a_reviewer_edit_on_an_uncontested_span_is_MATCHED(monkeypatch, tmp_path):
    bundle = _bundle(("s1", "Scope", S1))
    export_rail = _rail(("s1", []))
    path = _export(tmp_path, bundle, export_rail)
    # The reviewer's own edit, over prose ICDEV proposes nothing about.
    parsed = _hand_edit(path, "Appendix B", "Annex D", author="Reviewer B")
    report = _reconcile_parsed(monkeypatch, parsed, bundle, export_rail)
    finding = report["revisions"][0]
    assert finding["verdict"] == "matched"
    assert finding["origin"] == "reviewer_edit"
    assert finding["section_id"] == "s1"
    assert S1[finding["anchor_start"]:finding["anchor_end"]] == "Appendix B"


def test_a_rival_pending_redline_over_the_same_span_CONFLICTS(monkeypatch, tmp_path):
    """The card's case: both sides changed one span. Surfaced with BOTH
    proposals carried whole, never merged and never won by one side."""
    bundle = _bundle(("s1", "Scope", S1))
    path = _export(tmp_path, bundle, _rail(("s1", [])))
    parsed = _hand_edit(path, "Appendix B", "Annex D", author="Reviewer B")
    # Drafted AFTER the export, over the same words.
    rival = _change(S1, "Appendix B", "Annex B", sid="sug-annex")
    report = _reconcile_parsed(monkeypatch, parsed, bundle, _rail(("s1", [rival])))

    finding = report["revisions"][0]
    assert finding["verdict"] == "conflicting"
    assert finding["reason"] == "competing_proposal"
    assert finding["icdev_item_id"] == "sug-annex"
    # BOTH sides, whole.
    assert finding["after_text"] == "Annex D"
    assert finding["contested_by"] == []
    assert report["counts"]["matched"] == 0


def test_a_decision_taken_while_the_reviewer_edited_CONFLICTS(tmp_path, monkeypatch):
    bundle = _bundle(("s1", "Scope", S1))
    pending = _change(S1, "FIPS 140-2", "FIPS 140-3")
    path = _export(tmp_path, bundle, _rail(("s1", [pending])))
    decided = {**pending, "status": "rejected"}
    report = _reconcile(monkeypatch, path, bundle, _rail(("s1", [decided])))

    finding = report["revisions"][0]
    assert finding["verdict"] == "conflicting"
    assert finding["reason"] == "decided_since_export"
    assert finding["icdev_item_id"] == "sug-1"


def test_a_paragraph_that_moved_under_the_reviewer_CONFLICTS(tmp_path, monkeypatch):
    """It located by SPAN and not by PARAGRAPH: both sides changed it, and
    reporting a clean match would hand a human a merge nobody measured."""
    bundle = _bundle(("s1", "Scope", S1))
    path = _export(tmp_path, bundle, _rail(("s1", [])))
    parsed = _hand_edit(path, "Appendix B", "Annex D", author="Reviewer B")
    # ICDEV rewrote the paragraph around the span while the reviewer read it.
    moved = _bundle(("s1", "Scope",
                     S1.replace("defined in Appendix B.",
                                "defined in Appendix B of the SSP.")))
    report = _reconcile_parsed(monkeypatch, parsed, moved, _rail(("s1", [])))

    finding = report["revisions"][0]
    assert finding["verdict"] == "conflicting"
    assert finding["reason"] == "base_paragraph_changed"
    assert finding["section_id"] == "s1"


def test_an_edit_in_prose_no_section_holds_is_UNMATCHED_BY_NAME(tmp_path, monkeypatch):
    """Never dropped. A dropped revision is a reviewer's edit that silently
    ceased to exist, which is worse than one nobody could place."""
    bundle = _bundle(("s1", "Scope", S1))
    path = _export(tmp_path, bundle, _rail(("s1", [])))
    # The appendix dwr-word-01 always writes is not a section of the version.
    parsed = _hand_edit(path, "Every recorded change", "Every logged change",
                        author="Reviewer B")
    report = _reconcile_parsed(monkeypatch, parsed, bundle, _rail(("s1", [])))

    finding = report["revisions"][0]
    assert finding["verdict"] == "unmatched"
    assert finding["reason"] == "paragraph_not_found"
    assert finding["before_text"] == "Every recorded change"
    assert finding["after_text"] == "Every logged change"


def test_a_paragraph_mark_revision_is_unmatched_and_says_why(tmp_path, monkeypatch):
    bundle = _bundle(("s1", "Scope", S1))
    parsed = {"state": "parsed", "comments": {}, "truncated": False,
              "limits": ri.limits(),
              "counts": {"revisions": 0, "comments": 0, "paragraph_marks": 1,
                         "paragraphs": 1},
              "path": "x",
              "paragraphs": [{"index": 0, "before": S1.split("\n")[0],
                              "after": S1.split("\n")[0], "groups": [],
                              "comment_ranges": [],
                              "paragraph_mark": {"kind": "delete",
                                                 "author": "R", "date": "d"}}]}
    report = _reconcile_parsed(monkeypatch, parsed, bundle, _rail(("s1", [])))
    finding = report["revisions"][0]
    assert finding["verdict"] == "unmatched"
    assert finding["reason"] == "paragraph_mark_revision"
    assert finding["section_id"] == "s1"   # located, and still not anchorable


# ── comments ──────────────────────────────────────────────────────────────────

def test_our_own_REPLY_comes_back_as_ours_and_not_as_a_strangers_remark(tmp_path, monkeypatch):
    """THE SECOND DEFECT THE LIVE ROUND TRIP FOUND. dwr-word-01 writes a thread
    as a root plus one ``w:comment`` per reply over ONE range; a search of
    roots only returned our own author's words as a new reviewer remark."""
    bundle = _bundle(("s1", "Scope", S1))
    thread = _comment(S1, "Appendix B", "Which one?",
                      replies=[{"ann_id": "ann-2", "author": "Auth",
                                "comment": "B, renumbered.", "created_at": DATE}])
    rail = _rail(("s1", [thread]))
    report = _reconcile(monkeypatch, _export(tmp_path, bundle, rail), bundle, rail)

    assert [c["reason"] for c in report["comments"]] == \
        ["own_comment_unchanged", "own_comment_unchanged"]
    assert {c["icdev_item_id"] for c in report["comments"]} == {"ann-1", "ann-2"}
    assert [c["is_reply"] for c in report["comments"]] == [False, True]


def test_a_comment_is_never_conflicting_and_carries_the_contest_as_context(tmp_path, monkeypatch):
    """A comment changes no text, so there is nothing for the ICDEV side to
    disagree with. A reviewer asking about a proposed redline is the NORMAL
    case; flagging it would bury the real findings."""
    bundle = _bundle(("s1", "Scope", S1))
    thread = _comment(S1, "FIPS 140-2", "Is 140-3 approved?")
    rival = _change(S1, "FIPS 140-2", "FIPS 140-3", sid="sug-1")
    rail = _rail(("s1", [thread, rival]))
    report = _reconcile(monkeypatch, _export(tmp_path, bundle, rail), bundle, rail)

    comment = report["comments"][0]
    assert comment["verdict"] == "matched"
    assert [c["id"] for c in comment["contested_by"]] == ["sug-1"]
    assert all(c["kind"] == "change" for c in comment["contested_by"])


# ── absence ───────────────────────────────────────────────────────────────────

def test_an_item_missing_from_the_upload_is_an_ABSENCE_and_never_a_decision(tmp_path, monkeypatch):
    bundle = _bundle(("s1", "Scope", S1))
    path = _export(tmp_path, bundle, _rail(("s1", [])))
    # Drafted after the export, so it CANNOT be in the file.
    later = _change(S1, "Appendix B", "Annex B", sid="sug-late")
    report = _reconcile(monkeypatch, path, bundle, _rail(("s1", [later])))

    assert [row["id"] for row in report["absent_from_upload"]] == ["sug-late"]
    assert report["absent_from_upload"][0]["status"] == "pending"
    note = report["absent_from_upload"][0]["note"].lower()
    assert "not an accept" in note and "not a reject" in note
    # No verdict anywhere in the payload says the reviewer decided anything.
    assert not any(word in repr(report["absent_from_upload"]).lower()
                   for word in ("accepted", "rejected", "applied"))


def test_a_contested_item_is_not_ALSO_reported_absent(tmp_path, monkeypatch):
    """THE THIRD DEFECT THE LIVE ROUND TRIP FOUND. ``base_paragraph_changed``
    deliberately records no ``icdev_item_id``, so an item named only as a
    CONTESTER read as absent as well — telling a reader the same proposal both
    came back and did not."""
    bundle = _bundle(("s1", "Scope", S1))
    change = _change(S1, "FIPS 140-2", "FIPS 140-3")
    path = _export(tmp_path, bundle, _rail(("s1", [change])))
    moved = _bundle(("s1", "Scope", S1.replace("All traffic shall use",
                                               "All enclave traffic shall use")))
    moved_change = _change(moved["sections"][0]["content"], "FIPS 140-2",
                           "FIPS 140-3")
    report = _reconcile(monkeypatch, path, moved, _rail(("s1", [moved_change])))

    assert report["revisions"][0]["reason"] == "base_paragraph_changed"
    assert [c["id"] for c in report["revisions"][0]["contested_by"]] == ["sug-1"]
    assert report["absent_from_upload"] == []


def test_an_unpositioned_item_is_not_reported_absent(tmp_path, monkeypatch):
    """"Did it come back" cannot be asked of an item with no span. Counting
    those would report every unanchored proposal on the board — 58 of 58 on
    this deployment — as missing from every upload."""
    bundle = _bundle(("s1", "Scope", S1))
    path = _export(tmp_path, bundle, _rail(("s1", [])))
    loose = {**_change(S1, "FIPS 140-2", "FIPS 140-3", sid="sug-loose"),
             "position": None,
             "anchor": {"state": "unanchored", "reason": "no_anchor_recorded"}}
    rail = _rail(("s1", []))
    rail["sections"][0]["unpositioned"] = [loose]
    report = _reconcile(monkeypatch, path, bundle, rail)
    assert report["absent_from_upload"] == []


# ── unmeasurable ──────────────────────────────────────────────────────────────

def test_an_unmeasurable_rail_makes_the_WHOLE_report_unmeasurable(tmp_path, monkeypatch):
    """``matched`` asserts "nothing contests this span", which is a claim about
    the rail. A reconciliation reporting "0 conflicts" for a rail nobody could
    read is indistinguishable from a clean round trip."""
    bundle = _bundle(("s1", "Scope", S1))
    rail = _rail(("s1", [_change(S1, "FIPS 140-2", "FIPS 140-3")]))
    path = _export(tmp_path, bundle, rail)
    dead = {"doc_id": "d1", "version_id": "v1", "state": "unmeasurable",
            "reason": "sections_unreadable", "sections": [], "unplaced": [],
            "counts": {}, "unplaced_count": None}
    report = _reconcile(monkeypatch, path, bundle, dead)

    assert report["state"] == "unmeasurable"
    assert "sections_unreadable" in report["reason"]
    assert all(v is None for v in report["counts"].values())


def test_an_unknown_version_is_unmeasurable_not_empty(tmp_path, monkeypatch):
    from tools.document_intelligence import exporter
    bundle = _bundle(("s1", "Scope", S1))
    path = _export(tmp_path, bundle, _rail(("s1", [])))
    monkeypatch.setattr(exporter, "load_version", lambda vid: None)
    report = ri.reconcile(path, "nope")
    assert report["state"] == "unmeasurable"
    assert report["reason"] == "version_not_found"
    assert report["counts"]["matched"] is None


def test_an_unreadable_docx_is_unmeasurable_and_carries_its_parse(tmp_path):
    bad = tmp_path / "bad.docx"
    bad.write_bytes(b"nope")
    report = ri.reconcile(bad, "v1")
    assert report["state"] == "unmeasurable"
    assert report["reason"].startswith("docx_unreadable")
    assert report["parse"]["state"] == "unreadable"


# ── structural: the module cannot become an actuator ──────────────────────────

def _module_ast() -> ast.Module:
    path = REPO_ROOT / "tools" / "document_intelligence" / "docx_review_import.py"
    return ast.parse(path.read_text(encoding="utf-8"))


def test_the_module_writes_NOTHING():
    """A ``matched`` verdict says WHERE a revision goes, never that it may go
    there. The failure mode is a later edit threading a "just apply the matched
    ones" flag through, and a behavioural test over today's callers would still
    pass the day it happens — so the source is read instead."""
    tree = _module_ast()
    banned_calls = {"create_suggestion", "decide_suggestion", "record_application",
                    "supersede_suggestion", "create_annotation",
                    "update_annotation", "resolve_thread", "delete_thread",
                    "commit", "executemany"}
    # DOCSTRINGS ARE EXCLUDED, and the reason is the dwr-ev-03 trap: this
    # module's docstring says in words that it writes nothing, and a naive
    # scan flags that explanation of itself. Prose is not an execution path.
    docstrings = {
        node.body[0].value for node in ast.walk(tree)
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef,
                             ast.ClassDef))
        and node.body and isinstance(node.body[0], ast.Expr)
        and isinstance(node.body[0].value, ast.Constant)
        and isinstance(node.body[0].value.value, str)
    }
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            name = (node.func.attr if isinstance(node.func, ast.Attribute)
                    else getattr(node.func, "id", ""))
            assert name not in banned_calls, f"{name} is a writer"
        if (isinstance(node, ast.Constant) and isinstance(node.value, str)
                and node not in docstrings):
            upper = node.value.upper()
            for verb in ("INSERT INTO", "UPDATE ", "DELETE FROM"):
                assert verb not in upper, f"SQL write literal: {node.value[:60]!r}"


def test_the_paragraph_partition_is_dwr_word_01s_and_not_a_second_copy():
    """Two partitions of one section — one to write the .docx and one to read
    it — is how an offset comes to mean two different things."""
    tree = _module_ast()
    imported = {alias.name for node in ast.walk(tree)
                if isinstance(node, ast.ImportFrom)
                and node.module == "tools.document_intelligence.docx_revisions"
                for alias in node.names}
    assert "paragraph_spans" in imported
    assert ri.paragraph_spans is dr.paragraph_spans
    defined = {n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
    assert "paragraph_spans" not in defined


def test_every_reason_emitted_is_in_a_DECLARED_closed_set():
    """A consumer grouping by reason must not find a bucket per count. Every
    literal reason handed to ``_finding`` is checked against the tuple its
    verdict declares."""
    declared = {
        "matched": {"own_proposal_unchanged", "reviewer_edit",
                    "own_comment_unchanged", "reviewer_comment"},
        "unmatched": set(ri.UNMATCHED_REASONS),
        "conflicting": set(ri.CONFLICT_REASONS),
    }
    seen: dict[str, set[str]] = {v: set() for v in ri.VERDICTS}
    for node in ast.walk(_module_ast()):
        if not (isinstance(node, ast.Call)
                and getattr(node.func, "id", "") == "_finding"):
            continue
        if len(node.args) < 2:
            continue
        verdict, reason = node.args[0], node.args[1]
        if not (isinstance(verdict, ast.Constant) and isinstance(reason, ast.Constant)):
            continue   # the computed one, covered by _locate_failure below
        assert verdict.value in ri.VERDICTS
        assert reason.value in declared[verdict.value], reason.value
        seen[verdict.value].add(reason.value)
    assert seen["conflicting"] == declared["conflicting"], \
        "every declared conflict reason must be reachable"


def test_every_origin_emitted_is_DECLARED():
    """``ORIGINS`` is the closed set that says WHAT a matched item is. A
    constant nothing checks is the declared-but-unconsumed defect in
    miniature."""
    tree = _module_ast()
    values = {node.value.value for node in ast.walk(tree)
              if isinstance(node, ast.keyword) and node.arg == "origin"
              and isinstance(node.value, ast.Constant)}
    # The verdicts set it inside a dict literal (``{**base, "origin": ...}``),
    # so a keyword-only scan finds the ``None``s and none of the real ones.
    for node in ast.walk(tree):
        if not isinstance(node, ast.Dict):
            continue
        for key, value in zip(node.keys, node.values):
            if (isinstance(key, ast.Constant) and key.value == "origin"
                    and isinstance(value, ast.Constant)):
                values.add(value.value)
    # None is legitimate and is not an origin: an item that could not be
    # located has no origin to have, and giving it one would be the guess this
    # whole module refuses.
    assert values - {None}, "no origin is set anywhere"
    assert values <= set(ri.ORIGINS) | {None}, values - set(ri.ORIGINS) - {None}


def test_locate_failure_only_ever_returns_declared_reasons():
    for state, reason in (("ambiguous", "empty_paragraph_text"),
                          ("ambiguous", "paragraph_ambiguous:3"),
                          ("not_found", "paragraph_not_found")):
        assert ri._locate_failure({"state": state, "reason": reason}) \
            in ri.UNMATCHED_REASONS


# ── helpers that edit a built .docx the way a reviewer would ──────────────────

def _hand_edit(path: pathlib.Path, needle: str, replacement: str, *,
               author: str) -> dict:
    """Turn a plain run into a tracked replacement, in the built package.

    A STAND-IN for Word, and it is only ever used to construct INPUT — the
    module under test still does the parsing. The real thing (Word 16.0 over
    COM, saving the package itself) is recorded in
    ``docs/audits/dwr-word-02-round-trip.md``, because a runner has no Word.
    """
    import re
    import shutil
    with zipfile.ZipFile(path) as archive:
        parts = {name: archive.read(name) for name in archive.namelist()}
    document = parts[ri.PART_DOCUMENT].decode("utf-8")

    # Split the plain run that CONTAINS the needle into head / del+ins / tail.
    # ``xml:space`` is optional: docx_revisions sets it on the runs it builds,
    # python-docx's ``add_paragraph`` (which writes the appendix) does not.
    pattern = re.compile(
        r'<w:r><w:t(?: xml:space="preserve")?>([^<]*?' + re.escape(needle)
        + r'[^<]*?)</w:t></w:r>')
    match = pattern.search(document)
    assert match, f"{needle!r} is in no plain run of the export"
    whole = match.group(1)
    head, tail = whole.split(needle, 1)

    def run(text: str) -> str:
        return (f'<w:r><w:t xml:space="preserve">{text}</w:t></w:r>'
                if text else "")

    tracked = (
        run(head)
        + f'<w:del w:id="900" w:author="{author}" w:date="2026-09-08T14:00:00Z">'
          f'<w:r><w:delText xml:space="preserve">{needle}</w:delText></w:r></w:del>'
          f'<w:ins w:id="901" w:author="{author}" w:date="2026-09-08T14:00:00Z">'
          f'<w:r><w:t xml:space="preserve">{replacement}</w:t></w:r></w:ins>'
        + run(tail))
    parts[ri.PART_DOCUMENT] = (
        document[:match.start()] + tracked + document[match.end():]
    ).encode("utf-8")

    edited = path.with_name(path.stem + "-reviewed.docx")
    shutil.copyfile(path, edited)
    with zipfile.ZipFile(edited, "w", zipfile.ZIP_DEFLATED) as out:
        for name, blob in parts.items():
            out.writestr(name, blob)
    return ri.read_revisions(edited)


def _reconcile_parsed(monkeypatch, parsed: dict, bundle: dict,
                      rail: dict) -> dict:
    from tools.document_intelligence import exporter, review_rail
    monkeypatch.setattr(exporter, "load_version", lambda vid: bundle)
    monkeypatch.setattr(review_rail, "build_rail", lambda doc_id, **kw: rail)
    return ri.reconcile(parsed["path"], "v1", parsed=parsed)

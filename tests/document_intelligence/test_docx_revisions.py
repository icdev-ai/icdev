# CUI // SP-CTI
"""dwr-word-01 — the tracked-revision .docx.

WHAT THIS FILE CAN AND CANNOT PROVE. It asserts the OOXML: that a pending
redline becomes a ``w:ins``/``w:del`` pair whose deleted run uses ``w:delText``,
that a comment thread becomes ``comments.xml`` plus a ``commentsExtended.xml``
linking the reply to its root, and — the point of the whole card series — that
an item WITHOUT a verified anchor is neither placed nor dropped.

It CANNOT prove Word opens the file. A document Word silently repairs passes
every assertion here, which is exactly how the first build of this module
shipped a package that was well-formed and rejected outright. That half is
``tools/document_intelligence/docx_word_probe.py``, which drives the real
application over COM and is deliberately not run in CI (no Word on a runner).
"""
from __future__ import annotations

import pathlib
import re
import sys
import zipfile

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.document_intelligence import docx_revisions as dr  # noqa: E402

W = dr.NS_W
DATE = "2026-09-08T10:00:00+00:00"


# ── fixtures ──────────────────────────────────────────────────────────────────

def _bundle(content: str, *, heading: str = "Scope") -> dict:
    return {
        "version": {"version_id": "v1", "doc_id": "d1", "version_no": 3,
                    "status": "in_review", "tenant_id": None,
                    "classification": "CUI"},
        "doc": {"doc_id": "d1", "title": "Test Doc"},
        "sections": [{"section_id": "s1", "heading": heading,
                      "content": content, "citations_json": None,
                      "status": "draft", "origin": "ai"}],
    }


def _change(start, anchor, suggested, *, status="pending", sid="sug-1"):
    return {"kind": "change", "id": sid, "section_id": "s1", "position": start,
            "anchor": {"state": "verified", "reason": None},
            "anchor_text": anchor, "author": "docmod redline",
            "created_at": DATE, "status": status, "origin_kind": "docmod_redline",
            "body": "supersede the crypto baseline",
            "suggested_content": suggested, "current_content": anchor,
            "replies": [], "reply_count": 0}


def _comment(start, anchor, body, *, replies=(), sid="ann-1", status="open"):
    return {"kind": "comment", "id": sid, "section_id": "s1", "position": start,
            "anchor": {"state": "verified", "reason": None},
            "anchor_text": anchor, "author": "Reviewer A", "created_at": DATE,
            "status": status, "category": "question", "body": body,
            "replies": list(replies), "reply_count": len(replies),
            "thread_broken": False}


def _rail(positioned=(), unpositioned=(), unplaced=(), state="items"):
    return {"doc_id": "d1", "version_id": "v1", "state": state, "reason": None,
            "sections": [{"section_id": "s1", "heading": "Scope",
                          "positioned": list(positioned),
                          "unpositioned": list(unpositioned), "counts": {}}],
            "unplaced": list(unplaced), "counts": {}, "unplaced_count": len(unplaced)}


def _build(tmp_path, bundle, rail, name="out.docx"):
    out = tmp_path / name
    report = dr.build_tracked_docx(bundle, rail, out, title="Test Doc",
                                   classification="CUI")
    return out, report


def _xml(path, part="word/document.xml") -> str:
    with zipfile.ZipFile(path) as z:
        if part not in z.namelist():
            return ""
        return z.read(part).decode("utf-8")


# ── the declared dependency ───────────────────────────────────────────────────

def test_python_docx_is_present_rather_than_skipped_around():
    """python-docx is DECLARED (requirements.txt:155), so its absence is a
    broken install and not a reason to skip.

    A module-level ``skipif`` here would make this whole file report green on a
    runner where it asserted nothing -- the "a gated test that SKIPS is an
    UNMEASURED test" rule -- and it would cost a skip-census entry against a
    ceiling that may only go down. The dependency is asserted instead.
    """
    assert dr.DOCX_AVAILABLE, (
        "python-docx is declared in requirements.txt but is not importable; "
        "this is a broken environment, not a skippable condition")


# ── the offset invariant ──────────────────────────────────────────────────────

@pytest.mark.parametrize("content", [
    "", "one line", "a\nb", "a\n\nb", "trailing\n", "\nleading",
    "\n\n\n", "unicode — ✓ ok\nsecond", "tabs\there\nand\tthere",
])
def test_paragraph_spans_round_trip_for_every_shape(content):
    """The identity the whole module rests on: an anchor offset indexes the
    SECTION content, so the partition must lose nothing."""
    spans = dr.paragraph_spans(content)
    assert dr.spans_round_trip(content, spans)
    for s in spans:
        assert content[s["start"]:s["end"]] == s["text"]


# ── a pending change is a real revision ───────────────────────────────────────

def test_pending_change_becomes_ins_and_del_with_deltext(tmp_path):
    content = "The system shall use FIPS 140-2 cryptography."
    start = content.index("FIPS 140-2")
    rail = _rail(positioned=[_change(start, "FIPS 140-2", "FIPS 140-3")])
    out, report = _build(tmp_path, _bundle(content), rail)

    xml = _xml(out)
    assert f"{{{W}}}ins" in xml.replace("w:ins", f"{{{W}}}ins") or "w:ins" in xml
    assert "<w:ins " in xml and "<w:del " in xml
    # THE trap: a `w:t` inside `w:del` is the classic silently-repaired file.
    dele = re.search(r"<w:del\b.*?</w:del>", xml, re.S).group(0)
    assert "<w:delText" in dele and "<w:t>" not in dele and "<w:t " not in dele
    ins = re.search(r"<w:ins\b.*?</w:ins>", xml, re.S).group(0)
    # The inserted text is the word-level delta (`3`), not the whole clause --
    # see the granularity test below.
    assert ">3</w:t>" in ins and "<w:delText" not in ins

    assert report["tracked_changes"] == 1
    assert report["revision_elements"] == 2
    assert report["deferred"] == []


def test_the_revision_is_word_level_and_not_the_whole_clause(tmp_path):
    """dwr-ws-01's granularity, carried into Word. Replacing FIPS 140-2 with
    FIPS 140-3 must delete `2` and insert `3` -- NOT delete the clause and
    re-insert it, which is what a line-level diff renders and what leaves a
    reviewer hunting for the one changed token by eye."""
    content = "The system shall use FIPS 140-2 cryptography."
    rail = _rail(positioned=[_change(content.index("FIPS 140-2"),
                                     "FIPS 140-2", "FIPS 140-3")])
    out, _ = _build(tmp_path, _bundle(content), rail)
    xml = _xml(out)
    assert re.search(r"<w:del[^>]*>.*?<w:delText[^>]*>2</w:delText>", xml, re.S)
    assert re.search(r"<w:ins[^>]*>.*?<w:t[^>]*>3</w:t>", xml, re.S)
    assert "FIPS 140-" not in re.search(r"<w:del.*?</w:del>", xml, re.S).group(0)


def test_rejecting_every_revision_gives_back_the_section_byte_for_byte(tmp_path):
    """The invariant that makes this a REVIEW copy and not a rewrite: the
    equal runs plus the deleted runs, in document order, reassemble the
    section's content of record exactly."""
    content = "The system shall use FIPS 140-2 cryptography."
    rail = _rail(positioned=[_change(content.index("FIPS 140-2"),
                                     "FIPS 140-2", "FIPS 140-3")])
    out, _ = _build(tmp_path, _bundle(content), rail)
    body = _xml(out).split("Scope", 1)[1].split("<w:br", 1)[0]
    # Runs in DOCUMENT ORDER; a w:t inside a w:ins is the proposed text and is
    # not part of the original, so it is excluded the way Reject would.
    original = []
    for match in re.finditer(
            r"<w:ins.*?</w:ins>|<w:delText[^>]*>([^<]*)</w:delText>"
            r"|<w:t[^>]*>([^<]*)</w:t>", body, re.S):
        if match.group(0).startswith("<w:ins"):
            continue
        original.append(match.group(1) or match.group(2) or "")
    assert "".join(original) == content


def test_revision_carries_the_proposers_author_and_date(tmp_path):
    content = "Use FIPS 140-2 here."
    rail = _rail(positioned=[_change(content.index("FIPS 140-2"),
                                     "FIPS 140-2", "FIPS 140-3")])
    out, _ = _build(tmp_path, _bundle(content), rail)
    xml = _xml(out)
    assert 'w:author="docmod redline"' in xml
    assert 'w:date="2026-09-08T10:00:00Z"' in xml


# ── an ACCEPTED change is reported, never re-proposed ─────────────────────────

def test_accepted_change_is_not_a_revision_and_is_reported(tmp_path):
    """Word's Reject over an accepted change would silently revert a decision
    already on the append-only chain, writing nothing back."""
    content = "Use FIPS 140-3 here."
    rail = _rail(positioned=[_change(content.index("FIPS 140-3"), "FIPS 140-3",
                                     "FIPS 140-3", status="accepted")])
    out, report = _build(tmp_path, _bundle(content), rail)

    assert report["tracked_changes"] == 0
    assert [d["reason"] for d in report["deferred"]] == ["not_pending"]
    assert "<w:ins " not in _xml(out)


# ── comments, and real threads ────────────────────────────────────────────────

def test_comment_thread_becomes_comments_and_commentsextended(tmp_path):
    content = "The enclave uses TLS 1.1 today."
    start = content.index("TLS 1.1")
    thread = _comment(start, "TLS 1.1", "Is this still accurate?",
                      replies=[{"ann_id": "ann-2", "author": "Reviewer B",
                                "comment": "No — 1.1 is sunset.",
                                "created_at": DATE}])
    out, report = _build(tmp_path, _bundle(content), _rail(positioned=[thread]))

    with zipfile.ZipFile(out) as z:
        names = z.namelist()
    assert "word/comments.xml" in names
    assert "word/commentsExtended.xml" in names

    comments = _xml(out, "word/comments.xml")
    assert "Is this still accurate?" in comments
    assert "No — 1.1 is sunset." in comments
    assert 'w:author="Reviewer A"' in comments and 'w:author="Reviewer B"' in comments

    ext = _xml(out, "word/commentsExtended.xml")
    # The namespace that has to be exactly right; the plausible wrong one
    # produces a package Word refuses outright.
    assert dr.NS_W15 in ext
    assert "http://schemas.microsoft.com/office/2012/wordml" not in ext
    assert "w15:paraIdParent" in ext, "a reply with no parent is not a thread"

    doc = _xml(out)
    assert "<w:commentRangeStart" in doc and "<w:commentRangeEnd" in doc
    assert doc.count("<w:commentReference") == 2, "root and reply both referenced"
    assert report["comments"] == 2


def test_a_resolved_thread_is_marked_done(tmp_path):
    content = "The enclave uses TLS 1.1 today."
    thread = _comment(content.index("TLS 1.1"), "TLS 1.1", "checked",
                      status="resolved")
    out, _ = _build(tmp_path, _bundle(content), _rail(positioned=[thread]))
    assert 'w15:done="1"' in _xml(out, "word/commentsExtended.xml")


def test_no_comment_parts_when_there_are_no_comments(tmp_path):
    """An empty comments.xml would make Word show an empty review pane for a
    document nobody commented on."""
    out, _ = _build(tmp_path, _bundle("Plain prose."), _rail())
    with zipfile.ZipFile(out) as z:
        assert "word/comments.xml" not in z.namelist()


# ── the refusals: never placed, never dropped ─────────────────────────────────

def test_unanchored_change_is_neither_placed_nor_dropped(tmp_path):
    """The live-board case: 58 of 58 suggestions carry no anchor at all."""
    item = _change(0, "", "FIPS 140-3")
    item["position"] = None
    item["anchor"] = {"state": "unanchored", "reason": "no_anchor_recorded"}
    out, report = _build(tmp_path, _bundle("Some prose."),
                         _rail(unpositioned=[item]))

    assert report["tracked_changes"] == 0
    assert "<w:ins " not in _xml(out)
    assert [d["reason"] for d in report["deferred"]] == ["no_verified_anchor"]
    assert "sug-1" in _xml(out), "a deferred item must still appear, labelled"


def test_unplaced_document_level_change_is_reported(tmp_path):
    item = _change(0, "", "something")
    item["position"] = None
    item["section_id"] = ""
    item["unplaced_reason"] = "no_section_named"
    rail = _rail(unplaced=[item])
    _, report = _build(tmp_path, _bundle("Prose."), rail)
    assert [d["reason"] for d in report["deferred"]] == ["unplaced"]
    assert report["deferred"][0]["unplaced_reason"] == "no_section_named"


def test_overlapping_changes_keep_the_first_and_report_the_second(tmp_path):
    content = "alpha beta gamma delta"
    a = _change(0, "alpha beta", "ALPHA BETA", sid="s-a")
    b = _change(6, "beta gamma", "BETA GAMMA", sid="s-b")
    _, report = _build(tmp_path, _bundle(content), _rail(positioned=[a, b]))
    assert report["tracked_changes"] == 1
    assert [(d["id"], d["reason"]) for d in report["deferred"]] == \
        [("s-b", "anchor_overlap")]


def test_change_spanning_a_paragraph_break_is_refused(tmp_path):
    content = "first line\nsecond line"
    item = _change(6, "line\nsecond", "LINE SECOND")
    _, report = _build(tmp_path, _bundle(content), _rail(positioned=[item]))
    assert report["tracked_changes"] == 0
    assert [d["reason"] for d in report["deferred"]] == ["anchor_spans_paragraphs"]


def test_anchor_text_that_no_longer_matches_is_refused(tmp_path):
    """Belt and braces over one computation trusted twice: the rail said
    verified, the slice does not hold."""
    content = "The system uses FIPS 140-3 already."
    item = _change(16, "FIPS 140-2", "FIPS 140-3")   # slice says 140-3
    _, report = _build(tmp_path, _bundle(content), _rail(positioned=[item]))
    assert [d["reason"] for d in report["deferred"]] == ["anchor_text_mismatch"]
    assert report["deferred"][0]["found_text"] == "FIPS 140-3"


def test_every_defer_reason_is_declared():
    """A reason the module can emit but has not declared is a reason no reader
    can be written against."""
    src = pathlib.Path(dr.__file__).read_text(encoding="utf-8")
    emitted = set(re.findall(r'_defer\([^,]+,\s*"([a-z_]+)"', src))
    assert emitted <= set(dr.DEFER_REASONS), emitted - set(dr.DEFER_REASONS)


# ── unmeasurable is never a clean bill of health ──────────────────────────────

def test_unmeasurable_rail_reports_none_counts_and_says_so(tmp_path):
    rail = {"doc_id": "d1", "version_id": "v1", "state": "unmeasurable",
            "reason": "sections_unreadable", "sections": [], "unplaced": [],
            "counts": {}, "unplaced_count": 0}
    out, report = _build(tmp_path, _bundle("Prose."), rail)
    # None, NEVER 0 -- 0 asserts there are no revisions.
    assert report["tracked_changes"] is None
    assert report["comments"] is None
    assert report["deferred_count"] is None
    assert report["rail_state"] == "unmeasurable"
    assert "could not be read" in _xml(out)


def test_empty_rail_is_a_measured_zero(tmp_path):
    _, report = _build(tmp_path, _bundle("Prose."), _rail(state="empty"))
    assert report["tracked_changes"] == 0
    assert report["comments"] == 0


# ── the exporter seam ─────────────────────────────────────────────────────────

def test_docx_tracked_is_a_declared_export_format():
    from tools.document_intelligence.exporter import EXPORT_FORMATS, format_available

    assert "docx_tracked" in EXPORT_FORMATS
    available, reason = format_available("docx_tracked")
    assert available, reason


def test_render_refuses_docx_tracked_without_the_bundle(tmp_path):
    """Anchor offsets index the section content; the assembled markdown has
    already lost them, so rendering from it would place revisions at offsets
    that silently moved."""
    from tools.document_intelligence.exporter import ExportUnavailable, render

    with pytest.raises(ExportUnavailable):
        render("docx_tracked", "text", "T", "CUI", tmp_path, bundle=None)


# ── the migration: without it a gated export is refused by the database ───────

def _migration_module():
    import importlib.util

    up = (REPO_ROOT / "tools" / "db" / "migrations"
          / "20260909004511_dic_artifacts_format_check_from_constant" / "up.py")
    spec = importlib.util.spec_from_file_location("_dwr_word_mig", up)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _artifacts_db(tmp_path, ddl):
    import sqlite3

    db = tmp_path / "artifacts.db"
    conn = sqlite3.connect(db)
    conn.execute(ddl)
    return conn


def test_sqlite_with_the_old_check_is_rebuilt_and_keeps_its_rows(tmp_path):
    """The case the migration exists for: SQLite cannot ALTER a CHECK, so a
    database that ran 20260903194350 would refuse every tracked export for the
    life of the database -- an artifact on disk the record cannot describe."""
    from tools.document_intelligence.exporter import _SCHEMA

    old = _SCHEMA.replace(
        "format           TEXT NOT NULL,",
        "format           TEXT NOT NULL CHECK (format IN "
        "('md','html','docx','pdf')),")
    conn = _artifacts_db(tmp_path, old)
    conn.execute("INSERT INTO dic_artifacts (artifact_id,version_id,doc_id,"
                 "format,created_at) VALUES ('a1','v1','d1','docx','t')")
    conn.commit()
    import sqlite3
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO dic_artifacts (artifact_id,version_id,doc_id,"
                     "format,created_at) VALUES ('a2','v1','d1','docx_tracked','t')")
    conn.rollback()

    result = _migration_module().rebuild_format_constraint(conn)
    assert result == {"backend": "sqlite", "rebuilt": True,
                      "reason": "CHECK omits docx_tracked"}

    conn.execute("INSERT INTO dic_artifacts (artifact_id,version_id,doc_id,"
                 "format,created_at) VALUES ('a2','v1','d1','docx_tracked','t')")
    conn.commit()
    kept = [tuple(r) for r in conn.execute(
        "SELECT artifact_id, format FROM dic_artifacts ORDER BY artifact_id")]
    # The row that was already there survives the rebuild.
    assert kept == [("a1", "docx"), ("a2", "docx_tracked")]
    conn.close()


def test_sqlite_without_a_check_is_left_alone(tmp_path):
    """`exporter._ensure_schema` creates the table with no CHECK, so it already
    accepts every format. A rebuild nothing needs is still a drop and a copy of
    somebody's data."""
    from tools.document_intelligence.exporter import _SCHEMA

    conn = _artifacts_db(tmp_path, _SCHEMA)
    result = _migration_module().rebuild_format_constraint(conn)
    assert result["rebuilt"] is False
    assert result["reason"] == "no CHECK on the live table"
    conn.close()


def test_the_extension_is_docx_not_the_format_name():
    """`document.docx_tracked` is a file Word will not open and a browser will
    not associate -- an artifact that passed every gate and is unusable."""
    from tools.document_intelligence.exporter import format_extension

    assert format_extension("docx_tracked") == "docx"
    assert format_extension("md") == "md"

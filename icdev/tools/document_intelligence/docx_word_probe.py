#!/usr/bin/env python3
# CUI // SP-CTI
"""dwr-word-01 — ask WORD what is in the file, not the XML.

THE VERIFICATION THIS CARD EXISTS FOR. A .docx whose ``w:del`` wraps a ``w:t``
instead of a ``w:delText``, or whose ``commentsExtended`` declares ``w15`` as
``…/office/2012/wordml`` instead of ``…/office/word/2012/wordml``, is
well-formed XML, passes every schema-shaped assertion anyone would write, and
Word refuses to open it — or worse, SILENTLY REPAIRS it and shows the reviewer
a document with no revisions in it. Both of those happened while this module's
sibling was being written; the second is the dangerous one, because a repaired
document opens cleanly and simply has nothing in it.

So the acceptance evidence is Word's own object model:
``Document.Revisions.Count``, ``Document.Comments.Count``, and for each comment
whether ``Comment.Ancestor`` is set (which is Word saying "this is a reply in a
thread", the only way to prove ``commentsExtended`` linked).

THREE VERDICTS, and ``unmeasurable`` is never folded into either other:

  verified      Word opened the document and reported its counts.
  refused       Word REFUSED the file. The finding.
  unmeasurable  no Word on this host, no COM bridge (this is Windows-only, by
                construction — it drives the real application), or the file is
                absent. NOT a clean bill of health, and the reason is named.

SILENT REPAIR IS THE DANGEROUS CASE AND IT IS NOT DIRECTLY OBSERVABLE.
``Document.Repaired`` is NOT reliably exposed by the Word object model —
measured on Word 16.0 it does not answer — so ``repaired`` is ``None`` with
``repaired_basis: not_exposed_by_object_model``, and that ``None`` means "this
probe cannot tell", NEVER "it was not repaired". What DOES carry the argument
is the counts: a repaired document loses its revision and comment markup, so a
probe reporting ``verified`` with the counts the build REPORTED is evidence of
no repair, and one reporting ``verified`` with zeroes against a build that
placed revisions is the silent-repair signature. Compare the two; do not read
either alone.

Report only. It writes nothing, opens the document READ-ONLY, and never runs
in CI — the gated suite has no Word. It is the human/acceptance probe, and
``tests/document_intelligence/test_docx_revisions.py`` is the CI half.

    python -m tools.document_intelligence.docx_word_probe <file.docx> [--json]
"""
from __future__ import annotations

import argparse
import json
import os
import sys

from tools.logging.icdev_logger import get_logger

logger = get_logger(__name__)

#: ``wdRevisionInsert`` / ``wdRevisionDelete`` from the Word object model.
REVISION_TYPES = {1: "insert", 2: "delete", 3: "property", 5: "paragraph_number"}

VERDICTS = ("verified", "refused", "unmeasurable")


def probe(path: str) -> dict:
    """Open ``path`` in Word read-only and report what Word says is in it."""
    result: dict = {"path": path, "verdict": "unmeasurable", "reason": None,
                    "revisions": None, "comments": None, "replies": None,
                    "repaired": None, "repaired_basis": None, "detail": []}
    if not os.path.isfile(path):
        result["reason"] = "file_absent"
        return result
    try:
        import win32com.client  # type: ignore
    except ImportError as exc:
        # tsg-iso-03: the handler SAYS it fired. pywin32 is deliberately NOT in
        # requirements.txt -- it is Windows-only and this platform is
        # OS-agnostic, so a hard dependency would break every Linux install for
        # an acceptance probe that could not run there anyway. "No COM bridge"
        # and "Word said no" are different answers and only one is a finding.
        logger.warning("pywin32 is not installed (%s); the Word probe cannot "
                       "open %s and reports unmeasurable", exc, path)
        result["reason"] = f"no_com_bridge (pywin32 not installed: {exc})"
        return result

    word = None
    try:
        word = win32com.client.Dispatch("Word.Application")
    except Exception as exc:  # noqa: BLE001
        result["reason"] = f"word_unavailable: {exc}"
        return result

    try:
        word.Visible = False
        word.DisplayAlerts = 0
        try:
            doc = word.Documents.Open(os.path.abspath(path), False, True)
        except Exception as exc:  # noqa: BLE001
            # Word REFUSED the document. This is a finding, not an
            # unmeasurable: Word was there and said no.
            result["verdict"] = "refused"
            result["reason"] = str(exc)
            return result
        try:
            result["verdict"] = "verified"
            result["revisions"] = int(doc.Revisions.Count)
            result["comments"] = int(doc.Comments.Count)
            replies = 0
            for rev in doc.Revisions:
                result["detail"].append({
                    "kind": "revision",
                    "type": REVISION_TYPES.get(int(rev.Type), str(rev.Type)),
                    "author": str(rev.Author),
                    "text": str(rev.Range.Text),
                })
            for comment in doc.Comments:
                is_reply = comment.Ancestor is not None
                replies += 1 if is_reply else 0
                result["detail"].append({
                    "kind": "comment",
                    "author": str(comment.Author),
                    "is_reply": is_reply,
                    "text": str(comment.Range.Text),
                })
            result["replies"] = replies
            try:
                # Not exposed by every object model (Word 16.0 does not answer).
                # None stays None: "cannot tell", never "was not repaired".
                result["repaired"] = bool(doc.Repaired)
                result["repaired_basis"] = "object_model"
            except Exception:  # noqa: BLE001
                result["repaired"] = None
                result["repaired_basis"] = "not_exposed_by_object_model"
        finally:
            doc.Close(False)
    finally:
        if word is not None:
            try:
                word.Quit()
            except Exception:  # noqa: BLE001
                pass
    return result


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[2])
    ap.add_argument("path", help="the .docx to open in Word")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    result = probe(args.path)
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(f"verdict:   {result['verdict']}")
        if result["reason"]:
            print(f"reason:    {result['reason']}")
        print(f"revisions: {result['revisions']}")
        print(f"comments:  {result['comments']} "
              f"({result['replies']} of them replies)")
        print(f"repaired:  {result['repaired']} "
              f"({result['repaired_basis'] or 'not probed'}) — a None here means "
              "this probe cannot tell; compare the counts above with the "
              "build's own report")
        for entry in result["detail"]:
            print(f"  {entry}")
    # 0 verified, 1 refused, 2 could not measure -- an unmeasurable probe is
    # never the same as a clean one.
    return {"verified": 0, "refused": 1}.get(result["verdict"], 2)


if __name__ == "__main__":
    sys.exit(main())

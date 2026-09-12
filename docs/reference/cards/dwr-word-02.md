# A reviewer's Word revisions, read back in and RECONCILED (dwr-word-02)

> Moved verbatim from CLAUDE.md on 2026-09-12 (xrv-docs-02). The prose is the card's own record; only the leading `#` comment markers were stripped and the command runs fenced.

```bash
python -m tools.document_intelligence.docx_review_import --file review.docx --version <version_id>
python -m tools.document_intelligence.docx_review_import --file review.docx --version <version_id> --json
python -m tools.document_intelligence.docx_review_import --file review.docx --parse-only
python -m tools.document_intelligence.docx_word_probe review.docx    # what WORD says is in it
```

The return leg of dwr-word-01, which sent a review copy OUT and had nothing
read one back: a reviewer who struck a clause and typed a replacement had
produced exactly the artifact this canvas exists to adjudicate, and the only
way it re-entered ICDEV was somebody retyping it. Library + CLI, no route --
an operator who receives a marked-up copy by email has a FILE, and `python
-m` is this canvas's own idiom for that (originals, page_geometry,
suggestion_redraft).
THE HARD PART IS NOT PARSING, IT IS RECONCILIATION, AND THE REASON IS THAT
BOTH SIDES MOVE. While the reviewer was reading, a docmod sweep could draft a
redline over the same sentence, a human could accept one,
supersede_suggestion could retire one whose anchor drifted, and a section
could be regenerated. So a revision that arrives back is not a proposal about
the document -- it is a proposal about a document that may no longer exist.
THREE VERDICTS, AND NO FOURTH:
  matched      located to exactly ONE span of exactly one section, and
               nothing on the ICDEV side contests it. `origin` says WHICH
               thing it is -- own_proposal (one of our own exported redlines,
               back undecided) or reviewer_edit (their own new writing).
               Both are matched; they are not the same thing and are never
               merged.
  conflicting  it located AND the ICDEV side also changed that span. BOTH
               sides carried whole, surfaced for adjudication, never merged
               and never won by one side. base_paragraph_changed |
               competing_proposal | decided_since_export.
  unmatched    it could not be located. REPORTED BY NAME with its reason,
               never dropped -- a dropped revision is a reviewer's edit that
               silently ceased to exist, which is worse than one nobody could
               place. paragraph_not_found | paragraph_ambiguous |
               empty_paragraph_text | anchor_ambiguous |
               insertion_point_unlocatable | paragraph_mark_revision |
               heading_not_anchorable | comment_range_spans_paragraphs |
               comment_range_unlocatable | comment_body_absent.
NOTHING HERE DECIDES AND NOTHING HERE WRITES. No INSERT/UPDATE/DELETE and no
store writer, pinned by an AST test -- the failure mode is a later edit
threading a "just apply the matched ones" flag through, and a behavioural
test over today's callers would still pass the day it happens. `matched` says
WHERE a revision goes, never that it MAY go there: the accept door
(cef-ui-03) is the only writer of dic_sections.content, a human decides at
it, and turning a reviewer's Word edit into an applied change without one is
the "never auto-apply" prohibition wearing an import's name.
ABSENCE IS NOT A DECISION, and it is the one inference the module refuses to
make. An exported redline that does NOT come back is consistent with the
reviewer having ACCEPTED it (Word then writes the text plain), REJECTED it
(the text is simply gone), never having reached it, having deleted the whole
paragraph, or the upload being a different document altogether. ONE
OBSERVATION, FIVE CAUSES, and four of them are not decisions. So it lands
under `absent_from_upload`, a labelled ABSENCE; reading a decision out of it
would write a human's verdict that no human gave onto the append-only
dic_suggestion_decisions chain.
LOCATION IS AGAINST THE PARAGRAPH THE REVIEWER RECEIVED. `before` is equal +
delete -- the paragraph with every revision REJECTED, which is exactly what
was in the .docx when it left here and the only coordinate space an ICDEV
anchor means anything in. Looked up through docx_revisions.paragraph_spans,
dwr-word-01's partition, IMPORTED and not re-derived (AST-pinned): an offset
that means one thing on the way out and another on the way back is the whole
class of defect this series is about. Exactly one match locates; an ambiguous
match is never resolved by picking one (resolve_anchor's rule). LOCATED BY
SPAN BUT NOT BY PARAGRAPH IS A CONFLICT, never a match -- both sides changed
that paragraph, and a clean match would hand a human a merge nobody measured.
A COMMENT IS NOT AN EDIT, so a comment is never `conflicting`. One anchored to
a span a pending redline proposes to replace is the NORMAL case -- a reviewer
asking about a proposal -- and flagging it would bury the real findings. The
contest rides as `contested_by` CONTEXT and never as the verdict, and a
comment overlapping a comment is not a contest at all.
COUNTS ARE None AND NEVER 0 when nothing was measured. An `unmeasurable` rail
makes the WHOLE report unmeasurable and not merely the contest half:
`matched` asserts "nothing contests this span", which is a claim about the
rail. Parsing is measurable INDEPENDENTLY of the board and is reported
separately -- `--parse-only` touches no database.
THE ROUND TRIP IS MEASURED, THROUGH WORD ITSELF: export through the gated
export_version, open in Word 16.0 over COM with track changes on, edit, save
BY WORD, reconcile. On a seeded 2-section version 2026-09-08 it read
matched 5 (2 of our own redlines + our own comment ROOT and REPLY + the
reviewer's new comment) / conflicting 3 (one per reason) / unmatched 1 (an
edit in dwr-word-01's own appendix) / absent 1. IT FOUND THREE REAL DEFECTS
THE UNIT SUITE STRUCTURALLY COULD NOT, because that suite reads back XML this
repo wrote:
  1. THE EXPORTER EMITS A WORD-LEVEL DIFF (FIPS 140-2 -> 140-3 leaves as del
     `2` / ins `3`), so comparing a returned revision against a suggestion's
     OWN anchor_text/suggested_content columns matched NOTHING and THREE OF
     THREE of our own redlines came back reported as rivals to themselves.
     The expectation is now re-derived through word_diff.diff_words -- the
     exporter's own function, imported. A second spelling of "what does a
     suggestion look like as revisions" is how the two halves of a round trip
     come to disagree about a document neither of them changed.
  2. THREADS WERE SEARCHED FOR ROOTS ONLY, so our own author's reply came
     back as a stranger's new remark. Structurally too: dwr-word-01
     highlights a thread ONCE with one w:commentReference per reply inside
     that single range, while Word 16.0 saving the same document writes a
     range PER COMMENT. Both shapes are legal and both must read, so
     `thread_range` resolves a comment to its own range or to its nearest
     thread ancestor's.
  3. AN ITEM NAMED ONLY AS A CONTESTER WAS ALSO REPORTED ABSENT -- telling a
     reader the same proposal both came back and did not.
UNTRUSTED INPUT IS BOUNDED BEFORE IT IS PARSED (sandbox-coverage Gap 70,
trusted-first-party-bounded, deliberately narrower than Gap 69's): stdlib
zipfile + ElementTree ONLY -- no python-docx, no markitdown, no OCR, no
subprocess; a declared DOCTYPE is REFUSED unparsed (ElementTree does not
resolve external entities but does expand internal ones, and no legitimate
OOXML part carries a DTD); a part over ICDEV_DOCX_IMPORT_MAX_PART_BYTES
(64 MiB) is refused on its DECLARED uncompressed size BEFORE decompression;
only word/document.xml, word/comments.xml and word/commentsExtended.xml are
ever read, BY NAME, and nothing is extracted to disk; a hit revision bound
(ICDEV_DOCX_IMPORT_MAX_REVISIONS, 5000) reports `truncated` rather than a
quietly short list.
A COM GOTCHA THAT PRODUCED A GREEN LOG OVER AN UNEDITED FILE, recorded because
it cost a whole round: late-bound `Find.Execute(Replace=wdReplaceAll)` returns
a TRUTHY HIT and SILENTLY DOES NOT REPLACE. Call Execute POSITIONALLY. And
`Application.UserName` set before `Documents.Open` did not take, so the
reviewer's revisions carry the machine's Word user.
NOT BUILT, and named rather than implied: no route and no page (an upload
endpoint owes CSRF, RBAC and its own sandbox entry; a report surface owes the
8-point page gate); NOTHING turns a matched reviewer edit into a
dic_suggestions row -- that is a WRITE, and it belongs at a door with a human
at it; a revision on a section HEADING is unmatched BY NAME, because a
heading is its own column and carries no anchors; and a paragraph-mark
revision (a split or a merge) is unmatched by name too, because ICDEV anchors
offsets INSIDE one section's content and has no representation for it.
Report only, no --gate (kpr-fix-03). Exit 0 = a report was produced, whatever
it says; exit 2 = it could not be, which is never the same as a clean round
trip. Round trip in full: docs/audits/dwr-word-02-round-trip.md

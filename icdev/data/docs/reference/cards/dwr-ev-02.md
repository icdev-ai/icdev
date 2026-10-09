# A COMMENT is an instruction until a human promotes it; then it is CITED, and marked (dwr-ev-02)

> Moved verbatim from CLAUDE.md on 2026-09-12 (xrv-docs-02). The prose is the card's own record; only the leading `#` comment markers were stripped and the command runs fenced.

A library, no CLI. Import it:
  from tools.document_intelligence.sme_evidence import promote_comment, promotions_for
  promote_comment(conn, ann_id="ann-...", promoted_by="lead.reviewer",
                  claim={"entity_label": "TLS 1.1", "entity_type": "crypto_protocol",
                         "status": "retired", "as_of": "2026-07-01"})
```bash
python -m tools.currency.entity_currency --resolve "tls 1.1" --entity-type crypto_protocol
```

Promote: POST /document-intelligence/api/annotations/<ann_id>/promote
         {"promoted_by": "...", "claim": {...}}      404 unknown / 409 already / 400 bad claim
Read:    GET  /document-intelligence/api/annotations/<ann_id>/promote
UI:      /document-intelligence/doc/<doc_id> -> a section's comments panel
         ("Mark as SME assertion"), and the citation on /document-intelligence/docdrift

AN UNPROMOTED COMMENT IS NOT RANKED LOW, IT IS ABSENT. "We moved to TLS 1.3
last quarter" in a review comment is an INSTRUCTION, and a reviewer's chat
message is not a source -- letting unverifiable prose ground a compliance claim
is the hallucination the TRUST chain exists to stop. So the mechanism is not a
weight: `dic_section_annotations` is declared as a currency source NOWHERE and
is read by NO evidence seam (asserted over seven of them by an AST test), so
there is no path by which an unpromoted comment could be cited, and no flag on
the comment that could go stale. The state IS the absence of a row in
`dic_sme_assertions`.
THE PROSE IS NEVER PARSED, AND THAT IS THE WHOLE TRUST ARGUMENT. A promotion
carries a TYPED claim the PROMOTING HUMAN supplies -- entity, type, status --
validated by `author_evidence.normalize_assertion`, the SAME one validator an
author's upload goes through, so a status word cannot mean one thing on an
upload and another on a promotion. The comment text is stored VERBATIM as the
quotation and NOTHING reads a word of it: an assertion extracted from prose is
a `text_pattern` claim and can never reach a pack (TRUST rule 2, dwr-ev-01's
rule unchanged). An AST test refuses a regex over `comment` in promote_comment
and any model call in the module. So what the comment contributes is not the
claim -- it is WHO said it, WHEN, and against WHICH span of WHICH document,
which is exactly what makes the evidence ATTRIBUTED.
PER-COMMENT AND DELIBERATE. One `ann_id` in the URL, one in the writer; no bulk
endpoint (asserted absent by name), no promotion on any heuristic, and a second
promotion of the same comment is a 409 rather than a silent rewrite of what a
human already decided. AUDITED AS A DECISION, BEFORE THE WRITE, FAIL-CLOSED:
`_record_hitl_decision("dic_annotation", ..., "promoted_to_sme_assertion", ...)`
-- the cef-ui-03 door, `dic.hitl_decision`, raise_on_error=True -- so an
unauditable promotion never happens, and the row names BOTH the person whose
word it now is and the person who decided it was evidence.
TWO CLOCKS. `as_of` is the SME's; an SME who states no date gets the COMMENT's
own timestamp with `as_of_basis: comment_time`, never today's, because a
promotion made months later must not restamp their statement. `promoted_at` is
ours. `asserted_by` is COPIED off the comment -- an attribution the promoter
can type in is not an attribution.
RANKED BESIDE THE AUTHOR, NOT ABOVE. `dic_sme_assertions` is the SIXTH declared
source in args/entity_currency.yaml (migration 20260908071149), kind
`sme_attributed`, `precedence: 0` and confidence 0.9 -- IDENTICAL to
dic_author_assertions, so the two tie on precedence AND on the prior and the
LATER human clock decides, with the earlier preserved under `others` with
conflict:true. Above them would let a 2020 remark beat a 2026 signed upload;
below would let a stale upload beat this morning's correction. Neither is a
rule about evidence; recency between equals is, and the store already had it.
RENDERED DISTINCTLY, STRUCTURALLY, IN BOTH HALVES OF BOTH SURFACES. The citation
carries `source_type: sme_assertion` -- its own badge, not the
`currency_assertion` a machine feed produces and not a document type -- derived
from the store's `source_kind` (`_HUMAN_SOURCE_TYPES`), never from a source
NAME, so a seventh human source is a YAML entry and one line. Every other kind
is untouched, asserted. The content sentence itself names the person ("Asserted
by <who> ... This is an attributed human statement, not a document"), because
the reader of a drafted paragraph sees the snippet and not the badge. DocDrift
frames it amber under an ATTRIBUTED HUMAN SOURCE chip and the comments panel
renders a promoted comment in its own amber block with both clocks and both
people, an unpromoted one under "Instruction only -- cited by nothing".
THE VIEW CAN NOW READ ITS OWN CARRIED FIELDS. `provenance` gains `fields` --
the winner's declared `extra_columns`, decoded. args/entity_currency.yaml has
always said they are "carried verbatim into provenance_json ... so it is
preserved rather than lost", and until now nothing could read them BACK; a
carrier that only ever writes is not preservation, and an attributed citation
that cannot name the human is not attributed.
TWO SMEs DISAGREEING ARE TWO ROWS -- the unique key is the COMMENT, so nothing
overwrites anything -- and `promote_comment` REPORTS the contradiction it is
creating under `contradicts` rather than landing it silently. RESIDUAL, NAMED:
the STORE still keeps one row per (source, entity) and so carries the newest
human clock; making two statements from ONE source two STORE rows is a change
to the store's identity key, not this card.
NOT REVERSIBLE THROUGH THIS SEAM, and why: `entity_currency.backfill` is
upsert-only and the store has no delete path, so deleting the assertion row
would leave the derived currency row standing -- a revocation that looks like
it worked and did not, which is worse than none. Rather than invent a second
writer of currency rows inside a DIC module, correction works the way human
evidence works: a LATER attributed statement supersedes an earlier one and the
earlier stays readable. A real revoke needs a store deletion path and is its
own card.
NOT BUILT, and named: no DataBridge connector for this table (dwr-ev-01's
`icdev_author_evidence` serves author uploads; a second brokered rung changes
the `search_external` fan-out, which already cost one follow-up test fix), and
no extraction of assertions from prose, ever.

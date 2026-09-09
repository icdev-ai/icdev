# dwr-word-02 — the round trip, measured

**CUI // SP-CTI**

The card's DONE criterion is a round trip: *export, edit in Word, re-import,
and show matched, unmatched and conflicting revisions each reported by name.*
This is that run. It was done on 2026-09-08 on this host (Windows 11, Word
16.0, pywin32) and it found three real defects in `docx_review_import.py` that
the unit suite could not have found, because the unit suite reads back XML this
repo wrote. Each defect now has a test that fails without its fix.

Nothing in this document touched the live PostgreSQL board. Every step ran
against a throwaway SQLite database (`ICDEV_STORAGE_BACKEND=sqlite`,
`ICDEV_DB_PATH` under `.tmp/`), asserted at the top of each script.

## Why the fixture had to be constructed

Measured on the live board the same day: `dic_suggestions` holds 61 pending
rows and **not one carries a verified anchor** (58 with `anchor_basis` NULL,
3 `unanchored`), and the three `dic_section_annotations` rows are likewise
`unanchored`. `dic_artifacts` holds **zero** rows — nothing has ever been
exported. A `docx_tracked` export of any live version therefore carries zero
revisions and zero comments and every item lands in dwr-word-01's appendix,
which is the correct behaviour and proves nothing about a round trip. So the
version below was seeded.

## The document

Two sections, five paragraphs.

| section | paragraph | ICDEV item at export |
|---|---|---|
| `sec-001` | `All traffic shall use FIPS 140-2 validated cryptography.` | `sug-crypto` pending, `FIPS 140-2` → `FIPS 140-3` |
| `sec-001` | `The enclave boundary is defined in Appendix B.` | — |
| `sec-001` | `Legacy Catalyst 6500 switches remain in the core.` | `sug-eol` pending, `Catalyst 6500` → `Catalyst 9500` |
| `sec-002` | `Accounts are reviewed every 90 days by the ISSO.` | `sug-review` pending, `90 days` → `60 days` |
| `sec-002` | `Privileged accounts are reviewed every 30 days.` | `ann_7dab…` open thread on `Privileged accounts`, one reply |

## Step 1 — export, through the gated door

`exporter.export_version(VER, "docx_tracked", …)` — the same gate chain as any
other export (placeholder → citation → WriteGuard), with `force_writeguard`
and a stated reason because seeded prose is not a publishable document.

```
ARTIFACT: art_008e3338d1f74103915c docx_tracked 94dd48891b28
RENDER:   tracked_changes 3 · revision_elements 6 · comments 2 · deferred 0
```

`docx_word_probe` on that artifact, i.e. Word's own object model:

```
verdict:   verified
revisions: 6
comments:  2 (1 of them replies)
  delete '2' / insert '3'       by doc_modernization
  delete '6500' / insert '9500' by doc_modernization
  delete '90' / insert '60'     by doc_modernization
  comment 'Does this include service accounts?'  (root)
  comment 'Yes — see the ISSO memo.'             (reply)
```

## Step 2 — a reviewer edits it, in Word

Driven over COM with `TrackRevisions = True`, saved **by Word**, so what comes
back is Word's own serialisation and not this repo's XML read back by the
module that wrote it.

- `Appendix B` → `Annex D` (a span ICDEV proposed nothing about at export)
- `every 30 days` → `every 14 days` (uncontested)
- `Every recorded change` → `Every logged change` — **inside dwr-word-01's
  appendix**, which is not a section of the version
- a new comment on `Privileged accounts`: *"Does this cover break-glass
  accounts?"*
- the three exported redlines left alone

Word then reported `12 revisions, 3 comments`.

> **A COM gotcha worth recording, because it produced a file that looked
> right.** Late-bound `Find.Execute(Replace=wdReplaceAll)` returns a truthy hit
> and **silently does not replace**. The first run printed three "applied"
> edits and wrote a document containing zero reviewer revisions — a green log
> over a file that had not been edited. `Execute` must be called
> **positionally**. Also: `Application.UserName` set before `Documents.Open`
> did not take, so the reviewer's revisions are attributed to this machine's
> Word user (`Larry Chuon`) rather than to `Reviewer B`. Both are stated rather
> than tidied away — the author string in the report below is the real one.

## Step 3 — the ICDEV side moves too

All four of these happen on this board between an export and its return.

- `sug-annex` drafted over `Appendix B` → `Annex B` — a **rival** for the span
  the reviewer edited
- `sug-review` **rejected** by `isso.lead` — a decision taken while the
  reviewer was reading
- `sec-001`'s third paragraph edited: `… remain in the core.` → `… remain in
  the core pending refresh.`
- `sug-isso` drafted over `ISSO`, nowhere near anything the reviewer touched

## Step 4 — reconcile

```
python -m tools.document_intelligence.docx_review_import \
    --file reviewed.docx --version ver-roundtrip
```

```
state:     items
matched 5 · conflicting 3 · unmatched 1
           (revisions 6, comments 3, absent from upload 1)

MATCHED (5)
  revision own_proposal_unchanged @ sec-001[31:32]
      '2' -> '3' by doc_modernization              icdev item: sug-crypto
  revision reviewer_edit           @ sec-002[82:95]
      'every 30 days' -> 'every 14 days' by Larry Chuon
  comment  own_comment_unchanged   @ sec-002[49:68]
      Reviewer A: '[question] Does this include service accounts?'
                                                   icdev item: ann_7dab8d0759da4134
  comment  own_comment_unchanged   @ sec-002[49:68]
      Author: 'Yes — see the ISSO memo.'           icdev item: ann_359beeab35d04e16
  comment  reviewer_comment        @ sec-002[49:68]
      Larry Chuon: 'Does this cover break-glass accounts?'

CONFLICTING (3)
  revision competing_proposal      @ sec-001[92:102]
      'Appendix B' -> 'Annex D' by Larry Chuon     icdev item: sug-annex
      a pending ICDEV redline proposes something different for this span
  revision base_paragraph_changed  @ sec-001[120:124]
      '6500' -> '9500' by doc_modernization
      contested by change sug-eol (pending): 'Catalyst 6500' -> 'Catalyst 9500'
      the paragraph the reviewer edited no longer exists in this version, and
      their deleted text was found elsewhere; both sides moved
  revision decided_since_export    @ sec-002[28:30]
      '90' -> '60' by doc_modernization            icdev item: sug-review
      somebody adjudicated this span while the reviewer was editing it

UNMATCHED (1)
  revision paragraph_not_found     @ (unplaced)
      'Every recorded change' -> 'Every logged change' by Larry Chuon

ABSENT FROM THE UPLOAD (1) — NOT a decision
  change sug-isso (pending) @ sec-002[43:47]: 'ISSO'
```

Every offset above was checked by hand against the section content.
`sec-002` is `Accounts are reviewed every 90 days by the ISSO.\nPrivileged
accounts are reviewed every 30 days.` — `90` at 28, `ISSO` at 43, the second
paragraph starting at 49, `Privileged accounts` at 49–68, `every 30 days` at
82–95. They agree.

## The three defects this run found, and nothing else would have

**1. Our own redlines came back as rivals to themselves — three of three.**
dwr-word-01 emits a **word-level** diff, so `FIPS 140-2 → FIPS 140-3` leaves
here as a deletion of `2` and an insertion of `3`. The reconciler compared the
returned revision against the suggestion's own `anchor_text` /
`suggested_content` columns, which match nothing, so every one of our exported
proposals reported `competing_proposal` **against itself**. Fixed by
re-deriving the expectation through `word_diff.diff_words` — the exporter's own
function, imported (`expected_revision_groups`). A second spelling of "what
does a suggestion look like as revisions" is how the two halves of a round trip
come to disagree about a document neither of them changed.

**2. Our own reply came back as a stranger's remark.** Comment threads were
searched for **roots only**, so `Yes — see the ISSO memo.` — written by our own
author, exported by us — reconciled as `reviewer_comment`. Fixed in
`_matching_annotation`, which now searches a thread whole.

A second, structural half of the same defect: dwr-word-01 highlights a thread
**once** and emits one `w:commentReference` per reply inside that single range,
while Word 16.0 — saving the same document — writes a range **per comment**.
Both shapes are legal. A reader that walked ranges only saw the root and
dropped every reply on our own export. `thread_range` now resolves a comment's
anchor to its own range where it has one and to the nearest thread ancestor's
otherwise, and `read_revisions` reconciles over referenced comments rather than
over ranges.

**3. A contested item was also reported absent.** `base_paragraph_changed`
deliberately records no `icdev_item_id` — the point of that verdict is that
nothing can be identified cleanly — so `sug-eol`, named only as the
**contester** of that finding, also appeared under `absent_from_upload`. That
tells a reader the same proposal both came back and did not. `absent_items` now
excludes anything accounted for as a contester as well as anything matched.

## What this run does NOT prove

- **Nothing was written back.** The module is report-only by construction and
  by AST test. The reviewer's `Annex D`, the new comment and the `every 14
  days` edit are all still only in the .docx; turning any of them into a
  `dic_suggestions` row is a separate act with its own door and is named as not
  built in the module docstring and in CLAUDE.md.
- **Word's revision granularity is Word's.** `every 30 days → every 14 days`
  came back as one whole-phrase replacement because Find/Replace replaced the
  whole match; a human typing over two characters would produce a narrower one.
  The reconciler is indifferent — it locates whatever span Word records — but a
  reader comparing this report to the ICDEV word-level diff should not expect
  the two granularities to agree.
- **One document.** Five paragraphs, two sections, one reviewer, one save.
  Every verdict in the closed set fired at least once, which is the coverage
  this run was designed for; it is not a survey of a population.

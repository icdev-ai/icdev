# CUI // SP-CTI

# cef-rsv-03 — Citation validation and provenance persistence on every `resolve()`

`cortex.resolve()` shipped in cef-rsv-01 with one citation rule enforced: every
inline `[source: id]` tag in the returned prose is validated through the shared
`tools/quality/citation_grounding` and an unresolvable tag **blocks**. cef-rsv-02
then gave it real cross-backend conflict detection. This card closes the loop
over the three surfaces neither of those reached.

## What was open

| Surface | State before | Why it mattered |
|---|---|---|
| `gaps[]` | reasons + backend lists, **no citations** | A gap is what opens a data-quality ticket. Unattributable, it is an assertion — a reader could not check the silence for themselves. |
| `conflicts[]` | every side with its provenance fields, **no citations** | A conflict is what opens an adjudication. Each side named a `source_id` and nothing resolved it to the resolution's own evidence. |
| `Citation.provenance_id` | **empty on every resolution ever returned** | The governance chain's provenance gate writes one `source_citation_registry` row per governed call whose `source_hash` is a sha256 of the **egress prose**. That attests what was *said*; it names no source, so the evidence a verdict rests on was never registered. |
| "Recommended replacement: X" | rendered straight from `pack.recommend()` | That line is the one a redline is drafted from. `redline_drafter` hard-blocks a draft naming a replacement outside the candidate list; the resolve side had no equivalent, so a pack naming a successor it could not back produced an actionable, uncited instruction. |

## What shipped

`tools/cortex/resolution_provenance.py` (new, mirrored to `icdev/tools/cortex/`),
wired into `resolver.resolve()` as three blocks and one write.

### 1. Every finding carries the evidence that produced it

* `attach_gap_citations(gaps, hits, citations)` — a gap cites the sources that
  **mentioned** the entity and did not answer for it, using
  `entity_resolution._mentions`: the *same* predicate that chose the gap's
  reason. A second, locally written mention test could let a gap say `no_claim`
  ("the corpus HAS this entity") while its citation list says nothing mentioned
  it — one finding contradicting itself.
* `attach_conflict_citations(conflicts, citations)` — each conflict cites the row
  behind each side.

An **empty** citation list on a gap is not one fact, so it carries a
`citation_basis`:

| basis | meaning | the fix it points at |
|---|---|---|
| `evidence_did_not_answer` | sources mention the entity, none states its currency (they ARE cited) | content, or a source that carries a verdict |
| `no_evidence_retrieved` | nothing matched at all — the absence is the finding | ingestion |
| `retrieval_failed` | the fan-out died | infrastructure. **Never** merged with the row above: an outage must not read as a statement about the corpus |

A conflict side that names an authority and **no row id** — an `entity_currency`
source that lost read-time resolution carries no record id, and cef-rsv-02
deliberately declined to lend it the winner's — lands in `uncited_sides` with
the reason stated. It is not dropped, and it is not given the nearest available
citation.

### 2. Three hard blocks, one allowed set

`finding_citation_report()` validates every id a gap or conflict points at
against the **same** allowed-id set the prose is validated against, so the two
surfaces can never disagree about what an id is. The closed refusal vocabulary
lives on `resolver`:

| `CortexResolutionBlocked.reason` | cause | which layer is broken |
|---|---|---|
| `hallucinated_citation` | a prose `[source: id]` tag resolves to nothing (cef-rsv-01) | the renderer |
| `unattested_finding` | a gap/conflict points outside the evidence set | the detector |
| `unattested_replacement` | a `superseded_by` is rendered with no cited `Replacement.source_ref` | the pack |

All three **refuse**; none degrades a field. Every shipped domain pack sets
`Replacement.source_ref` to its evidence's source, so the third is tight rather
than aspirational: nothing in the tree trips it, and a *new* pack that names an
unbacked successor is refused instead of having its guess rendered as an
instruction. `reason` is surfaced on both the REST 403 and the MCP response —
three different bugs must not arrive as one word.

**No new citation parsing.** `citation_grounding` owns citation text and
`resolver` already calls `validate_citations` for the prose. Everything added
here validates a **structured** id a claim already carried, by set arithmetic;
`tests/cortex/test_resolve_trust_loop.py` asserts by AST that neither module
imports `re`.

### 3. One registry row per resolution

`register_resolution()` writes one `source_citation_registry` row per resolution
that returns:

* `citation_type="cortex"` — an **already-registered** vocabulary value. A new
  one without a migration rendered from `check_constraint_sql()` raises before
  the INSERT and lands nothing, which is cxo-trust-01 verbatim.
* `source_table="cortex_resolution"`, distinct from the governance gate's
  `cortex_governance`: the two rows attest different things about the same call
  (an evidence digest vs a prose hash) and a reader must be able to tell them
  apart in one query. Both rows are still written.
* `source_hash` = `citation_digest(entity, verdict, citations)` — an
  order-independent sha256 over each citation's identity (id / table / type;
  snippets excluded, so a rewording does not change the hash).
  **Recomputable from the returned resolution**, which is what makes the row
  checkable rather than merely present.
* `source_record_id` = `cres-<digest[:16]}`, deterministic, so the same entity
  resolved twice over the same evidence names one record instead of two
  uncorrelatable ids.
* the returned registry id is stamped onto every `Citation.provenance_id`, so
  the join runs both ways.

One row, not one per citation: `register_citation` opens its own connection per
call, and twenty-odd inserts would cost a verb designed to run over a document
sweep its latency budget for nothing the digest does not already give.

`trust_score` is left at its default. A resolution has no *measured* trust
score, and writing `1.0 if grounded else 0.0` would put a declared prior in a
column readers take for a measurement.

A failed write never blocks — `governance.py` documents its provenance gate as
never blocking and that is a platform-wide decision, not this module's — but it
is always legible. Three statuses, recorded on the resolution's own
`GovernanceReport` under the `provenance` gate name:

| status | outcome | meaning |
|---|---|---|
| `written` | `pass` | the row landed |
| `unavailable` | `warn` | operational: connection refused, table missing, or `register_citation` swallowed a DB error and returned `""` |
| `misconfigured` | `fail` (+ ERROR log) | the `citation_type` is not in `CITATION_TYPES` — a **programming** error |

That last split is the whole cxo-trust-01 lesson: the Cortex provenance gate
recorded a merely-flaky `warn` for a bad vocabulary value and wrote 0 of 285
rows for its entire lifetime while nothing went red.

## One pre-existing defect fixed

`resolve()` handed **all** hits — including advisory (`sme`) ones — to
cef-rsv-02's detector. An `sme` hit is an opinion an LLM authors at query time;
cef-rsv-01 excludes it from citations for exactly that reason. Fed to the
detector it became a claim, the claim became a **side of a reported conflict**,
and its `source_id` was in no citation — an LLM-authored verdict reaching a
finding through a door the citation exclusion does not cover. `resolve()` now
passes the **evidentiary** hits to the detector and to the gap reasons: an
opinion is not a corpus match either, so answering `no_evidence` off a response
containing nothing but an opinion is the same category error.
`metadata["advisory"]` still surfaces it, visibly and not as evidence.

## Verification

`tests/cortex/test_resolve_trust_loop.py` (25 cases, gated via
`args/ci_test_files/core.d/cef-rsv-03.txt`).

The provenance assertions leave the registry write **real** against a temp
SQLite database and read it back with `SELECT`, because every other test of this
path patches the writer and asserts the *call* — the shape that cannot see a
write that never happens. Each is paired with a control that must flip:

* `test_pre_fix_vocabulary_lands_nothing` drops `cortex` from `CITATION_TYPES`
  and asserts the row is gone, the status is `misconfigured` (not
  `unavailable`), and no citation was stamped.
* `test_a_backed_replacement_is_returned` is the control for the replacement
  block: the same pack with a `source_ref` resolves cleanly.
* `test_an_advisory_opinion_never_becomes_a_conflict_side` fails on the pre-fix
  tree — verified by reverting the one-line detector input and watching it raise
  `unattested_finding` on `sme:1`.
* `test_the_finding_block_is_a_refusal_not_a_flag` reads the audit row for
  `blocked_gate == "operation"` and asserts the refused resolution registered
  nothing, so a degrading implementation cannot pass.
* Each honest zero has its own case: a gap with no citations because nothing
  matched, a gap from a dead fan-out, a conflict side with no row id, and a
  resolution that claims no successor.

Full `tests/cortex/` suite: 1247 passed, 2 skipped.

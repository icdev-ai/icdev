# CUI // SP-CTI

# Delta Review — the side-by-side HITL panel (trust-hitl-02)

## The defect

`tools/quality/hitl_delta.py` landed in trust-hitl-01 and made the delta a
first-class, persisted object: `trust_deltas` now records what a draft said,
what it says now, and the claim-anchored spans between the two.

Nothing rendered it. The evidence existed and no human could look at it, which
is the same defect one layer up from the one trust-hitl-01 fixed: a reviewer
approving a self-corrected draft was still approving a diff nobody had shown
them.

The repo also had no generic delta review UI at all.
`doc_modernization/redline_drafter.py` produces one suggestion at a time into
the DIC accept/edit/reject surface; `dashboard/templates/review_board.html`
lists findings without ever showing what changed.

## What shipped

| Piece | File |
|---|---|
| Canvas constants (all re-exported) | `tools/delta_review/constants.py` |
| Read-side assembly | `tools/delta_review/review.py` |
| Routes | `tools/delta_review/blueprint.py` |
| Page (+ `icdev/` mirror) | `tools/dashboard/templates/delta_review/page.html` |
| IQE adapters (4 collections) | `tools/iqe/adapters/delta_review.py` |
| Seed queries | `context/iqe/queries/delta_review/` (4) |
| Registry (nav, IQE map, PATH_CANVAS) | `args/component_registry.yaml` |
| Tests (72, gated in this PR) | `tests/test_delta_review.py` |

UI: `/delta-review` (queue) and `/delta-review?delta_id=<id>` (side-by-side
panel). Toggle `ICDEV_DELTA_REVIEW_ENABLED`; `default_enabled: true`.

**No migration.** `20260815063941_trust_hitl_deltas` already created
`trust_deltas`. A second `CREATE TABLE IF NOT EXISTS` silently no-ops and its
extra columns never exist — see "What the first attempt got wrong" below.

## Three design decisions worth reading

### 1. A finding is attached to a SPAN by `item_number - 1`

This is the mechanism the whole panel rests on, and it is not new — it is a
property the TRUST spine already guarantees. `compute_delta` emits spans
carrying `before_index` / `after_index`: 0-based positions in
`hitl_delta.anchored_claims(text)`. Every guard in the spine numbers its
findings `item_number` **1-based over exactly that decomposition** —
`claim_gate` is written against it directly, and it is what
`self_correct.target_findings` already relies on.

So the join is `item_number - 1 == before_index`, and it is why the diff is
claim-anchored rather than a line diff: a reviewer sees not that a paragraph
moved but that the unsupported claim inside it is why the draft was blocked.

**What this makes visible.** A claim that was *reworded but still carries its
finding* is invisible to `self_correct`'s monotone invariant, because that
invariant only compares the TOTAL finding count — so the case hides whenever
some other span's finding cleared in the same round.
`review.resolve_span_findings` labels every span `resolved` / `persisting` /
`regressed` / `clean`, and the panel counts, highlights and warns on them.

Findings that anchor to no span — `placeholder_guard` and `citation_guard`
report at document level, and a guard that ran over a different revision can
point past the end — are partitioned out by `annotate_spans` in the same pass
and rendered separately. Computing the two halves in separate calls is how the
same finding ends up rendered twice, or nowhere.

### 2. Where a decision lives: `approval_items`, not a column here

`trust_deltas` is append-only EVIDENCE and owns no disposition column. The
human's answer is mutable state on the `approval_items` row reached through
`Delta.approval_item_id`; `settle_delta` moves it through
`approval_inbox.resolve`, which writes the permanent `agent_approval_log` entry.

`review.review_state` derives the panel's badge from there, and its resolution
order is deliberate at every step:

| condition | state | why |
|---|---|---|
| a later correction supersedes it | `SUPERSEDED` | wins over everything — it is no longer the thing to review |
| no `approval_item_id` | `PENDING` | the ask failed to queue; a failed enqueue must not read as an approval |
| the item is absent | `PENDING` | pruned, or on another tenant — nobody answered it either way |
| `resolved` + `approved`/`denied` | `APPROVED`/`DENIED` | |
| `expired` / `cancelled` | `LAPSED` | **not** `DENIED`. Nobody looked. Collapsing the two is how a timeout starts reading as a decision |

Both PENDING cases are the same call `hitl_delta.pending_deltas` makes, and the
panel withdraws the Approve/Deny buttons for a delta with no readable ask rather
than offering a control that would 409 on click.

**Nothing derived is persisted.** Finding counts, net change, per-span verdicts
and review state are computed at read time, every time.
`test_no_count_column_was_written_to_trust_deltas` asserts the table has not
grown a column for any of them.

### 3. The rationale floor lives in the route, not the store

`hitl_delta.settle_delta` accepts an empty `reason` and substitutes
`"delta <id> approved"` so a CLI or an expiry sweep still writes a well-formed
`agent_approval_log` row. That default is a *label*: it restates the action and
says nothing about the evidence.

The panel is the one surface where a human is looking at the diff, so it is the
one surface that can insist. `MIN_RATIONALE_CHARS = 10`, enforced at 400 by the
route (`trust_gate` invariant 4, the `pulse.py` `force_publish` precedent). The
floor is deliberately low — a smell test for an empty gesture, not an essay
requirement.

Two more properties of that route: the actor is bound to the authenticated user
and a body-supplied `actor` is **ignored** (the `integrity.blueprint._reviewer`
rule, nav-comp-06), and a second settle is a **409, never an overwrite** —
`approval_inbox._settle` UPDATEs conditionally on `state = 'pending'`, and this
route surfaces that refusal rather than reporting a decision it did not make.

`context/iqe/queries/delta_review/02_decisions_without_a_real_rationale.iqe`
exists so the mandatory-rationale claim is *checkable* rather than asserted. A
guardrail nobody can query is one nobody can falsify.

## CUI handling

The draft artifact is CUI, and it appears in exactly one place: the panel,
behind auth.

* `approval_items` bodies carry counts, hashes and a link only —
  `render_delta_summary` already enforces that, and those rows are mirrored out
  to Slack / Teams / Telegram / email.
* **The IQE adapters emit no draft text at all.** `before_text` / `after_text`
  are absent from every SELECT; the `findings_*` JSON columns are read only to
  COUNT them, because `claim_gate` puts the first 120 characters of the
  offending claim into `detail`; and the `spans` collection emits claim INDICES
  and verdicts, never claim strings. IQE results travel into analyst answers, AI
  briefs and chat replies.

  This costs the spans collection something, and it is named rather than hidden:
  `where p.finding_verdict == "persisting"` is a bulk-TRIAGE answer, and reading
  the claim itself is a one-delta act performed in the panel by someone who
  opened it.

## The four IQE collections, and why `settlements` and `decisions` are separate

The fact lives in two tables, deliberately. `approval_items` holds the mutable
outcome; the reviewer's RATIONALE is not in that table at all —
`approval_inbox._settle` passes it to `record_decision`, which appends it to
`agent_approval_log.reason`.

Collapsing them would require a join key that does not exist:
`agent_approval_log` has no `item_id`, and its link back is `rule = 'hitl_delta'`
plus the rendered `detail` line, which identifies the artifact and stage but not
the individual delta. `decisions_adapter` parses `artifact_id` out of `detail`
on a best-effort basis and the docstring says plainly that it is not a foreign
key.

`settlements` joins `trust_deltas` to `approval_items` in **Python**, for the
reason `pending_deltas` documents — the two are RLS-eligible independently. Here
the SQL failure would be quieter still: an inner join would drop every delta
whose ask never queued, which is exactly the population an operator auditing
HITL coverage is looking for.

## Verification

**Unit — 72 tests, gated in `args/ci_test_files/core.txt` in this PR.**
In-memory SQLite built from the real migrations' DDL (`20260815063941`,
`20260809203855`, `20260803002224`) behind the production `%s` translation, via
the same fixture shape `tests/test_hitl_delta.py` uses. Patching
`tools.db.storage` by string form misses the `icdev.` module object, so the
fixture resolves the shim out of `sys.modules` explicitly — a test that silently
reaches the live board is worse than no test. No DB, no network.

Three invariants were verified to **discriminate**, not merely to pass — each
defect was injected and the tests written for it failed:

| injected defect | caught by |
|---|---|
| `item_number` used as the span index directly (off-by-one) | `TestFindingAnchoring` (2 tests) |
| `expired`/`cancelled` collapsed into `DENIED` | `test_a_lapsed_ask_is_not_a_denial` (2 params) |
| an absent approval item read as settled | `TestReviewState` (2 tests) |

The 8-point page-completeness gate was checked the same way: removing
`tools/iqe/adapters/delta_review.py` makes `check_new_page_completeness` fail
naming this page, so it is genuinely covered rather than passing vacuously.

**End-to-end — real dashboard, live PostgreSQL, real browser. 28 checks, all
green.** Chromium via Playwright against `tools/dashboard/app.py` on port 5071
(5060/5061 are on Chrome's blocked-port list), reading and writing the live
PostgreSQL primary. No console errors. Screenshots under
`playwright/screenshots/delta_review_*.png` — local evidence; `playwright/` is
gitignored.

The delta under review was recorded through the real
`hitl_delta.record_delta` → `claim_gate` path over a draft asserting
`"ICDEV supports 47 compliance frameworks [source: ssp-1]"` — a well-formed
citation that resolves, against a source that never states the number. The
revision changes it to `"several dozen"`, which is **still unsupported**. So
findings go **1 → 1**: the count does not fall, `self_correct`'s monotone
invariant would report no progress and no regression, and only the per-claim
view says what actually happened.

1. Queue lists it; a delta approved in an earlier run is absent from it.
2. Panel renders three spans — one `modified`, two `unchanged`. The BEFORE side
   still shows `47`, the AFTER side does not, and `unsupported_claim` is
   attached to **that span on both sides**, which is what makes it read
   `persisting` rather than `resolved`. The two untouched claims read `clean`.
3. A token rationale (`"ok"`) is refused in the UI and no request is sent.
4. Approving with a real rationale succeeds; the page reloads.
5. After reload: state `APPROVED`, the controls are withdrawn, **the diff is
   still rendered** (it is evidence, not a receipt), the approval item is named,
   and it has left the pending queue.

Database, on live PostgreSQL, after that approval:

| | observed |
|---|---|
| `trust_deltas` row | unchanged — still `stage=promote`, its original actor and rationale |
| successor deltas | **0** — no settlement row was appended |
| `approval_items` | `pending` → `resolved` / `approved` |
| `agent_approval_log` | carries the reviewer's full rationale, actor `admin@icdev.local` — the **authenticated user**, not the delta's recorder |

The last row is the point of two separate rules landing together: the decision
is attributed to whoever was signed in, and the reason they gave survives in the
append-only log.

### The seed-query gate gap, closed for this canvas

The completeness gate counts seed-query FILES. **Nothing has ever parsed one** —
neither `check_new_page_completeness` nor
`component_registry.validate_canvas_completeness`. So a canvas can satisfy the
8-point standard with three `.iqe` files that raise on their first token.

That is not hypothetical: the IQE lexer has no `//` comment form (the supported
one is `#`), and the first attempt at this canvas shipped three of its four seed
queries with `//` headers. They are visually indistinguishable from the working
one — same extension, same body syntax — and the gate passed all four.

Two tests close it here: every `*.iqe` must parse and name a collection this
canvas actually registers, and one is EXECUTED end to end through `parse` →
`execute_query`. Parsing is not running: a query that parses but selects a
column no adapter emits is equally silent. The executing test passes its
connection in **explicitly** — with `conn=None` the adapter opens its own via
`get_connection()` and the assertion lands on the real database rather than the
fixture.

Generalising these two tests to every canvas is worth a task of its own; it is
deliberately not done here.

## What the first attempt got wrong (PR #1684, closed unmerged)

Recorded because the failure mode is reusable, not to relitigate it. That branch
was written against a **parallel implementation** of `hitl_delta.py` developed
at the same time as the one that landed, and the two disagreed at the
domain-model level:

| | PR #1684 | landed, and what this PR targets |
|---|---|---|
| stages | `manual_edit`, `override`, `self_correction`, `settlement` | `draft`, `review`, `promote`, `export`, `correction` |
| span kinds | `SPAN_ADDED/CHANGED/REMOVED/UNCHANGED` | `OP_ADDED/MODIFIED/REMOVED/UNCHANGED` |
| dispositions | `DISPOSITION_APPROVED/DENIED/PENDING` | none — the decision is an `approval_items` row |
| settlement | a successor delta (`stage=settlement`) | `approval_inbox.resolve` |
| counts | persisted (`findings_before_n`, …) | derived at read time |

`stage` has no CHECK constraint — it is validated in Python by `STAGES` — so
that branch's stages would have been rejected on write and matched nothing on
read, and its `STAGE_LABELS` / `SPAN_BADGES` / `DISPOSITION_BADGES` would have
rendered nothing recognisable. It also added a second migration for
`trust_deltas` fifteen seconds after the landed one, whose
`CREATE TABLE IF NOT EXISTS` silently no-ops so its extra columns never exist —
the exact defect CLAUDE.md warns about.

The branch is preserved at `ref/trust-hitl-02-pr1684` rather than deleted.

**The lesson this PR encodes in code:** `constants.py` now re-exports every
vocabulary from `hitl_delta` and `approval_inbox` and declares none of its own,
and `TestCanvasWiring` asserts the registry's collection list equals the
constants'. A canvas that keeps its own copy of a status list is the drift bug
CLAUDE.md names for CHECK constraints, and it fails the same way: the store
accepts a value the panel then renders as blank.

## Known-adjacent, not addressed here

* `capability_liveness` fails identically on an untouched `main` checkout at the
  same commit this branch is based on (`skill_optimizer`: 3 never-consumed units
  against a budget of 1). Pre-existing, GEPA's epic. Verified by running the
  check in the shared checkout on `b8465c099`.
* The four `trust_deltas` rows already on the live board carry PR #1684's stage
  vocabulary (`settlement`, `self_correction`) — residue from that branch's
  verification run. The panel renders them correctly: `_stage_label` falls back
  to a title-cased stage rather than a blank, which is precisely the degradation
  a canvas that redeclared its own vocabulary would not have.
* `tools/genesis/reflexes/cache_warm.py` was unregistered in
  `tools/manifest/`, failing `check_manifest` on `main`. Registered here — a
  one-row append to a `merge=union` shard — so this PR's gate is green for the
  right reason.

# CUI // SP-CTI

# Delta Review — the delta as the reviewable unit (trust-hitl-01 / trust-hitl-02)

## The defect

A `force_*` override records **that** a human overrode. It never records **what
changed**.

`idr_publish_audit`, `agent_approval_log` and the mandatory `force_reason` on
`dashboard/api/pulse.py` all answer *who cleared the gate and what they said
about it*. None of them answers *what text the human actually accepted*. So a
reviewer approving a self-corrected draft is approving a diff nobody has ever
shown them — which is unauditable after the fact and indistinguishable from a
rubber stamp.

The repo also had no generic delta review UI at all.
`doc_modernization/redline_drafter.py` produces one suggestion at a time into
the DIC accept/edit/reject surface; `dashboard/templates/review_board.html`
lists findings without ever showing what changed.

## What shipped

| Piece | File |
|---|---|
| Evidence model + store | `tools/quality/hitl_delta.py` |
| Migration (append-only) | `tools/db/migrations/20260815063956_trust_hitl_deltas/` |
| Canvas constants | `tools/delta_review/constants.py` |
| Read-side assembly | `tools/delta_review/review.py` |
| Routes | `tools/delta_review/blueprint.py` |
| Page (+ `icdev/` mirror) | `tools/dashboard/templates/delta_review/page.html` |
| IQE adapters | `tools/iqe/adapters/delta_review.py` |
| Seed queries | `context/iqe/queries/delta_review/` (4) |
| Registry (nav, IQE map, PATH_CANVAS) | `args/component_registry.yaml` |
| Tests | `tests/test_hitl_delta.py`, `tests/test_delta_review_blueprint.py` |

UI: `/delta-review` (queue) and `/delta-review?delta_id=<id>` (side-by-side
panel). Toggle `ICDEV_DELTA_REVIEW_ENABLED`; `default_enabled: true`.

## Three design decisions worth reading

### 1. The diff is claim-anchored, never a raw text diff

`citation_grounding.decompose_claims` returns ORIGINAL-TEXT offsets, and every
guard in the TRUST spine numbers its findings 1-based over exactly that
decomposition (`claim_gate`, `kg_gate` — the same property
`self_correct.target_findings` already relies on). So a delta aligns BEFORE
claims to AFTER claims with `difflib.SequenceMatcher` — stdlib, deterministic,
no LLM, runs air-gapped like TRUST stage 1 — and each aligned span carries the
findings open against it on both sides.

A line diff would anchor to nothing: a reviewer would see that a paragraph moved
without seeing that the unsupported claim inside it is why.

This is what makes the panel's central case visible at all. A claim that was
**reworded but still carries its finding** is invisible to `self_correct`'s
monotone invariant, because that invariant only checks the TOTAL finding count —
so this case hides whenever some *other* span's finding cleared in the same
round. `review.resolve_span_findings` labels every span `resolved` /
`persisting` / `regressed` / `clean`, and the panel counts and highlights them.

### 2. The storage split: append-only evidence, mutable state

This mirrors migration `20260809203855` exactly, and the split is deliberate on
both sides.

| | lifetime | append-only? |
|---|---|---|
| `trust_deltas` | an observation: this text became that text, at this instant | **yes** — in `APPEND_ONLY_TABLES` |
| `approval_items` | short-lived state: created `pending`, moved once to terminal | **no**, deliberately |

Settling APPENDS a `settlement` successor through `supersedes_delta_id` and
**never** edits its predecessor — not even to flag it settled. That is the
`sbom_revision.apply_correction` / `supersedes_sbom_id` rule. So a predecessor
still SAYS `pending` forever, and "has it been settled" is DERIVED at read time
by `delta_chain` / `get_settlement`.

**The consequence that bites:** `pending_deltas()` must filter on the successor.
A naive `WHERE disposition = 'pending'` re-queues every settled delta
indefinitely. `tests/test_hitl_delta.py::test_settled_state_is_derived_from_the_successor_not_the_column`
is the guard, and it was verified to fail when that defect is injected.

Two smaller consequences of taking append-only seriously:

* `record_delta` enqueues the approval ask **before** the INSERT, so the module
  never has to UPDATE its own append-only table to store the foreign key. The
  residual risk — an ask whose delta failed to insert — is handled by cancelling
  the ask, because a dead link in an approval queue is worse than a missing one:
  a reviewer who cannot see the diff still has an Approve button.
* A no-op delta (`before_hash == after_hash`) is REFUSED. Asking a human to
  review nothing trains them to approve without looking.

### 3. A rationale is mandatory, and the actor is not the caller's to choose

`settle_delta` refuses an empty rationale and settles nothing — `trust_gate`
invariant 4, ported from the `pulse.py` `force_publish` precedent. The route
enforces it again at 400 with a 10-character floor, because that is the layer
that can tell a human *why*, whereas the store can only return `None`.

The recorded actor is bound to the authenticated user; a body-supplied `actor`
is ignored (the `integrity.blueprint._reviewer` rule, nav-comp-06). A caller
must not be able to attribute a disposition to someone else.

A second settle is a **409, never an overwrite** — a second successor row would
give the panel two contradictory answers about the same diff.

`context/iqe/queries/delta_review/02_approvals_without_rationale.iqe` exists so
the mandatory-rationale claim is *checkable* rather than asserted. A guardrail
nobody can query is one nobody can falsify.

## CUI handling

The draft artifact is CUI, and it appears in exactly one place: the panel,
behind auth.

* The `approval_items` body carries counts and a link only — those rows are
  mirrored out to Slack / Teams / Telegram / email, the same convention
  `approval_inbox.render_summary` enforces for tool arguments.
* The IQE adapters emit no draft text at all. `before_text` / `after_text` are
  absent from every SELECT and the `spans` collection emits claim INDICES and
  verdicts, never claim strings, because IQE results travel into analyst
  answers, AI briefs and chat replies. This costs the spans collection
  something and it is named rather than hidden: `where s.finding_verdict ==
  "persisting"` is a bulk-TRIAGE answer, and reading the claim itself is a
  one-delta act in the panel.

## Verification

**Unit — 36 tests, both files gated in `args/ci_test_files/core.txt` in this PR.**
In-memory sqlite via a patched `hitl_delta._connect` (patching
`tools.db.storage` by string form misses the `icdev.` module object, and a test
that silently reaches the live board is worse than no test). No DB, no network.

Both central invariants were verified to **discriminate**, not merely to pass —
each defect was injected and the test written for it failed:

| injected defect | caught by |
|---|---|
| `pending_deltas` trusts the stale `disposition` column | `test_settled_state_is_derived_from_the_successor_not_the_column` |
| `settle_delta` UPDATEs its predecessor | `test_settlement_appends_and_never_touches_its_predecessor` |

The 8-point page-completeness gate was checked the same way: removing
`tools/iqe/adapters/delta_review.py` makes `check_new_page_completeness` fail
naming this page, so it is genuinely covered rather than passing vacuously.

**End-to-end — real pipeline, real browser, live PostgreSQL.**

1. `TrustGate("drafting").evaluate` over a fluent, well-cited, partly invented
   draft (`"ICDEV supports 47 compliance frameworks [source: ssp-1]"`) →
   Stage 1 **blocked** on `claim_guard/unsupported_claim`, detail `['47']`. The
   citation is well-formed and resolves; the number is invented — exactly the
   case TRUST v2 exists to catch.
2. `self_correct` — the real monotone loop, real targeting, real re-validation
   against the same gate; only the LLM call itself was stubbed by a scripted
   router so the run is deterministic and air-gapped. Round 1 accepted on
   `strict_decrease`, 1 → 0 findings.
3. Delta recorded; approval ask queued.
4. **Playwright (Chromium), 22 checks, all green.** The queue lists it; the
   panel renders both claims with the BEFORE side still showing `47`, the AFTER
   side not, and `unsupported_claim` attached to that span; a token rationale
   (`"ok"`) is refused in the UI; approving with a real rationale succeeds; after
   reload the controls are withdrawn, the disposition and rationale are shown,
   the diff is still rendered (it is evidence, not a receipt), and it has left
   the pending queue. No console errors.
5. Database, on live PostgreSQL: the `trust_deltas` evidence row exists, the
   settlement is a separate APPENDED successor pointing at it, **the predecessor
   is byte-identical and still says `pending`**, the `approval_items` row moved
   `pending → resolved/approved`, and `agent_approval_log` carries the permanent
   decision with the reviewer's rationale.

Screenshots: `playwright/screenshots/delta_review_{queue,side_by_side,rationale_required,approved,settled}.png`.

## Known-adjacent, not addressed here

* `capability_liveness` fails identically on an untouched `main` checkout
  (`cache_warm` reflex, GEPA `skill_optimizer`). Pre-existing, other epics; the
  budgets in `args/liveness_gate.yaml` were deliberately not raised.
* trust-hitl-03 wires the remaining `force_*` call sites to record an override
  Delta. `STAGE_OVERRIDE` and the panel's "From Overrides" stat already exist
  for it; no schema or UI change is needed.

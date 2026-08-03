# CUI // SP-CTI

# FORGE Academy — Assessment Model (aca-trn-01)

**Status:** specified and implemented. The spec half of this document is normative;
the "What shipped" section records how much of it is wired and what is deliberately
left to `aca-trn-02` … `aca-trn-05`.

---

## 1. Why this exists

The INT epic (`aca-int-01` … `aca-int-07`) made grading server-authoritative: the
browser no longer says whether it passed, the test comes from the step row, the
answer key is stripped before the page is rendered, and XP has a provenance ledger.
That plumbing is correct and this document does not revisit it.

What INT could not fix is that **there was nothing to grade**. The Academy has the
machinery of assessment and no assessment model:

| Gap | Evidence in the tree before this change |
|---|---|
| No pass threshold | `grading.py::_verdict` hardcodes `score = 100 if passed else 0`. `db.py::record_step_attempt` does the same. A step is 100 or 0, always. |
| No attempt limit | `record_step_attempt` upserts. Nothing counts attempts, nothing caps them, and there is no formative/summative distinction anywhere in the app. |
| No item bank | Every reflect step is free text. `content_loader.BUILTIN_STEPS` contains **32 reflect steps and zero `question`/`options`/`correct` keys**, so `_grade_reflect` short-circuits at `if not schema.get("question") or not options` and returns `assessed=False, passed=True` for all of them. |
| No randomisation | There was no item to randomise. |
| Certificate gate declared but dead | `constants.CERT_TIERS["foundation"]["requirements"]["assessment_score_min"] = 70` and the description promises "20-question adaptive assessment". `db.py::check_cert_eligibility` never reads that key — it falls off the end of the `if reqs.get(...)` chain and is silently dropped. |
| Content promises a question that does not exist | `content/tier1/m01-llm-fundamentals/steps/step4_context_window.md` ends with **"Test your understanding — answer the question below to continue."** There is no question below. The step is a `watch`. |

This is the root cause the task description names: most of the INT epic's edge cases
are downstream of there being no model. 94% of steps are passive because passive is
the only thing the catalogue knows how to be.

### Karpathy gate

**Assumptions.** (1) Server-authoritative grading from the INT epic is sound and is
the substrate — this change extends `grade_step`, it does not replace it. (2) A
learner may hold exactly one open attempt per step; concurrent attempts on one step
by one user are not a case worth modelling. (3) The runner reports a test suite as
one boolean (`code_runner.run_code` returns a single `passed`), so per-test partial
credit is not available without changing the sandbox contract — out of scope here.
(4) PostgreSQL is the primary backend; all new SQL is `%s`-parameterised and the
tables are declared in both the migration and `db.SCHEMA`, per the `fa_xp_ledger`
precedent (a query against a missing table inside an open transaction aborts that
transaction on PG).

**Interpretations of "pass threshold" that were considered.**
1. *A fraction of a mission's steps must pass.* Rejected: since `aca-int-05`, a
   failed step is filed `attempted`, not `completed`, so "all steps completed"
   already implies "all assessed steps passed". A per-mission fraction below 100%
   would be strictly **weaker** than today's rule — it would let a mission complete
   with a failed step in it. See §4.
2. *A fraction of a step's items must be correct.* Adopted for item-scored steps.
   This is where a threshold has real work to do.
3. *A weighted score across step types.* Rejected as unfalsifiable: it would assign
   a number to watching a video.

**Success criteria** — stated before the code, all now covered by tests in
`tests/test_academy_assessment_model.py`:
- A 5-item bank served 3 at a time produces a different served set/order across
  attempts, and the correct index never appears in any client-facing payload.
- 2 of 3 correct = 66% = **fail** at a 70% threshold. 3 of 3 = pass. The old code
  could not express this.
- A summative step refuses a 4th attempt and grants no credit for it.
- A practice step accepts an unbounded number of attempts.
- A mission of only `watch` steps completes but is classified `attested`, never
  `demonstrated`, and contributes nothing to the certificate assessment score.
- `assessment_score_min` is enforced: a learner below 70% aggregate is not eligible
  for the Foundation certificate, and the gate appears in `gates[]` with its figure.

**Bounded scope.** No change to the sandbox, the XP formula, the tier gate, hint
accounting, or certificate issuance mechanics. The item bank is seeded for the three
Tier-1 lesson steps that have no verification test (§7); authoring items for the
remaining catalogue is content work belonging to `aca-trn-02`/`aca-trn-03`.

---

## 2. Step assessment classes

Derived at runtime by `assessment.classify_step`; never stored, so it cannot drift
from the row it describes.

| Class | Condition | Completable | Counts toward a certificate |
|---|---|---|---|
| `graded` | a `coding` step with a non-empty `test_code_path`, **or** any step with ≥1 active row in `fa_assessment_items` | yes, by passing | **yes** |
| `ungraded` | an assessed *type* (`coding`) with nothing to grade against | **no** — `_grade_coding` already refuses | no |
| `acknowledged` | `watch` / `configure` / `verify` / `deploy` / `design`, and a `reflect` step with no item bank | yes, by acknowledging | no |

`acknowledged` is not a failure state. Reading a lesson and writing a free-text
reflection is real work and still earns XP; it is simply not evidence of a
demonstrated skill, and a certificate must be able to tell the two apart. This is
the same distinction `aca-int-07` introduced with `assessed: False` — this document
gives it a name and a certificate consequence.

An item bank promotes a step to `graded` **regardless of its declared type**. That is
deliberate: it is what lets a `watch` lesson become assessable by authoring items
against it, without rewriting the catalogue's step types (`aca-hon-04` established
that a step type must describe what the step actually is).

---

## 3. Thresholds

All in `constants.py`. Nothing in this section may be hardcoded at a call site.

| Constant | Value | Applies to | Rationale |
|---|---|---|---|
| `STEP_PASS_THRESHOLD_PCT` | 70 | item-scored steps | Score is `correct / served × 100`. 70 is the same bar the Foundation certificate already declared, so a learner meets one standard, not two. |
| `CODING_PASS_THRESHOLD_PCT` | 100 | coding steps | A test suite is all-or-nothing **because the runner says so**: `code_runner.run_code` returns one boolean for the whole script. Partial credit here would be invented, not measured. Recorded as a constant rather than an implicit `if passed` so that when the runner learns to report per-test results, there is one place to change. |
| `MISSION_ASSESSMENT_THRESHOLD_PCT` | 80 | mission classification | Fraction of a mission's **graded** steps that must be passed for the mission to be `demonstrated`. Does **not** gate completion — see §4. |
| `CERT_ASSESSMENT_THRESHOLD_PCT` | 70 | certificate gate | Wires the previously dead `assessment_score_min`. Kept equal to the step threshold on purpose. |
| `ASSESSMENT_ITEMS_PER_ATTEMPT` | 3 | item draw | Default `items_per_attempt`. Three is the smallest draw for which a 70% threshold is not degenerate: 2/3 = 67% fails, 3/3 passes. With one item a "threshold" is just the binary pass/fail this task exists to remove. |
| `ASSESSMENT_MIN_BANK_SIZE` | 5 | authoring rule | A bank must exceed the draw or every attempt serves the same items. Enforced by `validate_item_bank`, which the seeder calls. |
| `SUMMATIVE_MAX_ATTEMPTS` | 3 | summative steps | |

---

## 4. Mission-level model

**Mission completion is unchanged.** `grading.mission_is_complete` still requires
every step to have a `completed` progress row, and since `aca-int-05` a failed step
is not `completed`. Lowering that to a percentage would be a regression, and raising
it is impossible — it is already 100%. Interpretation 1 in §1 is rejected for this
reason.

What is added is a **classification** of what a completed mission is evidence *of*,
returned by `assessment.mission_assessment_summary(user_id, mission_id)`:

| `assessment_status` | Meaning |
|---|---|
| `demonstrated` | the mission has ≥1 graded step, and ≥`MISSION_ASSESSMENT_THRESHOLD_PCT` of them were passed |
| `attested` | the mission completed, but has no graded steps — pages were turned |
| `partial` | has graded steps, completed, but below the threshold (reachable only through an instructor reset, which can leave a passed step behind a later failed attempt) |
| `incomplete` | not all steps completed |

This is the number a certificate should cite, and it is why a mission of three
`watch` steps can no longer look like a coding mission on a verification page.

---

## 5. Attempt policy

Per step, in `fa_step_assessment_policy`. Absent row = `practice` defaults.

| Policy | Attempts | Meaning |
|---|---|---|
| `practice` (default) | unlimited | Formative. Retry until it clicks; that is what practice is for. |
| `summative` | `SUMMATIVE_MAX_ATTEMPTS` (3) | A check that gates a certificate. |

Rules:

1. **Only a `graded` step may be summative.** A limited number of attempts at
   clicking "I Understand" is theatre. `set_step_policy` refuses it and
   `step_policy` downgrades a mis-seeded summative acknowledgement to practice
   rather than locking a learner out of a button.
2. **The limit is enforced before grading**, in `api_step_submit`. An exhausted step
   returns `status: "attempts_exhausted"` with no verdict and no XP. Grading first
   and discarding the result would let a learner burn attempts to enumerate the bank.
3. **Exhaustion is not permanent.** `reset_attempts(user_id, step_id, reason, by)`
   records a compensating `reset` row in the append-only ledger — it never deletes
   attempts. The instructor UI that calls it is `aca-trn-04`; the function and its
   audit trail land here so that shipping a limit does not ship a dead end.
4. **A passed step is never re-locked.** Mastery is not withdrawn (the rule
   `record_step_attempt` already follows). Once passed, further attempts are
   recorded and cost nothing.

---

## 6. Item bank and per-attempt selection

### Serving

`assessment.open_attempt(user_id, step_id)`:

1. Returns the learner's existing open attempt if there is one, so a page refresh
   does not consume an attempt or reroll the questions. **A refresh must not be a
   way to shop for an easier draw.**
2. Otherwise draws `items_per_attempt` items from the step's active bank, shuffles
   the draw, and shuffles each item's options independently.
3. Writes the served order and the per-item option permutation to
   `fa_step_attempts.served_json`, then returns only `{item_key, prompt, options[]}`
   — plain strings, positional, no key, no flags.

Randomness comes from `secrets.SystemRandom`, not a seeded PRNG. A seed derived from
`(user_id, step_id, attempt_num)` would be reproducible by anyone who knows those
three values, which is the learner.

### Grading

`assessment.grade_attempt(user_id, step_id, answers)` where `answers` maps
`item_key → displayed index`:

1. Loads the open attempt and maps each displayed index back through the recorded
   permutation to the real option index. **The client's indices are meaningless
   without the server's row**, which is what makes the DOM useless to an attacker.
2. `score_pct = correct / served × 100`; `passed = score_pct >= threshold`.
3. Closes the attempt (writes `score_pct`, `passed`, `answers_json`, `closed_at`).
4. Returns per-item feedback — correct/incorrect and the explanation — **only after
   the attempt is closed**.

### Rendering

A step with an active bank renders `partials/_step_assessment.html` **instead of its
step-type pane**, not alongside it. Two reasons:

1. It mirrors `grade_step`, where `_grade_items` already takes precedence over the
   declared type. With a bank, the step *is* the questions.
2. The three seeded Tier-1 steps are `watch` (their frontmatter is
   `step_class: icdev:Lesson`, which `_STEP_CLASS_TO_TYPE` maps to `watch`). Their
   type pane's only control is "✓ Understood → Continue", which completes the step
   in one click. Rendering both panes would put a way to pass a knowledge check
   without answering it directly beside the knowledge check — the pass-by-clicking
   this model exists to remove. A bank-bearing pane therefore owns completion.

Which steps take that path is decided by `assessed_step_ids`, derived in the
blueprint *from `steps_client` itself* rather than re-queried. If `open_attempt`
returned nothing — every item retired, or the table unavailable — the step keeps its
type pane, so the routing decision and the payload it renders from cannot disagree
and no learner is shown an empty form they cannot submit.

### Why this defeats both attacks

- **Reading the DOM:** `correct_index` is never serialised into any response. The
  options the browser holds are a permutation whose mapping lives only in
  `fa_step_attempts.served_json`.
- **Memorising:** a bank of ≥5 drawn 3 at a time, with per-attempt option shuffling,
  means the answer to "the second option on question two" is different next time.
  Memorising positions is worthless; memorising content is *learning*, which is the
  point.

---

## 7. Seeded content

Item banks live in `apps/forge_academy/content/item_banks/<mission-slug>.yaml`,
authored alongside the lesson they assess and seeded by `content_loader`.

This change seeds the three Tier-1 steps that are lessons with no verification test:

| Step | Bank | Why this one |
|---|---|---|
| `m01-llm-fundamentals` step 2 — Token Economics | 5 items | ungraded lesson on the Foundation path |
| `m01-llm-fundamentals` step 4 — Context Window Limits | 5 items | **its own content says "answer the question below to continue"** and there was no question |
| `m01-llm-fundamentals` step 5 — ICDEV LLM Router | 5 items | ungraded lesson on the Foundation path |

Every other Tier-1 step already has an authored `stepN_test.py` and is `graded` by
the coding path. Tier 1 is therefore now fully graded end to end, which is what makes
the `assessment_score_min` gate in §8 mean something rather than pass vacuously.

The remaining 32 reflect steps and 118 watch steps across Tiers 2–3 stay
`acknowledged`. That is not an oversight and it is not silently hidden: they report
`assessed: False`, they contribute nothing to the certificate score, and
`/api/academy/assessment/coverage` reports the shortfall as a number. Authoring
~600 items across nine role tracks is content work, and inventing it here — without
a subject-matter review per track — would produce exactly the fabricated-authority
problem `aca-hon-01` was filed for. `aca-trn-02` and `aca-trn-03` own it.

---

## 8. Certificate wiring

`assessment_score_min` is now enforced. `check_cert_eligibility` gains:

```
Gate: Assessment Score >= 70
detail: "Assessment score: 83% across 12 graded steps"
```

The score is the mean of the learner's **best** score per graded step, over graded
steps they have attempted. A learner who has attempted no graded step scores 0 and
does not pass the gate — the gate cannot be satisfied vacuously.

Because `collect_cert_evidence` snapshots `eligibility["gates"]` verbatim
(`aca-int-07`), the assessment figure lands in `fa_certificate_evidence` and appears
on `/academy/verify/<token>` with no further work.

---

## 9. Schema

Migration `324_fa_assessment_model.sql`, also declared in `db.SCHEMA`.

- **`fa_assessment_items`** — the bank. `(step_id, item_key)` unique. `correct_index`
  is server-only and must never be selected into a client payload.
- **`fa_step_attempts`** — **append-only**, registered in `APPEND_ONLY_TABLES`. One
  row per attempt, plus `kind='reset'` rows for instructor resets. An attempt ledger
  that can be edited is not evidence, and `fa_xp_ledger` cites it.
- **`fa_step_assessment_policy`** — per-step policy. A separate table rather than
  `ALTER TABLE fa_mission_steps`: `ADD COLUMN IF NOT EXISTS` is PostgreSQL-only and a
  bare `ADD COLUMN` breaks on re-run against SQLite, so a portable `CREATE TABLE IF
  NOT EXISTS` is the honest form. It also keeps policy separable from content, which
  the instructor workflow in `aca-trn-04` will need.

`fa_step_progress.score` now records the real percentage instead of `100 if passed
else 0`.

---

## 10. What shipped vs. what is deferred

**Shipped:** the model above, its constants, `assessment.py`, the three tables
(migration 324, `db.SCHEMA`, and `tests/conftest.py`), `fa_step_attempts` registered
in `APPEND_ONLY_TABLES`, the serve/grade path through `api_step_submit`, the
`/api/academy/assessment/coverage` endpoint, multi-item rendering in the new
`partials/_step_assessment.html` (mirrored into `icdev/`), the certificate gate, the
three Tier-1 item banks with their seeder (`content_loader.seed_item_banks`), and
`tests/test_academy_assessment_model.py` — 34 tests, one per success criterion in §1.

Verified end to end against migration 324 on a seeded database: all five
`m01-llm-fundamentals` steps classify `graded` (`coverage_report` → 100% for the
mission), re-seeding corrects in place rather than duplicating, and the served
payload contains only `item_key`/`prompt`/`options`.

**Deferred, by design:**
- Item banks for Tiers 2–3 — `aca-trn-02`, `aca-trn-03` (§7).
- The instructor UI that calls `reset_attempts` and sets policy — `aca-trn-04`.
  The functions and their audit trail ship here.
- Adaptive/IRT item selection. The draw is uniform-random. Adaptive selection needs
  item difficulty statistics, which need attempt volume, which needs this to ship
  first. `fa_assessment_items.difficulty` is authored and recorded but not yet used
  to select.
- Per-test partial credit for coding steps — needs `code_runner` to report per-test
  results (§3).
- xAPI/SCORM export of assessment results — `aca-trn-05`.

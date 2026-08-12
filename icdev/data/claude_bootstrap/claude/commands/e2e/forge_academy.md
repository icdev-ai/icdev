# E2E Test: FORGE Academy — a genuinely graded learner journey

Prove a learner can actually be **assessed** — not merely that pages render. The
academy's integrity work (the ACA epic) rests on one claim: submitting a
non-solution must be refused, and XP must only ever follow a verdict the server
reached itself. This spec drives that end to end in a real browser.

Every number below was observed on a live run against PostgreSQL, not invented.

## Prerequisites

- Flask dashboard running at the configured port, default `5050`
  (`ICDEV_DASHBOARD_PORT`). If another session owns `5050`, start your own with
  `python tools/dashboard/app.py --port 5099` and read `<PORT>` as your bound port.
  Do **not** start one from a git worktree: the scheduler auto-start keys its
  dedup heartbeat off `BASE_DIR`, so a worktree instance spawns a second kanban
  scheduler that does not see the pause sentinel.
- `ICDEV_FORGE_ACADEMY_ENABLED=true` (default) — the child app is registry-gated.
- A learner row in `fa_users`. Visiting `/academy` while authenticated creates one.
- Tier 1 needs at least one **gradeable** coding step: a step whose
  `test_code_path` resolves under `apps/forge_academy/content/`. This is the
  aca-hon-05 dependency — before it, Tier 1 had 10 gradeable steps out of 49 and
  this journey could not exist.

> **Screenshots (repo rule):** save as
> `playwright/screenshots/aca-e2e-<n>-<slug>.png`. Never point a native-runner
> `outputDir` at the `playwright/screenshots` root — it is wiped each run.

> **Failure handling:** on ANY failed assertion capture `page.content()` and a
> screenshot named `...-FAIL.png` BEFORE aborting. A 404 body proves the child app
> is not registered; a 403 on submit proves the CSRF token did not travel.

---

## Scenario 1 — the grader refuses a non-solution

The single most important assertion in this file. Before the integrity work, 42 of
49 Tier 1 steps could not be graded at all and one accepted anything.

1. Open `/academy/mission/m01-llm-fundamentals`.
2. Select the step whose `step_type` is `coding` and which is not yet completed —
   on the reference data that is step id **93**, "Temperature & Sampling",
   `xp_partial` 50, at sidebar index 2. Click it, or press Enter on it.
3. Record XP from `/academy/profile` before doing anything.
4. Leave the starter **exactly as shipped** — its body is `# YOUR CODE HERE` /
   `pass`, so `sample_responses()` returns `None`. Click `#run-btn-<idx>`.
5. Poll `#output-<idx>` until its text stops being `Executing...`.

**Assert:**
- `#output-<idx>` carries class `failed`.
- The panel shows the grader's own `AssertionError`, i.e.
  `sample_responses() returned None — did you implement it?`, sourced from the
  step's `test_code_path` — the client never supplies a test.
- XP on `/academy/profile` is **unchanged**. Observed: `1615` → `1615`.
- The sidebar item is **not** marked `done`.

## Scenario 2 — a real solution passes and is credited

6. Replace the `# YOUR CODE HERE` / `pass` body with an implementation that calls
   `simulate_temperature()` at 0.0, 0.5 and 1.0, prints each result, and returns
   the list of three dicts. Set it through the CodeMirror instance
   (`window.editors[<idx>].setValue(...)`) — writing to the hidden textarea does
   not reach the editor.
7. Click `#run-btn-<idx>` again and wait for the verdict.

**Assert:**
- `#output-<idx>` carries class `passed` and ends with the test's own
  `PASS: Temperature sampling implemented correctly.`
- XP **increases**. Observed: `1615` → `1715` (50 base, doubled by the speed bonus).
- The sidebar item is now `done`.
- The live region `#fa-live` announces the award. Observed:
  `⚡ Speed Bonus!: 100 XP earned` (aca-trn-06).
- Screenshot → `playwright/screenshots/aca-e2e-1-graded-pass.png`.

## Scenario 3 — XP reconciles to what the database stores

8. Read `fa_users.xp` and the `fa_step_progress` row for that step.

**Assert:**
- `fa_users.xp` equals the figure the profile page rendered. Observed `1715`.
- The step row is `status='completed'`, `score=100`, `hints_used=0`, with
  `completed_at` set and the learner's actual `submission` persisted — the
  evidence a certificate would later have to cite.

> **KNOWN GAP — do not assert a ledger yet.** The card asks for XP that
> "reconciles to the ledger". There is no ledger: a live schema probe returns no
> `fa_*xp*` table, so `fa_users.xp` is a running total with no per-event record,
> and a negative or duplicated award would be indistinguishable from a correct
> one after the fact. Building it is **aca-int-07**; extend this scenario when it
> lands.

## Scenario 4 — the hint price quoted is the price charged

Hints used to be billed twice: an instant deduction plus a submit-time multiplier,
so one hint on a 50 XP step really cost 58 XP against a button advertising 10.

9. Open a fresh step, note XP, click the hint button, wait for the panel to settle.

**Assert:**
- The panel quotes the real consequence, e.g.
  `this step now pays 27 XP instead of 75 (1 hint)`.
- XP is **unchanged** at hint time. Observed `1715` → `1715`. The submit-time
  multiplier is the single pricing mechanism (aca-int-06), so a learner who reads
  a hint and never submits is charged nothing.

## Scenario 5 — no floating chrome covers the primary action

The dashboard docks four fixed widgets the academy does not own. Assert by
**hit-testing**, not by eye: overlapping bounds and a blocked click are different
claims, and only the second one matters.

10. Scroll `.fa-main` fully to the bottom. For every `button, a.btn` inside it,
    compare against `assistant-fab`, `iqe-fab`, `iqe-minibar` and `clibp`, and run
    `document.elementFromPoint` at each control's centre and both edges.

**Assert:**
- Zero controls intersect the docked chrome.
- `elementFromPoint` at the left edge, centre and right edge of `#run-btn-<idx>`
  all return the button.

Regressions this catches, each of which shipped as a real defect:
- `iqe-minibar` over the bottom of the page with `body padding-bottom: 0` (ux-05)
- `assistant-fab` over the right corner of `Ask Sensei` (ux-05 follow-up)
- `clibp` over the **left 24px of Run** — found by this very journey, with
  `elementFromPoint` returning `clibp_tog`

## Scenario 6 — the page is clean and operable

11. Read console messages and drive the step list from the keyboard only.

**Assert:**
- **Zero** console errors. The CodeMirror vendor 404s are fixed; a 404 here means
  the vendored assets regressed and the editor silently fell back to a textarea.
- `.CodeMirror` exists — the editor initialised.
- Every `#step-nav li` has `role="button"` and `tabindex="0"`; Enter/Space opens a
  step, Arrow/Home/End move between them, and exactly **one** item carries
  `aria-current="step"` at any time.
- The CodeMirror input carries an `aria-label` — `fromTextArea` hides the labelled
  textarea and types into a private one, so the `<label for>` alone is not enough.

---

## Not yet covered

State plainly rather than implying coverage:

- **Reflect-step wrong answers.** `ASSESSED_STEP_TYPES` includes `reflect`, but
  this run exercised only `coding`. Extend once a Tier 1 reflect step with a
  scored rubric is seeded.
- **Certificate issuance and token resolution.** Requires the Tier 1 gates to be
  genuinely met by one learner; the reference learner has not completed enough
  missions. Pair with aca-int-07 so the certificate can cite verified evidence
  rather than a bare total.

# CUI // SP-CTI

# FORGE Academy — Assessment Integrity (ACA)

**Status:** shipped
**Project card:** `aca` — *ACA – FORGE Academy Assessment Integrity and Learner Experience*
(`args/projects.yaml`)
**Audit that opened it:** `/academy` run 2026-07-29 against the live PostgreSQL instance
(17 pages + 8 APIs probed, all HTTP 200, no crashes)
**Closed:** 2026-08-01 (`aca-vv-03`)
**Epics:** INT (7), HON (5), HYG (5+2), UX (7), TRN (6, partial), VV (3)

---

## The one-sentence problem

The Academy shell was large and well built — 124 missions, 212 steps, 18 templates,
certificates, guilds, leaderboards, an Oracle — and **almost nothing a learner did was
verified**. Everything that decided whether they passed was decided by their own browser.

## The one-sentence fix

Every authoritative decision moved to the server, into one module
(`apps/forge_academy/grading.py`), under a rule that reads:
**the graded party never supplies the grader, the verdict, or the amount.**

---

## Before / after

| | Before (2026-07-29 audit) | After |
|---|---|---|
| **Step verdict** | `passed = bool(data.get("passed", True))` — read from the request body, **defaulting to `True`** when omitted | `grading.grade_step(step_id, submission)` — the request carries no verdict, and there is deliberately no `test_code` parameter to pass |
| **Coding steps** | `mission.html` posted `STEPS[i].test_code` back and the runner graded against the client's copy; `run_code` passed any script exiting `0`, so a step with no stored test was cleared by `print(1)` (6 of 16 had no test) | Graded against the step's own `test_code_path`, loaded server-side. A coding step with no stored test is **`ungraded_no_test`** — refused, not passed |
| **Reflect steps** | `_step_reflect.html` submitted `passed=true` on a **wrong** answer; the key sat in the DOM as `data-correct` | Graded against the stored `config_schema_json`; `client_safe_steps()` strips the key so it never reaches the browser |
| **Verified steps** | at most **10 of 212** (4.7%) — 118 watch click-through, 42 configure auto-passing on fabricated output, 36 reflect passing either way | **49 graded coding steps, all 49 grade.** Non-assessed types report `assessed=False` — still completable, never described as an assessment |
| **Mission completion** | `complete_mission()` called on the client's own `mission_complete` flag | `mission_is_complete()` derived from recorded step progress |
| **Progress on GET** | opening a mission page called `start_mission()` → `SET status='in_progress', attempts=attempts+1`, which also **downgraded a completed mission on revisit**. `fa_step_progress` was empty while `fa_mission_progress` held 39 `in_progress` rows and 352 "attempts", every one a page view | GET never writes. Mastery is never withdrawn — a later failed experiment on a passed step records the submission without downgrading it. Migration 313 reconciled the page-view attempts |
| **Failed submissions** | filed as completions | `STEP_STATUS_ATTEMPTED`, no `completed_at`, no XP |
| **XP amounts** | `base_xp` / `mission_xp` taken from the request | `fa_mission_steps.xp_partial` / `fa_missions.xp_reward` |
| **XP provenance** | one running total in `fa_users.xp`, mutated by `SET xp = xp + ?` from 11 call sites, with nothing linking an award to the work behind it. **1715 XP, of which 1465 was 41 daily logins** — 85% of the rank was attendance, and rank was computed from the total | append-only **`fa_xp_ledger`** (migration 315): amount, reason, source type, source id, `is_attendance`, `verified`. `reason` is keyword-only with **no default**, so an unattributed award is a `TypeError` at the call site. Rank is computed from **earned** XP (migration 316) |
| **Certificates** | `check_cert_eligibility` computed the gates and discarded every detail; `/academy/verify/<token>` could only repeat the label back. One crafted POST per mission minted a certificate the public verify page vouched for | **`fa_certificate_evidence`** (migration 317) snapshots the gates, missions and verified steps **at issue time**, so the token is checkable by someone who was not there |
| **Hints / speed bonus** | client-reported; `hintsUsed` and `startTime` both reset in `goStep()`, so the penalty and the speed bonus laundered on navigation. The button quoted −10 XP when the real first-hint cost on a 50 XP step was −48 | counted and charged server-side; the panel quotes the price the submit will actually apply |
| **Tier gating** | `fa_users.tier_unlocked` set to `1` for every learner and enforced nowhere. All 104 Tier-2 missions listed and openable; the hub showed a "TIER 1" tile implying a gate that did not exist | enforced in `api_step_submit`, where credit is granted — see the decision below |
| **Configure handlers** | 5 of 7 returned invented constants presented as live ICDEV output (`ai_inventory` hardcoded `systems_found: 7`; `govcon_scan` returned a $4.2M opportunity closing in 18 days) and all returned `status: ok`, which is also what auto-passed the 42 configure steps | handlers that cannot reach live data say so, following the honest demo-mode pattern `rag_search` already used |
| **Coding assets** | `discover_steps` globbed `*.md` only, so **124 authored `.py` assets across 60 mission directories were invisible** — every Tier-1 step was `watch` with an empty `test_code_path` while `stepN_starter.py` / `stepN_test.py` sat unused in the same folder | 102 of 124 attach. A sibling **test** promotes a step to `coding`; a starter alone does not — promoting without a test manufactures a step that can never be credited. The remaining 22 have code but no prose and are pinned in `_ASSETS_AWAITING_PROSE` |
| **Reverse-direction test** | none — every academy test asserted what the system *accepts* | `tests/test_aca_vv_integrity_refusal.py` posts what an attacker would post, over the real HTTP routes, asserting on the response **and** the database |

---

## The tier-gating decision (aca-ux-04)

Two choices were taken here, and both are load-bearing enough to record.

### 1. The threshold is computed over **completable** missions, not the raw count

A "100% of the previous tier" rule makes the next tier **permanently unreachable**. Tier 1
contains `m-chat-agent-interview`, which has zero steps — deliberately *Coming Soon* per
fga-wire-06 — so it can never be completed. Only **12 of 13** Tier-1 and **95 of 104** Tier-2
missions are completable.

The percentage is therefore computed over completable missions, with `total` still reported so
the UI can explain the difference ("excluding N not yet authored"). The same latent bug sat in
`check_cert_eligibility`'s `tier1_complete` gate, which counted all 13 Tier-1 missions and so
made the Foundation certificate **unobtainable by construction**. Fixed with the same rule.

```python
TIER_UNLOCK_PCT = {2: 80, 3: 25}   # apps/forge_academy/constants.py
```

Tier 1 is absent from the map on purpose — it is the entry point and must never gate. Tier 3's
bar is deliberately much lower than Tier 2's because Tier 2 is a 95-mission role-track
catalogue that no single learner is expected to finish; a netops engineer has no reason to
complete the ISSM track. **Scoping the Tier-3 threshold to the learner's own role missions is
the better answer and is deliberately deferred** — it depends on exact-token role matching,
which landed separately in aca-hyg-02. That is the revisit condition.

`tier_progress()` computes from recorded completions rather than trusting the stored column
nothing maintained, treats a prior tier with zero completable missions as **open** so an empty
tier cannot permanently lock everything after it, and **returns unlocked on a query failure** —
never lock a learner out because of a database error.

### 2. Locked-but-readable, enforced at the point of credit

A locked mission renders in full, with a notice stating the requirement and the learner's
current progress and a link to the gating tier. It can be read and its code can be run. It
simply earns nothing: the submit returns `reason='tier_locked'` with no XP and no completion.

Curiosity is not punished, and — more importantly — **the gate is enforced in `api_step_submit`,
where credit is granted, not in the template.** Hiding a gate in a template after everything
else in this project moved authority server-side would have been the wrong shape.

aca-ux-06 (prerequisites on mission cards) follows from this decision: prerequisites are shown
as advisory markers with the card still navigable, because a second, stricter *blocking* rule on
the same screen would be incoherent with locked-but-readable. A test pins that.

---

## What the honesty work found that grading could not

Two defects surfaced only because someone wrote the reverse-direction suite (aca-vv-01). The
grading machinery was working correctly; the **content** was not.

1. **The Academy's front door graded nothing.** `m01-llm-fundamentals` step 1 — step 1 of
   mission 1 — had an auto-grader that defined its own `simulate_llm_call`, re-executed its own
   solution and asserted on its own output. It passed whatever the learner submitted, including
   an empty file. `run_code` concatenates learner + grader into one program, so the grader can
   see the learner's names and stdout; it now checks those. Verified in four directions: a
   non-solution fails, the **untouched starter** fails (its TODOs are the exercise), a correct
   solution passes, and a hardcoded fake response fails.

2. **Six graders could never pass.** `m-secops-05-aadc-threat-model`, `m-netops-pna-01`,
   `m-sre-xai-01`, `m-ace-03-multi-role-pipeline`, `m-readiness-01-eleven-pillars` and
   `m-readiness-02-remediation` imported `importlib`/`subprocess` or used
   `from step1_starter import ...` — all blocked by penta-aca-02's AST allowlist, which inspects
   the **combined** script. Content authoring and sandbox hardening had never been reconciled,
   so those steps were permanently uncompletable. All six were rewritten;
   `_GRADERS_BLOCKED_BY_SANDBOX` is now the empty set and the test fails both if a new one
   appears **and** if one is fixed without updating the list.

Measured across the catalogue at the time: of 49 graded coding steps, 42 correctly rejected a
non-solution, 6 were blocked as above, and 1 was vacuous. All 49 now grade.

## What was deliberately NOT built

- **aca-trn-03 (learning objectives).** The card's premise did not hold. Measured against the
  real content tree, frontmatter carries only `ontology_id` and `step_class` across all 212 step
  files, and just 16 files contain anything objective-shaped. Extraction would put an objective
  on ~7% of missions, which is worse than showing none. It needs authoring, or a decision to
  derive objectives another way — left in the backlog **with that correction recorded**, rather
  than half-built.
- **aca-trn-01/02/04/05** (assessment model, competency evidence chain, instructor/cohort
  workflow, xAPI/SCORM export) remain `scheduled`. They are product-shaped additions, not
  integrity defects, and none of them were blocking the integrity work.
- **A one-time rank-demotion notice** — see
  [forge-academy-aca-ux-07-rank-xp-split.md](forge-academy-aca-ux-07-rank-xp-split.md) for that
  decision and why re-litigating it is out of bounds.
- **Backfilling invented provenance.** Migration 315 reconstructs only awards with a surviving
  source row (daily logins, completed steps). The residual is written as a single
  `opening_balance` row flagged `verified=0` rather than distributed across plausible-looking
  reasons — a ledger that launders unknown history into confident entries is worse than no
  ledger, because it reads as evidence.

## Scope boundary (do not reopen)

All 17 `fga-*` and all 7 `penta-aca-*` tasks were already done before this project opened. PENTA
hardened the `code_runner` sandbox (AST allowlist, scrubbed env, `-I` isolation, rlimits — it is
genuinely solid and was left alone). FGA fixed the guild 500s, the fake watch-step Demo Output,
profile save, the Arena, the workflow-builder palette, and wired content discovery onto
frontmatter `ontology_id`.

**Retracted during the audit:** the 19 content directories with no matching DB slug are **not**
orphans — discovery is frontmatter-keyed by design, and `tests/test_fga_content_discovery.py`
proves coverage in the reverse direction. Likewise the 10 zero-step missions are the deliberate
outcome of fga-wire-06 and are tested.

---

## Verification

- **26 test modules** — `tests/test_aca_*.py` (25) plus the shared `tests/_academy_conn.py`
  helper, on top of the 15 pre-existing `test_fga_*` / `test_penta_aca*` suites.
- **`tests/test_aca_vv_integrity_refusal.py`** (aca-vv-01) is the anchor: route-level refusals
  for a forged `passed`, an omitted `passed` (the old default was `True`), client-supplied
  `base_xp`/`mission_xp`, a client-supplied test body, an untested coding step, a wrong reflect
  answer, the answer key never reaching the browser, a mission page view not touching progress,
  and certificate issuance without evidence. It aggregates over **every** graded step rather than
  a sample — which is how the vacuous front-door grader was found.
- **`.claude/commands/e2e/forge_academy.md`** (aca-vv-02) — a genuinely graded learner journey,
  run in a real browser against PostgreSQL first and written from what was observed. Every figure
  in it was measured. The untouched starter for m01 step "Temperature & Sampling" produced
  `class="failed"`, `AssertionError: sample_responses() returned None`, XP unchanged at 1615 and
  no sidebar tick; the correct implementation produced `class="passed"`, 1615 → 1715, a sidebar
  tick and a `#fa-live` announcement. `fa_step_progress` carried `status=completed`, `score=100`,
  `hints_used=0`, `completed_at` and the learner's actual submission — the evidence a certificate
  has to cite. Picked up automatically by `e2e_runner`'s mcp glob.

## Registration (aca-vv-03)

- Manifest shard: [`tools/manifest/forge-academy.md`](../../tools/manifest/forge-academy.md),
  indexed in `tools/manifest.md`.
- `fa_xp_ledger` and `fa_certificate_evidence` registered in `APPEND_ONLY_TABLES`
  (`.claude/hooks/pre_tool_use.py`) and `MINIMAL_ICDEV_SCHEMA` (`tests/conftest.py`).
- Academy tables recorded in [`docs/reference/databases.md`](../reference/databases.md).
- Sandbox decision (Gap 31) updated in
  [`docs/security/sandbox-coverage.md`](../security/sandbox-coverage.md) — the learner no longer
  supplies the test harness, so the code-execution ingress is strictly narrower than when the
  decision was first recorded.
- Template parity verified: 18 pages + 9 partials identical in `tools/dashboard/templates/`
  and `icdev/tools/dashboard/templates/`.

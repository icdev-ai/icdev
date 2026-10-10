<!-- CUI // SP-CTI -->
# mfx-mrg-03 — refusing a protected-path PR before the conflict arm: the survey

- **Card:** mfx-mrg-03 (`fix`), seeded from
  `docs/audits/task-det-9a62ee81a7-needed-a-human-resolution.md`
- **Date measured:** 2026-09-05, against the live PG board and the live forge
- **Re-derive:** `python -m tools.ci.protected_conflict_survey --json`
- **Verdict:** the refusal ships. Its reach was **narrowed by the measurement**,
  not by preference — see [What the survey changed](#what-the-survey-changed).

## The defect

`pr_watcher` asked the protected-path question in exactly one place on the
task-linked path: `_refuse_protected`, inside the **MERGEABLE** arm, immediately
before the un-draft and `_auto_merge`. `_maybe_rebase` and the resume ladder live
in the **`MERGE_CONFLICT`** arm.

That is not "later in one ladder" — it is a **different branch of it**. A PR that
is conflicting from the moment it opens never reaches the rung that would refuse
it, and the rung fires only once the PR becomes mergeable, which is precisely
when it is no longer needed to prevent wasted work.

Measured on PR #2064 (mfx-mrg-01), which changed `tools/ci/pr_watcher.py`, the
first entry in `protected_paths`:

| action | rows |
|---|---:|
| `pr_watcher.wait` | 96 |
| `pr_watcher.rebase_failed` | 63 |
| `pr_watcher.resume` | 5 |
| `pr_watcher.escalate` | 1 — *"resume cap reached (5/5) — manual intervention required"* |

**0 of those 165 rows mention `protected`.** The ledger, the AWAITING MERGE panel
and the escalation all named something else, and the escalation's stated reason
was never the reason.

A second, quieter instance of the same shape was found on the way. kpr-watch-05
placed the mergeable-arm refusal "AHEAD OF THE UN-DRAFT" for a written reason:
un-drafting is visible, hard to walk back, and burns the one brake a human still
has. A later fix (a green PR held behind a sibling was never taken out of draft)
moved the un-draft **up**, silently overtaking the guard — so every protected PR
reaching that arm was un-drafted ~200 lines before anything asked whether it
could ever be merged. The refusal is moved back ahead of it, which also puts it
ahead of the behind-main `_maybe_rebase`.

## Method

The card's own instruction: *"measure the change before shipping it: replay how
many currently-escalating PRs would be diverted, and confirm no PR that
legitimately rebases to green is diverted instead."*

- **Population.** Every `(task_id, pr_url)` with at least one `pr_watcher.rebase`,
  `rebase_failed`, `resume` or `escalate` row in `audit_trail` — the watcher's own
  ledger, no new writer and no second opinion. `resume`/`escalate` rows are
  counted only when the row's own `classification` is `merge_conflict`: a resume
  spent on `CI_FAILED` or `CHANGES_REQUESTED` is on a path this change does not
  touch, and counting it would credit the refusal with savings it cannot make.
  `wait` rows are excluded — a wait spends nothing.
  **210 episodes, lifetime.**
- **Predicate.** `merge_readiness.protected_hits`, the SHIPPED function, against
  the deployed `protected_paths` in `args/pr_watcher_config.yaml`. Never a second
  copy — a survey that re-implements the rule measures the copy.
- **File lists.** `gh api repos/{repo}/pulls/{n}/files --paginate`, i.e. **REST,
  never GraphQL**. The outage recorded beside this card refused every `gh pr view`
  (GraphQL) while `gh api repos/.../pulls/N` answered normally, so a survey built
  on the GraphQL door cannot be run on the day it is most needed.
- **`unmeasurable`.** A PR whose file list the forge will not return is counted
  separately and folded into neither side. The production predicate is
  fail-closed; reading an unreadable PR as clean would understate the fire rate of
  the very control being measured. **0 unmeasurable on this run** — every one of
  the 210 answered.

## Results

```
conflict episodes      : 210 (examined 210)
measured / unmeasurable: 210 / 0
would hold             : 32 (15.24%)
unchanged              : 178
spent on held          : 13 rebase, 125 rebase_failed, 74 resume, 6626 escalate
```

`escalate` is 6,626 **audit rows** across 21 episodes, not 21 escalations: rows
predating the escalate-once fix repeat per poll (kax-obs-02 alone carries 1,233).
Both numbers are reported so neither can be quoted as the other.

### Shape A — hold ahead of `_maybe_rebase` (the card's *suggested* shape)

The card labelled this "**NOT a decision -- survey first**", and the survey
refutes it.

| | |
|---|---:|
| episodes held | 32 |
| **false positives** (a held episode a pushed rebase repaired) | **11** |
| of which a single pushed rebase and **nothing else** before the PR merged | **8** |
| false positives as a share of the population | **5.24%** |
| successful rebases lost | 13 |

The eight are `rb=1, rbf=0, res=0, esc=0`, all merged:
**#1724** (hcx-evt-01), **#1734** (rem-hyg-02), **#1751** (rem-hyg-07),
**#1789** (rem-hyg-06), **#1821** (kpr-watch-06), **#1682** (trust-hitl-01),
**#1686** (trust-hitl-03), **#1695** (trust-disc-05).

8/210 = **3.81%** of the population is work the automatic rebase repaired at zero
human cost, which Shape A converts into a manual rebase for no gain. That is above
the **1.63%** CLAUDE.md already calls refusing routine work, and this repository's
standing rule is unambiguous: *a control that stops work it was never meant to
stop gets switched off.*

### Shape B — SHIPPED

Ask and audit the refusal **before any `_maybe_rebase` call**, let the bounded
rebase run, hold **before the resume ladder**.

| | |
|---|---:|
| episodes held | 32 |
| **false positives** | **0 — by construction** |
| rebases preserved | 13 |
| resumes saved | 74 |
| escalated episodes saved | 21 |

The rebase is the one rung on that arm the survey measures **repairing** these
PRs. Everything below it — the resume ladder and the escalation — cannot produce
a merge this watcher is permitted to perform, because `_refuse_protected` refuses
in the mergeable arm and `_auto_merge` refuses again as its last line. A resumed
agent's best possible outcome on a protected PR is a PR that still waits for a
human, so five LLM resumes, a HITL alert and an escalation announcing a spent
resume cap are all spent describing something else.

## What the survey changed

The acceptance criterion asked for the refusal *"before any `_maybe_rebase`
call"* **and** for *"no PR that would legitimately rebase to green"* to be
diverted. On this data those two clauses are in tension, and the second one is
the one the standing rule protects. The shipped rule keeps both as far as they
can both be kept:

- the question is asked, and `protected_path_hold` written, **before any
  `_maybe_rebase` call** — so the ledger states the real reason from the first
  poll, which is the defect;
- **no resume is ever consumed** and no escalation is ever raised;
- the **rebase budget is unchanged** (`max_rebase_attempts_per_task: 2`,
  `max_resume_cycles_per_task: 5`, `auto_rebase_on_conflict: true` — pinned by
  `test_the_rebase_and_resume_budgets_are_unchanged`);
- **zero PRs that would legitimately rebase to green are diverted.**

`tests/ci/test_protected_conflict_hold.py::test_the_bounded_rebase_is_still_attempted`
exists so that this finding survives somebody later "tidying" the hold up one rung.

### The two opposite defaults, and why both ship in one file

| | `_protected_hits` | `_protected_hits_seen` |
|---|---|---|
| answers | "may this PR be merged" | "should the watcher stop spending resumes on it" |
| unreadable file list | **fail-CLOSED** — reads as protected | **fail-OPEN** — `None`, unmeasured, unchanged ladder |
| why | a merge gate that opens when it cannot see is not a gate | stopping is not merging; stopping on an unreadable listing holds work the ladder would legitimately have repaired |

`_protected_hits_seen` reads only the open-PR index `poll_once` already fetched
for the sibling map, so the hold costs **no extra `gh` call** — on a door the
measured outage behind this card was refusing.

The same reasoning applies to the mergeable arm. Moving that rung up past the
sibling / landed / behind-main holds means PRs that used to `continue` before
reaching it now reach it, and `_refuse_protected` costs one `gh pr list` per
poll. So the index already in hand is asked first and only for its one
unambiguous answer — a PR **present** in it is measured (the index is keyed by
url, so a present entry can only be that PR's), and a measured-clean answer
needs no second call. Anything else — absent, unreadable, or a hit that must be
audited — takes the fail-closed path exactly as before. Net effect for a clean
mergeable PR: **two** `url,files,mergeable,isDraft` listings per poll (the
sibling map and `_auto_merge`'s own chokepoint) where the pre-change tree made
**three**, pinned by
`test_a_clean_mergeable_pr_costs_no_extra_forge_listing`.

## Not addressed here, and named

**Cause 2 of the seeding record — the per-base-era rebase budget is not a ceiling
on a busy repo.** #2064's 63 `rebase_failed` rows fall into **30 distinct base
eras with exactly 2 failures in each** (29 × 2, one × 1): main landed 30 times in
~24h and refunded the budget 30 times. Under the shipped rule those 63 attempts
would still have been spent — the refusal stops the resume ladder, not the rebase
rung, and the survey is why. The card's own instruction stands: *do not raise the
rebase budget to quieten this — the budget is not the defect.* Bounding a
recurring-conflict rebase loop is a separate change with its own survey.

## Scope deliberately not taken

`CI_FAILED` and `CHANGES_REQUESTED` resumes on a protected PR are **untouched**.
Those resumes fix code; a human still performs the merge, and the measured defect
is the conflict arm. Widening to them would need its own survey.

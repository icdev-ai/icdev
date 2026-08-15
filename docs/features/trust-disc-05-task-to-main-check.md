# CUI // SP-CTI

# trust-disc-05 — check task → main before task → PR

## The defect

The board tracks **task → PR**. Nothing checked **task → main**.

Measured on the live board 2026-08-15: two of the five cards sitting in
`pr_opened` had their work **already merged under a different PR number**.

| Task | Actually landed as | Still open against it |
|------|--------------------|-----------------------|
| `ctx-perf-02` | #1641 | #1646 |
| `ctx-trust-02` | #1638 | #1651 |

That is why both conflicted: they re-apply changes already present, against
files that have since moved on. #1651's diff against main was **-38/+26 on
`rest_v1.py`** — merging it would have DELETED 38 lines main currently has. A
revert wearing a feature's clothes, and every gate on the board reported green,
because every gate on the board asks about the PR.

A third card, `ctx-enf-01`, had **two PRs open at once** (#1640 and #1647). Only
the one on `kanban/<task_id>` can settle the card: the done-gate resolves a
task's work by branch NAME, so work landed from a rival branch leaves the card
stranded even after it merges.

## What shipped

`tools/kanban/landed_check.py` — one `git log -E --grep` against
`origin/<default>`, plus the rival-PR half. Registered in
`tools/manifest/kanban.md`, mirrored to `icdev/tools/kanban/`.

```bash
python -m tools.kanban.landed_check --task ctx-perf-02 --json
python -m tools.kanban.landed_check --all --json           # non-terminal tasks
python -m tools.kanban.landed_check --all --status done --no-prs --json   # the survey
python -m tools.kanban.landed_check --task <id> --gate     # exit 1 on a finding
```

### Tiered evidence — the part that makes it usable rather than noisy

| Tier | What it means | Blocks? |
|------|---------------|---------|
| `merge_ref` | a merge commit names a branch carrying the id (`Merge pull request #1647 from icdev-ai/kanban/ctx-enf-01`) | yes |
| `subject` | the id is in the commit SUBJECT (`fix(cortex): … (#ctx-trust-02) (#1638)`) — the house convention, and the only thing that survives a squash merge | yes |
| `body` | the id appears **only** in the body | **never** |

`body` does not block because a body mention is a citation at least as often as
a landing. Commit `a758250c0` says *"that is exactly the defect ctx-trust-02
removed"* while implementing `ctx-reach-03`. A gate that read that as a landing
would be confidently wrong about which commit did the work — and confidently
wrong is worse than silent.

### Boundary matching

`-` and `_` count as word characters, so:

* `ctx-perf-02` does **not** match `ctx-perf-021`
* a parent id does **not** match its decomposed children (`dwo-mcp-03-d5` vs
  `dwo-mcp-03-d5-d1`)

Strict on purpose: a false "already landed" stops real work, while a miss leaves
today's behaviour exactly as it was.

### Fail-open, but never falsely clean

No git, an unresolvable `origin/<default>` (a repo that never fetched), a
non-id-shaped id — all report `checked: false`, never "nothing landed". This is
the same discipline `chain_sweep` uses for `pre_cutover` and
`capability_consumption` for `telemetry_available: false`: an unavailable check
must not be able to read as a passing one.

## Where it is wired

| Seam | File | Behaviour |
|------|------|-----------|
| SEED | `tools/kanban/task_factory.py::create_tasks` | one bulk git call for the batch, **before any insert**, so a refusal cannot half-land a batch |
| DISPATCH | `tools/genesis/reflexes/kanban.py::_write_prompt_file` + the pre-dispatch loop | the banner goes **above** the description — the session reading that prompt is the one that would re-implement merged work |
| PR OPEN | `tools/genesis/reflexes/kanban.py::_push_branch_and_open_pr` | evidence goes in the PR body, where the human deciding whether to merge #1651 would have seen that its -38/+26 was a revert |

Answers are memoised per scheduler cycle (`clear_landed_cache()` at the top of
`run()`) — two subprocess calls per dispatch is one too many, and a memo that
outlived its cycle would report yesterday's merge state as today's.

## Enforcement posture: advisory by default

`KANBAN_LANDED_CHECK` = `off` | `warn` (default) | `enforce`.

Default is `warn` because the first thing a new gate must do is be **measured**.
This repo has shipped the other way: eleven PreToolUse checks spent months
printing `BLOCKED:` behind a `|| true`, and the one that had shipped as a hard
block turned out to refuse one call in forty.

**Survey, 2026-08-15** (live board + 7,347 commits of `origin/main`):

* 3,176 `done` tasks swept in 56.8s (22 chunked `git log` calls).
  **1,003 (31.6%)** found on main — 545 `merge_ref`, 458 `subject`. That is
  COVERAGE, not error rate: the other 68% landed by squash merge with no task id
  in the subject, which this check misses by design.
* **207 (6.5%)** matched on body only, and therefore did not block. The sample
  shows why: `dm-prod-02`, `dm-domain-02` and `dm-contract-02` all matched the
  same multi-task commit body.
* Fire rate on the population the gate actually sees (the 10 non-terminal tasks
  that day): **0**.

Re-run the survey before ever defaulting this to `enforce`.

### One thing deliberately not done

`enforce` mode skips a dispatch but does **not** change the task's status. There
is no board status meaning "held pending human verification": `kanban_tasks`'
CHECK constraint has no `blocked` (`state_machine.py` carries that migration as
an open TODO), and every status that does exist lies about what happened —
`failed` says the work was attempted, `backlog` says nobody has got to it. The
task stays put and says so every cycle.

## Tests

`tests/kanban/test_landed_check.py` (29) builds a **real git repository per
case**. The defect class is "what git actually says vs what the board believes",
so a test that stubbed git out would be testing the belief.
`tests/kanban/test_landed_check_wiring.py` (9) pins the three consumers, so the
check cannot degrade into a correct answer nobody acts on — this platform's
signature defect. Both gated in `args/ci_test_files/core.txt` in this PR. 38
tests, ~4s, no network, no gh, no board.

One trap worth recording, hit while writing them: `import tools.db.storage as s`
does **not** bind the same module object that `from tools.db.storage import
get_connection` reads from — the `tools` shim resolves the former to
`icdev.tools.db.storage` while `sys.modules['tools.db.storage']` stays distinct.
Patching only the first let a real INSERT through against the live PostgreSQL
board. The fixture patches every alias.

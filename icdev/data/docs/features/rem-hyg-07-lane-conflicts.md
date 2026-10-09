# CUI // SP-CTI

# rem-hyg-07 — Sibling file contention, detected at seed time

## The gap

`tools/ci/pr_watcher.py` already answers "will these two branches fight over the
same file?" — `_open_pr_files()` plus `hold_on_sibling_conflict`. It answers it
about **open PRs**, which is after both sessions have already built. By then the
cheapest outcome is a rebase and the usual one is a binned branch: #1684
dispatched a producer and its consumer together, the loser's PR was unlandable,
and 1,058 lines were discarded.

The prevention has to happen where the collision is still two rows in a batch
rather than two branches with work on them.

## Measured on the live board

2026-08-16, 44 non-terminal tasks: 39 named at least one non-coordination source
file, and 54 pairs shared a file with **no dependency path between them** — 16
of them dispatchable simultaneously. Two were serialized by hand that day
(`kpr-stale-02` vs `kpr-watch-01` on `tools/ci/pr_watcher.py`; `rem-cap-05` vs
`rem-cap-01` on `tools/awareness/capability_consumption.py`) and a four-way
contention on `capability_consumption.py` had already turned PR #1730 DIRTY.

## What shipped

`tools/kanban/lane_conflicts.py` reads every non-terminal row, extracts the file
paths its description names, builds the dependency closure, and reports
unserialized pairs sharing a non-coordination file.

**Both dependency mechanisms are read** — the scalar `depends_on_task_id` and
the `kanban_task_deps` junction — because `_deps_satisfied` ANDs them. Either
alone is a serialization, so consulting one would report a hand-serialized pair
as a live race.

**Ranked `live` vs `latent`.** A backlog task whose dependency is unsatisfied
cannot race today. Reporting the two identically is how a real finding gets lost
in noise.

**Gate sentinels are excluded** via `gates.is_manual_gate` — a `<prefix>-gate-<n>`
row is never built, so a path in its `RISK:` description is not contention.

**The exclusion list is shared, not copied.** `tools/git/coordination_paths.py`
now holds the coordination/generated path lists that `pr_watcher` curated, and
both import it. A second divergent copy is worse than none: the seed-time check
would report a collision the merge-time check waves through, and no reader could
tell which list was current. `pr_watcher` keeps `_is_additive_path` /
`_is_generated_path` as re-exports.

## The honest part: prose is a heuristic

Distinguishing "this task will **write** this file" from "this task **mentions**
this file" is the whole difficulty, and prose does not carry that distinction
reliably. Two evidence grades are reported and never merged:

- **`prose`** — parsed from the description. Available at seed time, which is the
  only time it helps, and inherently a guess.
- **`branch`** — `git diff --name-only origin/main...kanban/<id>`, exact, but it
  only exists once a branch does. `--from-branches`.

Where a task has a branch its paths **replace** the prose guess rather than
joining it: the exact answer must not be diluted by the heuristic one.

Each branch is compared to `origin/main` and **never to another branch**.
`git merge-tree` between two task tips reports conflicts the forge will never
see, because the forge merges each branch into main in sequence — measured
2026-08-16, `hcx-live-02` vs `hcx-live-03` reported CONFLICT while against main
`hcx-live-03` was CLEAN and only `hcx-live-02` was dirty.

## Six suppressions, all found by running it

The brief named two false-positive classes as mandatory. Running the check
against the live board found a third, and the first pass produced **three `live`
findings of which none were real** — including one pairing rem-hyg-07 against
`hcx-live-02` over a file rem-hyg-07 only cites.

| Class | Why it is not a claim | Found on |
|---|---|---|
| `command` | `python tools/ci/red_first_gate.py --gate` is a tool to RUN | nearly every row |
| `citation` | an explicit `see X` / `per X` cross-reference, or a `docs/` path with no write verb on its line | rem-hyg-04 |
| `evidence` | a specimen inside a caps-led `MEASURED`/`OBSERVED` paragraph | rem-hyg-05, rem-hyg-07 |
| `precedent` | `Follow args/ci_test_backlog.txt` — a model to imitate elsewhere | rem-hyg-05 |
| `negated` | `Do NOT change ...` | rem-tst-01 |
| `coordination` | many branches legitimately co-edit it | shared list |

Two decisions worth keeping:

- An **evidence paragraph is not rescued by a write verb**, unlike the `docs/`
  rule. Such a paragraph narrates writes that already happened ("added",
  "landed"), so honouring one would defeat the rule on exactly the sentences it
  exists for. Claims live under `DO:`.
- The evidence marker must be **capitalised and lead the line**. Lower-case
  "measured" mid-sentence is ordinary prose; silencing every sentence containing
  the word turns a suppression into a blindfold.

Proximity for `see X` and `Follow X` is measured in **characters, not words**,
and a sentence break is a period followed by *whitespace* — never a bare period,
because every path this module looks at contains a dot. Excluding the dot itself
truncated the window at `.txt` and silently claimed the second path of
`Follow args/ci_test_backlog.txt + args/test_gating_gate.yaml`.

**Result on the live board: 3 live / 14 latent → 0 live / 8 latent**, every
survivor matching a contention identified by hand — including a genuine one
against this task on `tools/kanban/task_factory.py`.

## Report only

Wired into `task_factory.create_tasks` as a fourth pre-insert check, in the same
shape as the three already there: evaluated **before any insert** so a future
refusal cannot half-land a batch, and **fail-open** on any error.

Arming it needs a fire-rate survey first, exactly as rem-hyg-03/04 do for the
identity check. CLAUDE.md is explicit, and the PreToolUse hook is the precedent:
a check enabled without a measured rate is unmeasured, not proven.

## Known limit

Without a cross-reference marker, a single-line paragraph that merely names a
file and edits something else still over-claims. That hole is pinned by a test
rather than papered over, so a reader sees the edge of the heuristic instead of
assuming prose parsing is solved. Where it matters, use `--from-branches`.

## Usage

```bash
python -m tools.kanban.lane_conflicts --json
python -m tools.kanban.lane_conflicts                  # table grouped by shared file
python -m tools.kanban.lane_conflicts --live-only
python -m tools.kanban.lane_conflicts --from-branches
python -m tools.kanban.lane_conflicts --task rem-hyg-07
```

Tests: `tests/kanban/test_lane_conflicts.py` (44, ~1s, no board/network/LLM).

# diag-2710647d28 — RCA: `task-det-d42b032ea5-r3` "stuck in loop"

Verdict: **no code defect. The loop was a retry storm against a finding whose fix had
already landed, and each retry ended `no_commits` because there was nothing left to change.**

## What was measured (2026-09-20)

| Question | Answer | How to re-check |
|---|---|---|
| Is the declaration the finding asked for on `main`? | Yes — `args/red_first_gate.yaml` under `union_resolver.files` in `args/pr_watcher_config.yaml` (`keep_both_blocks, adjacent_edits`), landed by `089931fab` (PR #2298, the `-r2` retry). | `grep -n "red_first_gate" args/pr_watcher_config.yaml` |
| Does the derivation still list it as a candidate? | No — it now appears only under `collateral` (declared, 6 historical refusals). | `python -m tools.kanban.union_candidates --window-hours 720 --json` |
| Is finding `d42b032ea51b8d17` cleared? | Yes. | `python -m tools.kanban.detector_findings --list --status cleared` |
| Branch of `task-det-d42b032ea5-r3` ahead of main? | 0 commits (evidence snapshot `branch_commits_ahead: "0"`). | `git rev-list --count origin/main..<branch>` |

## Why it looped

`-r2` fixed the finding and merged. `-r3` was still dispatched, its worker correctly found
nothing to edit and said so, and the verifier's rule "a task branch with 0 commits ahead is
a failure" classified that as `no_commits` — a class the remediation table marks
"not auto-remediable", so `self_debug` filed this card. The signature recurred because the
*condition that made the work unnecessary* (fix already on main) is not something the
verifier consults; it only counts commits.

## What deliberately did not change

- No edit to `args/pr_watcher_config.yaml`: the declaration is present and correct; re-touching
  it would create a commit whose only purpose is to satisfy the counter.
- No verifier change in this card: teaching `no_commits` to consult `landed_check`
  (task -> main, `python -m tools.kanban.landed_check --task <id> --json`) is the right
  follow-up, but it is a behaviour change to the completion gate and belongs on its own card
  with a survey of how many past `no_commits` failures were "already landed".

## Disposition

Close as diagnosed. This note is the committed artifact for the card.

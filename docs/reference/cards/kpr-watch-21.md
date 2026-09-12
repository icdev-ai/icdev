# CUI // SP-CTI

# kpr-watch-21 — A refusal that names no remedy

**Measured 2026-09-12.** `pr_watcher.wait` carried **10,542** lifetime rows whose
reason was `enforced gate: awaiting ICDEV done-verification`
(`tools/ci/pr_watcher.py`). The sentence was accurate and it named **no remedy**,
so a reader — human or agent — learned that the door was shut and not how to open
it.

It cost a cycle in the session that filed this card. Landing `xrv-cost-05` through
`cli.py --set-status <id> done --merge` refused on that exact rung with seven of
eight gates green. The command that clears it is `cli.py --reverify <id>`, which
re-derives delivery from git and appends the verification the gate is waiting for.
Nothing in the refusal, and nothing in `--help`, said so; it was recovered from a
session memory. An operator without that memory reads "awaiting ICDEV
done-verification" as *wait*, and waiting never clears it — nothing writes a
`kanban_verifications` row except a dispatch, so the verification is something you
**run**, not something that arrives.

The precedent was already in this tree and this rung simply did not follow it:
mfx-ci-04's bootstrap refusal names its repair (`git add CLAUDE.md
icdev/data/claude_bootstrap/CLAUDE.md`), the census gates print the exact
`--prune`/ratchet command, and `restore_acts` names the undo for every act.

**Scope was a MESSAGE, not a gate.** Nothing about *when* a door refuses changed:
the predicates are identical, the fail-closed posture is identical, and 10,542
rows is the gate working. `tests/ci/test_done_gate_names_its_remedy.py` asserts
both halves — that each refusal still fires on the same input, and that it now
names what to run.

## The remedy is not the same remedy in every branch

`--reverify` is right for three of the four done-gate holds and **wrong** for the
fourth. `pr_watcher.reverify_is_allowed` refuses to refresh a
`review_passed=false` row, because a re-verification writes `review_passed` NULL
and `_enforced_done_ok` reads NULL as "not judged, allowed" — a refresh would
launder a real conformance failure. Naming `--reverify` there would send a reader
to a command designed to refuse them, so that branch says so explicitly and names
the audited bypass instead. The `verification unreadable` branch does not name it
either: that is a storage fault, and `--reverify` reads the same store.

## The audit — all thirteen rungs, in one pass

`tools/kanban/land.py` has **13 named checks** (`pr_recorded`, `pr_readable`,
`pr_open`, `base_is_default`, `mergeable`, `no_changes_requested`, `ci_green`,
`approved`, `enforced_done_gate`, `no_sibling_conflict`, `draft_promoted`,
`merge_requested`, `merge_confirmed`) reached through **17 `_refusal()` sites**.
One of those sites — `enforced_done_gate` — does not author its own sentence: it
passes through whatever `_enforced_done_ok` returned, which is **5** distinct
refusals. So the surface a reader can actually hit is **21 distinct refusal
messages**, and every one of them was read.

**Counts: 5 already named a remedy, 9 were fixed here, 7 cannot name one.**

### Already named a remedy (5) — unchanged

| Rung | Refusal |
|---|---|
| `pr_recorded` | "no PR is recorded for this task … — **open a PR first**" |
| `mergeable` | "PR is CONFLICTING — **rebase it onto the default branch first**" |
| `no_changes_requested` | "a reviewer requested changes — **address the review first**" |
| `ci_green` (in progress) | "CI is still running — **wait for it to finish**" |
| `merge_confirmed` | "…gh may be queueing it behind branch protection … **re-run once it lands**" |

### Fixed here (9) — message only

| Rung | Remedy now named |
|---|---|
| `enforced_done_gate` — no verification row ★ | `python tools/kanban/cli.py --reverify <id>` (and that the row will not arrive on its own) |
| `enforced_done_gate` — `result=failed` | `--reverify <id>` |
| `enforced_done_gate` — not yet passed / pending | `--reverify <id>` |
| `enforced_done_gate` — `review_passed=false` | that `--reverify` will **NOT** clear it; address the finding and re-dispatch, or `--force-done --reason '<why>'` |
| `enforced_done_gate` — verification unreadable | that it is a **storage** fault and not a task state; check the database is reachable |
| `base_is_default` | `gh pr edit <pr_url> --base <default_branch>` |
| `ci_green` — red | `gh pr checks <pr_url>` names the failing check |
| `ci_green` — no conclusive rollup | `gh pr checks <pr_url>` shows what has reported; push a commit if nothing has |
| `no_sibling_conflict` — held | the lowest-numbered mergeable sibling goes first: land it, rebase, re-run |

★ is the 10,542-row refusal this card was filed for.

### Cannot name a remedy (7) — reported, deliberately unchanged

| Rung | Why there is nothing to name |
|---|---|
| task lookup failed | A storage/lookup fault. The exception text **is** the finding; there is no command that repairs an arbitrary one. |
| `pr_readable` | The forge or `gh` failed. Nothing to run but the same call again. |
| `pr_open` | The PR is CLOSED or already MERGED. There is nothing to land, so there is nothing to fix. |
| `approved` | The remedy is a **person** approving. It already names the lever (`auto_merge_require_approval`); turning that off is a policy change, not a repair. |
| `no_sibling_conflict` — listing unreadable | A `gh` listing fault, same class as `pr_readable`. |
| `draft_promoted` | It already names its three causes (manual gate, unsatisfied dependency, `auto_ready_draft_prs` off). The repair differs per cause and is a board or config change, not one command. |
| `merge_requested` | `gh pr merge`'s own stderr is the finding. |

There is no "look at the failing check" left in the fixed column that does not
also name the command that shows it — that distinction is the whole point of the
split above: a rung either hands the reader a command, or it says honestly that
the next step is judgment rather than a keystroke.

## What did NOT move

No threshold, no budget, no gate condition. `KANBAN_PIPELINE_ENFORCE` still
governs whether the done-gate applies at all; `_enforced_done_ok` still returns
`False` for a missing, failed, unreadable or unpassed verification and `True` only
for `pass`/`passed`/`bypassed`; `reverify_is_allowed` is untouched. The reasons
keep their `enforced gate:` prefix, which is what `tools/ci/merge_stall.py`
classifies on (`CAUSE_DONE_GATE`).

`--reverify` is also now documented where a reader looks **before** the refusal:
the `--merge` help text in `tools/kanban/cli.py` and the usage examples in its
module docstring.

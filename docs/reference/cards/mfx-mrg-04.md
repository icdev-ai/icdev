# A protected-path PR lands through the DOOR, with an audited reason (mfx-mrg-04)

> Moved verbatim from CLAUDE.md on 2026-09-12 (xrv-docs-02). The prose is the card's own record; only the leading `#` comment markers were stripped and the command runs fenced.

```bash
python tools/kanban/cli.py --set-status <id> done --merge --protected-ok --reason '<why>'
python tools/kanban/cli.py --set-status <id> done --merge --protected-ok --reason '<why>' --dry-run
```

`pr_watcher` REFUSES to merge a PR touching `protected_paths` -- the guard
that stops the merge ladder auto-merging a change to ITSELF. That refusal is
CORRECT and stays. THE PROBLEM WAS THE ONLY AVAILABLE OVERRIDE. Measured
2026-09-05/06: three PRs needed a human merge and got one -- mfx-mrg-01
(#2064), mfx-boot-01 (#2066), mfx-sib-03 (#2070) -- every one touching
tools/ci/pr_watcher.py, and the sole way through was ICDEV_GH_PR_MERGE_GUARD=0
plus a raw `gh pr merge`, which stands the guard above down for EVERY kanban PR
in that shell and runs NONE of land.py's thirteen checks. In the same session a
merge attempt on #2070 was refused by GitHub's own branch policy because 12
checks were STILL RUNNING; land.py's `ci_green` refuses that case too (and a
failed check, and an EMPTY rollup), so the blunt override would have taken
exactly that safety away. An operator who wants ONE protected PR landed should
not have to disarm the guard for all of them.
THE SIBLING TIE-BREAK IS HALF THE RULE, AND THIS DOOR WAS RUNNING THE OTHER
HALF. `hold_on_sibling_conflict` SERIALISES merges over a shared source file
-- merge one, let the rest rebase -- and sharing is SYMMETRIC, so holding
every sibling is not serialisation, it is the stall
`pr_watcher._wins_sibling_tiebreak` was written to break (its docstring: 14
AGOV PRs, the whole board at "awaiting merge", zero active tasks; LOWEST PR
NUMBER WINS, which is the oldest PR and is deterministic across processes).
`pr_watcher` consults it; `land.py` reused `_sibling_conflicts` and NOT the
function that RESOLVES what `_sibling_conflicts` reports, so two
implementations of one policy disagreed -- the watcher would merge the
lowest-numbered sibling while the door refused that same PR. MEASURED
2026-09-06 on THIS card's own PR #2143: lowest of its set
{2143, 2145, 2154, 2156}, first in the queue by the policy's own rule, held by
the door alone with every other check green. The door now reads
`_open_pr_index` (the same gh call already carries `mergeable`/`draft`), drops
siblings `_pr_can_merge` says the forge would refuse, and holds only when the
tie-break says this PR LOSES. NOT a widening: at most one PR in a set can be
the lowest-numbered, so two PRs sharing a file still cannot merge together --
asserted, along with an AST test pinning the door to the WATCHER'S functions
so a future edit cannot re-derive "lowest number wins" locally.
ALL THIRTEEN CHECKS STILL RUN. The flag overrides EXACTLY ONE rung --
`_refuse_protected`, the first statement of `pr_watcher._auto_merge` -- and
nothing else. pr_recorded, pr_readable, pr_open, base_is_default, mergeable,
ci_green, approved, no_changes_requested, no_sibling_conflict, draft_promoted,
enforced_done_gate, merge_requested and merge_confirmed are untouched, `done`
is still written only after the forge CONFIRMS the merge, and the HOLD LABEL is
a different guard that still refuses.
A REASON IS REQUIRED, non-empty, the `--force-done --reason` precedent: a usage
error, never a default string.
AUDITED BEFORE THE MERGE, FAIL-CLOSED (`log_event(raise_on_error=True)`,
event type `kanban.protected_merge_override`, migration 20260906120818),
naming the paths the PR ACTUALLY hit -- re-derived from the open-PR listing at
the moment of the decision, never the configured list -- and the reason
VERBATIM. NO ROW, NO MERGE: an unaudited override of a self-protection control
is indistinguishable from the defect it guards against. prove -> audit ->
apply -> confirm, restore_acts' ordering; the outcome row
(`.merged` / `.not_merged`) is written after and is best-effort, because by
then there is nothing left to refuse. On a PG board that has not run the
migration the CHECK refuses the type and EVERY override is refused -- the
correct reading, not an obstacle.
THE AUTONOMOUS PATHS CAN NEVER SET IT. `protected_ok` is keyword-only,
defaults False, and is threaded ONLY from the CLI through
`tools/kanban/land.py`. The poll loop and the unlinked sweep do not pass it,
and tests/kanban/test_protected_merge_override.py reads pr_watcher.py's AST
(both spellings) to keep it that way -- a behavioural test would still pass for
a future edit that threads the flag through the cycle, which is the one change
that turns this door back into the hazard kpr-watch-05 closed.
Do NOT add ICDEV_GH_PR_MERGE_GUARD=0 to .claude/settings.local.json instead. It
was considered and REFUSED: gitignored, so it never reaches the public repo,
but it disarms the guard for every kanban PR in every session on the machine --
far wider than the one case that needs it, against a surveyed fire rate of
0.1009%, a sixteenth of the 1.63% this file calls refusing routine work.

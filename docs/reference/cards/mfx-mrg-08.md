# The union resolver DISCARDED a deletion: an empty side is not "nothing to say" (mfx-mrg-08)

```bash
python -m tools.kanban.union_deletion_survey            # both populations
python -m tools.kanban.union_deletion_survey --recorded # the audit rows, replayed
python -m tools.kanban.union_deletion_survey --landed   # merge history; ground truth = what merged
python -m tools.kanban.union_resolver --list-rules      # same five rules, same declarations
```

`union_resolver._resolve_cluster` opened with two lines that ran BEFORE any
declared rule and cannot be switched off (`other_side_when_empty` is
deliberately absent from `DECLARABLE_RULES`): `if not main_seg and card_seg:
return card_seg` and its mirror. **Neither looked at `base_seg`.** An empty
side was read as "this branch had nothing to say here", which is also exactly
what "this branch DELETED these lines" looks like -- so the rung restored the
lines, `resolve_index_conflicts` wrote them into the replayed commit, and the
run reported success. `keep_both_blocks` and `table_rows` both open `if
base_seg: return None` ("both REWROTE existing lines -- not two appends"); the
universal rung, which runs FIRST and refuses nothing, did not.

IT IS NOT THEORETICAL, AND THE ONE SUCCESSFUL RESOLUTION THIS RESOLVER HAS EVER
RECORDED IS THE INCIDENT. `xrv-docs-02` is the branch whose whole change is the
7,968-line CLAUDE.md trim. Its remote-tracking reflog, with the line count at
each push: `33bb0b36c` 07:25 EDT **473** (the trim as written) -> `693ee8964`
08:13 **4458** -- the union resolver's own rebase, `git diff --stat` =
**3985 insertions(+)**, force-pushed, logged `pr_watcher.union_resolved` with
the note `CLAUDE.md:other_side_when_empty@85` -> `871e08782` 08:31 **476**, a
human, by hand, *"regenerate the CLAUDE.md card trim, and make it
re-appliable"*. The rung fired THREE times in the resolver's entire recorded
history (2026-09-12, all on CLAUDE.md) and **three of three were
deletion-discards; zero were the intended case.** The other two were the mirror
image -- rebasing `xrv-cost-05` onto the merged trim, MAIN was the empty side
(`base 3905, main 0, card 3965`) -- and nothing was pushed only because a
DIFFERENT file in the same run refused and aborted the rebase.

THE INTENDED CASE NEVER REACHED THE RUNG, STRUCTURALLY, so the narrowing gives
up nothing there. `_resolve_cluster` is called only when `main_seg != base_seg`,
so `main_seg` empty IMPLIES `base_seg` non-empty, and likewise for the card:
every reachable firing is a deletion by construction. Measured over the
recorded rows, all 253 `base_empty_side_empty` hunks were disposed of by the
earlier equality branches, and 0 reached the rung. The rung is KEPT, not
removed -- a pure one-sided insertion still resolves to the other side under
`other_side_when_empty`, asserted in both directions -- and its one reachable
path now refuses.

GROUND TRUTH IS WHAT LANDED, and the recorded population cannot supply it (a
refused row produced no file). So a SECOND population, never merged with the
first: every conflicting merge on `origin/main` touching a declared file, where
the merge commit IS the answer. All 1,686 merges, not truncated: 3,172
(merge, declared file) pairs, **123 where both sides changed the file**, 8
empty-side-over-non-empty-base hunks reaching the rung = **4 distinct hunks x 2
parent orientations, 3 WRONG and 1 right**. The three wrong are one merge of
`tools/network/blueprint.py` where the rung would have put back 3,432 lines of
which **8** survive into the file that merged. `restore_was_right` is a
ONE-SIDED test -- every restored non-blank line is somewhere in the landed file
-- so a False is conclusive and a True is *consistent with*, not equal to, the
human's resolution.

FIRE RATE, three denominators because they are three questions: **1 of 1**
recorded `union_resolved` rows (100%, and that one was wrong); **1 of 74**
recorded union rows whose verdict changes (1.35% -- the other two firings were
in runs that already refused on another file); **1 extra escalation in 123**
conflicting merges (0.81%) or in 1,686 merges (0.06%). All below the 1.63% this
repo calls refusing routine work, and the cost is ONE NAMED CASE:
`5059969cc` / `tools/dashboard/app.py`, the single historical hunk the old rung
resolved correctly and the narrowed one hands to a human.

THE REPLAY UNDERSTATES AND SAYS SO. It reconstructs a whole-file three-way from
`(merge-base(head, base_sha), base_sha, head)` while the resolver reads
**rebase INDEX STAGES**, which are per-commit -- so it reproduces 2 of the 3
firings the audit rows themselves record, and the fire-rate claim rests on the
resolver's OWN `rules=[...]` notes rather than on the replay. Heads come from
the remote-tracking reflog at each row's own instant (kpr-watch-15), never
today's head; a branch whose ref was deleted after merge is
`unmeasurable_no_reflog` (10 rows) and is NEVER folded into "no deletion
found". FOUND ON THE WAY: `git log --merges --name-only` prints NOTHING for a
merge commit by default, so the first version of the landed survey reported
"no conflicting merge" across all 1,686 -- a clean bill of health produced by a
missing `--diff-merges=first-parent`.

ONE RUNG, AND THE REST IS PINNED. `_rule_keep_both_blocks`, `_rule_table_rows`,
`_rule_quoted_list_line`, `_union_quoted`, `merge_three_way`, `_clusters`,
`_map_index` and `_touches_at_seam` are byte-identical, pinned by SOURCE HASH
in `tests/kanban/test_union_deletion_rung.py` -- by hash and not by a git diff
because the gated pytest shards check out at depth 1 ("no step in this job
reads git history"), so a merge-base comparison is UNMEASURABLE exactly where
the test runs. A positive control asserts the hash actually discriminates. An
AST test asserts the rung's early return is GUARDED by a `base_seg` test, which
a behavioural test alone would not catch for an edit that special-cased the
fixture. `--list-rules` lists the same five rules and the same declarations;
the `other_side_when_empty` DESCRIPTION changed, because a rung documented as
"universal" that is no longer universal is the drift this file exists to refuse.

AND ONE EXISTING TEST PINNED THE DEFECT AS EXPECTED BEHAVIOUR:
`test_an_empty_side_resolves_to_the_other_on_any_declared_file` asserted that
`base="a\nb\nc\n", main="a\nc\n"` -- MAIN DELETED `b` -- resolved to the card's
rewrite. It is now the intended case (empty base, both directions) plus
`test_a_deletion_is_never_silently_restored` on the shape it used to bless.

NOT changed, and named: `keep_both_blocks`, `table_rows`, `quoted_list_line`,
`adjacent_edits`, the declared path list in `args/pr_watcher_config.yaml`, and
`derivation_parity`. Survey:
`docs/audits/mfx-mrg-08-union-deletion-survey.md`.

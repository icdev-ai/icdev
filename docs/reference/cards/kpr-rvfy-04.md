# A `done` task with NO artifact, and a comment mention read as a landing (kpr-rvfy-04)

> Moved verbatim from CLAUDE.md on 2026-09-12 (xrv-docs-02). The prose is the card's own record; only the leading `#` comment markers were stripped and the command runs fenced.

```bash
python -m tools.kanban.artifact_evidence --survey                    # every done task
python -m tools.kanban.artifact_evidence --survey --window-days 7 --json
python -m tools.kanban.artifact_evidence --survey --prefix ftp-      # one card
python -m tools.kanban.artifact_evidence --task ftp-prd-08 --json
python -m tools.kanban.artifact_evidence --survey --fetch            # refresh each repo first
```

MEASURED 2026-08-29: of 18 ftp-* tasks, FIVE were `done` by genesis_scheduler
with completed_via_bypass=0 while their deliverable was ABSENT from
origin/main. Real completion 11 of 18, not 16 — a board overstating itself by
45%, which silently drops real work and inflates every "N of M done" anyone
reads.

THE MECHANISM, read from kanban_status_transitions rather than from the cards:
all five were completed on a `Verified (git-first)` reason, and FOUR of them
on `N uncommitted change(s) in worktree`. UNCOMMITTED CHANGES ARE NOT A
LANDING — they are not even a commit, and a worktree is torn down.
`_run_verify_checks`'s own docstring has said "uncommitted changes alone are
NOT evidence of completion" since the dirty fallback was removed from check 5;
check 0, which runs FIRST, reinstated it. ftp-ezb-06 was marked done on 24 of
them while its worker still ran pytest; that session exited without
committing, kanban/ftp-ezb-06 carries zero commits and `git fsck` finds no
dangling one — ~40 minutes of finished work is gone, and the false `done`
removed the pressure that would have caught the loss. The fifth, ftp-prd-07,
came through the OTHER arm: `<dispatch baseline>..<branch>` counts every
commit MAIN gained while the task ran whenever the branch sits at or below the
default branch — "18 file(s) changed" on a branch 0 commits ahead. Both arms
are fixed: the fast path takes `committed_only=True`, and arm 1 excludes what
is already on the default branch. SURVEYED first — the dirty arm carried
3.01% of the last 498 scheduler completions (branch commits 55.6%, main
advanced 18.9%, not git-first 22.5%), so a task that now falls through runs
the full check chain and, failing that, is RETRIED with its worktree intact.
NOT narrowed, and stated rather than hidden: arm 3 still infers "merged" from
"main advanced AND the worktree is clean". It carries 18.9% of completions and
is the auto-merge path, so it needs its own survey.

THE POSITIVE HALF OF THE DONE-GATE. `_branch_has_unmerged_commits` asks
whether a task's branch holds work that has NOT landed, so it is satisfied by
unmerged work being ABSENT: a task nothing ever built, whose branch does not
exist, passes it trivially. A NEGATIVE check cannot establish that anything
happened. `done_delivery_refusal` in reflexes/kanban.py refuses an AUTOMATIC
`done` when the dispatch record, the branch listing and the commit compare
were all READ and all three came back negative. Anything unreadable is None
and ALLOWS — an unreachable git must never wedge the board. Stand it down with
KANBAN_REQUIRE_DELIVERY_EVIDENCE=0, never a shell neutraliser.
SURVEYED BEFORE ARMING over the 904 tasks whose latest `-> done` came from an
actor that reaches _move_task (scheduler/pr_watcher/tool_runner/
startup_backfill — the DASHBOARD is not in it, it has its own move path, and
measuring over every done task instead read a meaningless 17.63%). The bare
rule fires 3.21%, twice the 1.63% this repo calls refusing routine work, and
every one of the 29 was LEGITIMATE — so each is answered with POSITIVE
evidence, never an exemption list: a parent whose children are all done has
its children's evidence (12 of 29); a merged-and-deleted branch's work is ON
MAIN and `_work_already_landed` sees it; and the pre-dispatch auto-resolver,
whose claim is "there was nothing to build", now completes under its own
actor `pre_dispatch_resolver` instead of wearing the scheduler's. After
narrowing: 17 fires (1.88%), ALL dated 2026-06-14..06-30 from the June-era
auto-resolve path that wrote no reason, and 0.00% over the last 30 days (410
completions) and the last 60 (451). Do NOT add a fifth exempt actor to quieten
a fire — find the positive evidence, or the completion has none.

A TASK ID IN A FILE IS NOT AN ARTIFACT, and this is the trap the obvious
survey falls into. Three of the five ids are on origin/main ONLY as forward
references in comments naming the card that WILL do the work —
`FIN_API_TOKEN ... (ftp-prd-08)` in setup_ft.py, supervise_ft.py citing
ftp-prd-07, test_bootstrap.py naming ftp-ezb-05. A comment saying "the X card
will do this" is the strongest available evidence that X has NOT happened, so
`git grep -l <id> origin/main` inverts the signal exactly. landed_check gained
`CONFIDENCE_FILE_CONTENT` (rank 0, below `body`) and `NON_LANDING_CONFIDENCE`;
it is absent from BLOCKING_CONFIDENCE, `check_file_content_bulk` hard-wires
`landed: False`, and the done-gate reads that ONE statement of the rule rather
than re-deriving it.
```bash
python -c "from tools.kanban.landed_check import check_file_content_bulk as f; print(f(['ftp-prd-08'])['ftp-prd-08'])"
```

FOUR SURVEY VERDICTS and `unmeasurable` is never folded into the others:
  present       every declared artifact exists on the default branch
  partial       some do — the card shipped part of what it declared
  absent        none does. THE FINDING.
  unmeasurable  the card declares no artifact path, or the repo/ref could not
                be read. NOT a clean bill of health, counted separately.
A "declared artifact" needs a CREATION MARKER within 40 chars ("new
ft_api/auth.py"): a path merely CITED for context already exists, so counting
it would make every card report `present` for free. Shared registries
(tools/manifest.md, args/ci_test_files/core.txt, requirements.txt) are never a
deliverable.
TWO WAYS "NOT ON MAIN" IS NOT A FINDING, both MEASURED as false positives on
the first board-wide run (300 done tasks, 14 days — its ONLY two findings, and
both wrong; a report whose findings are all wrong is one people learn to
skip). Each is answered by RE-DERIVING, never by a blocklist: a path git is
told never to track (`git check-ignore`) cannot be on any branch, so it is
kept out of BOTH present and missing (ftl-sched-03 declared
args/ft_scheduler.local.yaml, .gitignore line 40); and a path the card wrote
relative to a subdirectory resolves by a UNIQUE suffix match against the
branch's tracked files, recorded under `resolved_relative` rather than
silently applied (ftl-val-05 declared families/__init__.py, on main at
icdev_fin/backtest/families/__init__.py). Two or more matches is a GUESS, and
a guess is not evidence, so an ambiguous path stays missing. After both: the
same 14-day sweep reports 21 present, 0 findings, 279 unmeasurable. `artifact_present_pct` is None, NEVER 100.0, over an empty
denominator (args/perfect_score_gate.yaml, ratcheted to 0). Repo-aware through
`repo_registry`, so an ftp-* deliverable is looked for in ICDEV[FT].
RE-VERIFIED against the tree 2026-08-29, `git cat-file -e origin/main:<path>`
in C:/ai/icdev_ft: ftp-prd-08 ABSENT (ft_api/auth.py, ui/src/components/
AuthGate.tsx), ftp-prd-07 ABSENT (icdev_fin/fathomdesk/alert_delivery.py),
ftp-ezb-06 PARTIAL (ui/src/lib/glossary.ts and routes/glossary.tsx absent),
ftp-ezb-05 and ftp-prd-11 UNMEASURABLE — both declare a change to an EXISTING
file, so the survey says it cannot tell rather than inventing a finding. The
card's claim that ftp-prd-11 was "never dispatched" does not survive the
transition log: it was dispatched at 23:52 and completed at 23:57 on the dirty
worktree arm, like the other three.
Report only, deliberately no --gate: it measures the BOARD, not a diff
(kpr-fix-03). Exit 2 = the survey could not be produced, never a clean survey.

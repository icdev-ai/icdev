# mfx-mrg-07 — the Actions auto-merge workflow is a FOURTH merge door, and it walked past `protected_paths`

**Measured:** 2026-09-07, `gh pr list --state merged --limit 84 --json number,mergedBy,mergedAt,headRefName,files`
against icdev-ai/icdev, files matched with the shipped predicate
(`tools.ci.protected_paths.protected_hits`, exact-or-directory-prefix) over the
`protected_paths` list as it stood on main at 90bcaaf24.
**Window:** merges from 2026-09-04T01:59Z to 2026-09-06T20:50Z plus #2143 at 2026-09-07T05:02Z.

## The door

`.github/workflows/pr-watcher.yml` ("PR Auto-Merge (Kanban)") runs on a 15-minute
cron and on every `check_suite` completion, as `github-actions[bot]`, with
`contents: write`. Its ladder was three rungs: `kanban/*` head branch, not a
draft, required checks green (read as a column since task-det-295a9bb95e),
`mergeable == MERGEABLE`. It ran none of land.py's thirteen checks — not
`_refuse_protected`, not the sibling hold or its tie-break, not `behind_main`,
not landed_check, not the enforced done-gate.

Observed cadence, from `gh run list --workflow "PR Auto-Merge (Kanban)"`: the
last six runs were all `schedule` events at 22:41, 00:21, 05:02, 10:17, 15:58,
19:50 — roughly every 4–5 hours, not every 15 minutes. GitHub delays scheduled
workflows under load; the declared cron is an upper bound on frequency, not a
promise. No `check_suite`-triggered run appears in that sample.

## The measurement

| PR | merged by | merged at (UTC) | branch | protected paths hit |
|---|---|---|---|---|
| #2154 | github-actions | 2026-09-06 22:41 | kanban/mfx-mrg-06 | args/pr_watcher_config.yaml, tools/ci/pr_watcher.py |
| #2143 | github-actions | 2026-09-07 05:02 | kanban/mfx-mrg-04 | tools/ci/pr_watcher.py |
| #2141 | github-actions | 2026-09-06 05:41 | kanban/kpr-watch-13 | tools/ci/pr_watcher.py |
| #2135 | github-actions | 2026-09-05 21:19 | kanban/kpr-watch-12 | args/pr_watcher_config.yaml, tools/ci/pr_watcher.py |
| #2130 | github-actions | 2026-09-05 23:07 | kanban/mfx-mrg-03 | tools/ci/pr_watcher.py |
| #2102 | github-actions | 2026-09-05 12:05 | kanban/mfx-mrg-02 | args/pr_watcher_config.yaml, tools/ci/pr_watcher.py |
| #2070 | icdev-ai (operator) | 2026-09-06 12:00 | kanban/mfx-sib-03 | args/pr_watcher_config.yaml, tools/ci/pr_watcher.py |
| #2069 | github-actions | 2026-09-04 17:36 | kanban/mfx-own-02 | tools/kanban/task_factory.py |
| #2066 | github-actions | 2026-09-06 00:57 | kanban/mfx-boot-01 | tools/ci/pr_watcher.py |
| #2064 | icdev-ai (operator) | 2026-09-06 11:02 | kanban/mfx-mrg-01 | tools/ci/pr_watcher.py |

- 84 merged PRs sampled; **10** touched a protected path.
- **8** of the 10 were merged by `github-actions[bot]` through this workflow.
- **7** of those 8 edited `tools/ci/pr_watcher.py` — the first entry in the list.
- **2** were merged by the operator (#2064, #2070).
- `SELECT COUNT(*) FROM audit_trail WHERE event_type='kanban.protected_merge_override'`
  was **0** on the live board: the mfx-mrg-04 door had never been used, and its
  own PR (#2143) is on the table above.

The card's numbers reproduce exactly. One caveat on method: `gh pr list --json
files` is GraphQL's `files(first: 100)`, so a PR with more than 100 changed files
would be under-read here. None of the 84 is in that range; the workflow's own
check (below) reads the paginated REST listing precisely so that it never is.

## What changed

1. **`tools/ci/protected_paths.py`** — the ONE implementation of the rule.
   `protected_hits` moved here from `tools/ci/merge_readiness.py`, which now
   re-exports it (identity pinned by test, `tools/git/coordination_paths.py`
   precedent). Stdlib-only, so a one-file sparse checkout runs it on a bare
   runner. CLI: `--config-file` (the YAML text), `--files-from` /`--files`,
   `--expected-count`; exit 0 clean / 1 protected / 2 undecidable.
2. **The workflow** checks out that one file from the repository's DEFAULT
   branch (never a PR head), reads `protected_paths` from
   `args/pr_watcher_config.yaml` on the PR's BASE branch via
   `gh api repos/.../contents/...?ref=<baseRefName>` (once per distinct base),
   reads the PR's files from `gh api pulls/<n>/files --paginate` and checks the
   count against the forge's `changedFiles`, and skips a protected PR with the
   hits named, pointing at the audited door.
3. **`protected_paths` gains** `.github/workflows/pr-watcher.yml` and
   `tools/ci/protected_paths.py`. The consequence is deliberate: this card's own
   PR is protected and must land through
   `kanban/cli.py --set-status mfx-mrg-07 done --merge --protected-ok --reason '<why>'`
   — the first real use of the mfx-mrg-04 door.

## The undecidable case, decided

An unreadable config, an unreadable base name, a file listing that is shorter
than the forge's own count: every one is a SKIP, the same precedent the
workflow already applies to an empty required-check answer. The cost is real
and stated in the workflow itself: this cron is the door that kept the board
moving while the local watcher was blind to a tripped GraphQL limit
(2026-09-06), so a persistent config-read failure stalls every kanban merge on
that base until a human reads the log. The alternative — an unreadable list
reading as "nothing protected" — reinstates the exact hole this card closes.

## Named, not fixed here

- **Squash vs merge.** This workflow merges `--squash`; land.py and the local
  watcher merge `--merge`. Squash leaves a branch patch-equivalent but not an
  ancestor, the `branch_not_ancestor` signature that stranded mfx-ci-04 and
  kph-repark-mfx-ci-04 in `validating` for five hours on 2026-09-06 (mfx-own-05).
  Which strategy to keep is a history decision with its own survey of the
  not-ancestor population; unchanged here.
- **The other ten rungs.** The workflow still runs none of land.py's sibling
  hold, `behind_main`, landed_check, enforced done-gate, approved or
  changes-requested checks. This card closed the one rung that guards the
  merger's own code.
- **The local watcher's 100-file cap.** `_open_pr_index` reads files through
  `gh pr list --json files`, the same GraphQL `first: 100` — a protected path
  beyond the hundredth file of a PR is invisible to `_protected_hits` today.
- **mfx-mrg-04's citation list** records #2066 as a human merge; the table above
  shows it merged by the bot at 00:57.

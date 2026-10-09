# The Actions auto-merge workflow is a FOURTH door, and it now honours protected_paths (mfx-mrg-07)

> Moved verbatim from CLAUDE.md on 2026-09-12 (xrv-docs-02). The prose is the card's own record; only the leading `#` comment markers were stripped and the command runs fenced.

```bash
python tools/ci/protected_paths.py --config-file args/pr_watcher_config.yaml --files tools/ci/pr_watcher.py docs/x.md   # 1: protected
python tools/ci/protected_paths.py --config-file args/pr_watcher_config.yaml --files docs/x.md                            # 0: clean
python tools/ci/protected_paths.py --config-file args/pr_watcher_config.yaml --files docs/x.md --expected-count 3         # 2: truncated listing
gh run list --workflow "PR Auto-Merge (Kanban)" --limit 5     # the door; a skipped PR names its hits in the log
```

`.github/workflows/pr-watcher.yml` squash-merges any green non-draft kanban/*
PR as github-actions[bot], on a cron and on check_suite completion, and ran
NONE of land.py's thirteen checks -- not `_refuse_protected`. MEASURED
2026-09-07 over the last 84 merged PRs: 10 touched a protected path, EIGHT
merged unattended through this workflow, 7 of those 8 editing
tools/ci/pr_watcher.py itself. mfx-mrg-04's own PR (#2143) -- the audited
`--protected-ok` door, built on the premise that such a PR needs a human --
was merged by it at 05:02; `kanban.protected_merge_override` had ZERO rows.
ONE LIST, ONE RULE. `protected_hits` now lives in tools/ci/protected_paths.py,
stdlib-only, and merge_readiness RE-EXPORTS it (identity pinned by test), so
the local watcher's two doors and the workflow run the same function. The
workflow checks out THAT ONE FILE from the DEFAULT branch (never a PR head),
fetches `protected_paths` from args/pr_watcher_config.yaml on the PR's BASE
branch (`gh api contents/...?ref=<baseRefName>` -- a PR that deletes its own
path from the list cannot authorise itself), reads the PR's files from the
PAGINATED REST listing checked against the forge's `changedFiles` (gh's
GraphQL `files` stops at 100), and SKIPS a protected PR with the hits named,
leaving it to the audited door. No path literal from the list appears in the
workflow; the test asserts that against the live list.
UNDECIDABLE IS A SKIP -- unreadable config, unreadable base, truncated
listing -- the same precedent as an empty required-check answer, and the cost
is stated: this cron is what kept the board moving while the local watcher
was blind to a tripped GraphQL limit (2026-09-06), so a persistent config-read
failure stalls every kanban merge on that base until a human reads the log.
The alternative reinstates the hole.
`.github/workflows/pr-watcher.yml` and `tools/ci/protected_paths.py` are ON
THE LIST: a workflow with `contents: write` that merges unattended belongs
there on the merge ladder's own reasoning, and this card's PR is the first
real use of `--protected-ok`. NOT changed, and named: the workflow merges
`--squash` while land.py and the watcher merge `--merge` (the
`branch_not_ancestor` shape of mfx-own-05 -- its own survey), and it still
runs none of the other ten rungs. Survey:
docs/audits/mfx-mrg-07-actions-auto-merge-door-survey.md

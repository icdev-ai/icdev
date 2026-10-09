# ONE ICDEV CI run per ref -- a newer push CANCELS the superseded run (mfx-ci-02)

> Moved verbatim from CLAUDE.md on 2026-09-12 (xrv-docs-02). The prose is the card's own record; only the leading `#` comment markers were stripped and the command runs fenced.

```bash
python -m pytest tests/ci/test_ci_concurrency.py -q      # the declaration, and its consumers
gh run list --workflow "ICDEV CI" --branch <branch> --limit 5   # one in progress, the rest cancelled
```

MEASURED 2026-09-03 23:30-00:30 UTC: eight ICDEV CI runs queued behind one in
progress (the account's job cap makes a ~19-job run effectively serial), two of
them for commits a newer push had ALREADY superseded; an operator cancelled
the dead runs by hand to move a green PR forward ~35 minutes. icdev-ci.yml
declared no `concurrency:` at all. Now, at WORKFLOW level:
  concurrency: { group: icdev-ci-${{ github.ref }}, cancel-in-progress: true }
`github.ref` (never head_ref, which is EMPTY on a push): a `pull_request` run
and a `push` run for the same commit sit in DIFFERENT groups and report
different checks. The workflow-name prefix keeps another workflow's group out.
main is INCLUDED -- a superseded main run is dead too -- and every consumer of
a main run was checked: shard-timings.yml asks `--status success` (a
cancelled run is neither a measurement nor a failure; crx-test-06 keeps
`cancelled` its own outcome and e2e_flake_survey counts it apart), and
merge_readiness / the done door read the PR's HEAD-sha rollup, in which the
cancellation on the OLD sha is simply absent. A job-level `concurrency:` would
carve that job OUT of the group and keep it queueing for an abandoned sha;
the test refuses one. Playwright is not a separate workflow here (the E2E
shards are icdev-ci.yml jobs); the test finds every workflow that runs
`npx playwright test` and requires the declaration on each.

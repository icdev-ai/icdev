# The gated pytest run is SHARDED across runners (crx-test-05)

> Moved verbatim from CLAUDE.md on 2026-09-12 (xrv-docs-02). The prose is the card's own record; only the leading `#` comment markers were stripped and the command runs fenced.

```bash
python tools/ci/gated_test_list.py --print --list core --shard 2/4   # this shard's targets
python tools/ci/gated_test_list.py --check --list core --shard 2/4   # validate, then narrow
```

`Test` (33m43s, of which 31m15s was ONE pytest call over 438 files) is now an
AGGREGATOR over `test-gates` + a 4-way `test-shard` matrix, and `E2E
(Playwright)` is an aggregator over `e2e-shard`. Both keep their exact check
NAMES, so branch protection and the three `needs: [test]` jobs need no edit.
SHARDED ACROSS RUNNERS, never pytest-xdist: 138 test files write the ambient
cwd/data/icdev.db through a bare get_connection() and xdist workers share one
working directory (crx-test-03 spike). A shard per runner has its own
filesystem, so the hazard does not arise and no quarantine markers are needed.
BIN-PACKED BY MEASURED DURATION (crx-test-07), never contiguous chunks (they
group tests/cortex/* -- 44 files -- into one shard) and never builtin `hash()`:
PYTHONHASHSEED is randomised per process, so a hash partition puts a file in
shard 2 on one runner and shard 4 on another -- some files run twice, others
never, and the run reports GREEN. See the crx-test-07 block below.
The floor / duplicate / existence checks always read the FULL list. A derived
per-shard floor is strictly weaker: a 110-file shard could lose 90 files and
still clear a floor of 20. `--shard` REFUSES --check-coverage /
--prune-backlog -- sharding a census silently shrinks the backlog ratchet.
BOTH AGGREGATOR INVARIANTS FAIL *GREEN* IF WRONG, so both are pinned by tests
in tests/ci/test_gated_test_list.py: `if: always()` (without it GitHub SKIPS
the aggregator when a dependency fails, and a skipped check is never accepted
by branch protection -- the PR waits forever with no red anywhere) and
`!= success`, NEVER `== failure` (a CANCELLED shard is not `failure`, so
enumerating failure reports green on a suite that never finished).
E2E no longer waits for `test` (`needs: lint`), which alone removed ~17.5 min
of wall clock. playwright.config.ts KEEPS workers:1 and fullyParallel:false --
the Gov/DoD audit-traceability decision -- because --shard splits ACROSS jobs
while leaving order WITHIN a shard deterministic.
Files that must share a process: args/ci_test_files/shard_pins.txt.

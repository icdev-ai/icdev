# The shards are BIN-PACKED by measured duration, not file count (crx-test-07)

> Moved verbatim from CLAUDE.md on 2026-09-12 (xrv-docs-02). The prose is the card's own record; only the leading `#` comment markers were stripped and the command runs fenced.

```bash
python tools/ci/shard_timings.py --show                              # what the loader merges
python tools/ci/shard_timings.py --balance --shards 4                # the partition it produces
python tools/ci/shard_timings.py --balance --shards 4 --no-timings   # the crx-test-05 baseline
python tools/ci/gated_test_list.py --check --list core --shard 2/4 --no-timings
python tools/ci/shard_timings.py --from-junit '.tmp/junit/shard*/*.xml' --source github-run-N --write
```

Round-robin balanced FILE COUNT (111/111/110/110) and said NOTHING about
runtime. Measured on the first merged sharded pipeline (run 32352491214,
2026-08-20): shard 1 17m01s, shard 2 5m59s, shard 3 5m43s, shard 4 6m36s -- so
`Test` cost 17 minutes to do ~7 minutes of work while three runners idled for
ten of them, because shard 1 drew the repo-wide scanners whose cost is
superlinear in tree size. `partition()` now does greedy longest-first bin
packing over the per-file snapshot in args/ci_test_timings/.
READ FROM THE JUNIT XML, never `--durations`: that flag prints a truncated
top-25 of CALL time, while pytest's default junit_duration_report is `total`
-- and the four worst offenders spent 82.6s/33.3s/32.5s/26.8s in SETUP ALONE.
TWO PROPERTIES ASSERTED, because violating either reports GREEN. `partition()`
computes the WHOLE partition and checks multiset equality before handing back
one shard, so a dropped file is caught on the runner that would otherwise
silently skip it; and no builtin `hash()` anywhere. Floor / duplicate /
existence still read the FULL list.
A FILE ABSENT FROM THE SNAPSHOT IS NEVER DROPPED -- it is weighted at the
MEDIAN of the measured entries. Median rather than zero (zero declares a new
test free and lets any number pile onto one shard); median rather than mean
(the mean is dragged by the very scanners that caused the imbalance). Nothing
measured degrades to round-robin, and a MALFORMED snapshot degrades the same
way with a `::warning::` and never an error -- this directory governs how FAST
the gate runs, never what it COVERS, and a `Test` that goes red over a
malformed JSON file is a check people learn to bypass.
DO NOT RESPOND TO A SLOW SHARD BY RAISING N. `--balance` reports
`lower_bound_seconds`, the heaviest single INDIVISIBLE unit, which a partition
can never beat. Measured 2026-08-20 it is 699.2s of a 1791.2s suite --
tests/cortex/test_chat_routing.py, 39% of the entire gated run in FOUR tests
(278.8 + 141.4 + 139.5 + 139.4s, all four network-timeout-shaped). The busiest
shard is already AT that floor: `Test` goes 17min -> ~12min, NOT the ~8-9min a
count-based estimate predicts, and a 5th and 6th runner would idle exactly the
way three do today. Splitting that file is crx-test-08, not more shards.
PACKING IS LESS STABLE THAN ROUND-ROBIN, and that is its real cost. Round-robin
reshuffled only the TAIL on an append; greedy packing cascades, because every
unit sorted after a new one depends on the running loads -- measured
2026-08-20, adding two test files moved ~50 of the other 442 between shards.
Nothing is lost (the assertion above covers that) but a file's NEIGHBOURS
change, so an order-dependent pass surfaces in whatever PR moved the list. The
mitigations are unchanged: isolation_run.py runs a changed file ALONE, the
shard runs it IN-SUITE, and a PR's own `Test` runs the exact partition it will
merge with. Fix it by making the test self-sufficient, never by pinning it.
`snapshot.json` is owned by the weekly .github/workflows/shard-timings.yml,
which reads the newest SUCCESSFUL ICDEV CI run on the default branch (a failed
run's shards abort at `-x`, so its JUnit is a PARTIAL measurement) and opens a
PR. A task correcting one weight writes its own
args/ci_test_timings/<task-id>.json; snapshots merge newest-`generated_at`-wins
per path, the same collision-free discipline core.d/ gave core.txt.

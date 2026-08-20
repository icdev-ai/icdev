# `ci_test_timings` — per-file duration snapshots the shards bin-pack by

`tools/ci/gated_test_list.py::partition` reads every `*.json` in this directory
and packs the gated pytest run so the four shards finish at roughly the same
time. Without a readable snapshot it falls back to the round-robin partition
that shipped with crx-test-05, which balances **file count** and says nothing
about runtime.

## Why this exists (crx-test-07)

Measured on the first merged sharded pipeline (GitHub run 32352491214,
2026-08-20):

| shard | wall clock |
|-------|-----------|
| Test Shard 1 of 4 | **17m01s** ← the whole `Test` check waits on this |
| Test Shard 2 of 4 | 5m59s |
| Test Shard 3 of 4 | 5m43s |
| Test Shard 4 of 4 | 6m36s |

`Test` cost 17 minutes to do ~7 minutes of work and three runners idled for ten
of them. Shard 1 had simply drawn the repo-wide scanners, whose cost is
superlinear in tree size.

## Format

```json
{
  "generated_at": "2026-08-20T09:10:09Z",
  "source": "github-run-32352491214",
  "unit": "seconds",
  "durations": { "tests/cortex/test_chat_routing.py": 699.184 }
}
```

`durations` is per **file**, in seconds, summed over every testcase in the
JUnit XML — which is setup + call + teardown, because pytest's default
`junit_duration_report` is `total`. That matters: the four worst offenders on
shard 1 spent 82.6s, 33.3s, 32.5s and 26.8s in **setup** alone, which a
`--durations` call-time reading would have missed entirely.

Everything else in the document is provenance and coverage. Only
`generated_at` and `durations` are read.

## Who writes what

* **`snapshot.json` is owned by `.github/workflows/shard-timings.yml`.** It runs
  weekly, reads the `ci-junit-shard-*.xml` artifacts of the newest successful
  `ICDEV CI` run on `main`, and opens a PR. **Do not hand-edit it** — the next
  refresh overwrites you.
* **A task that needs to correct one file's weight writes its own
  `<task-id>.json`**, exactly as `core.d/` fragments work. Snapshots are merged
  newest-`generated_at`-wins per path, so a fragment stamped after the snapshot
  corrects it and one stamped before it cannot silently undo a fresh
  measurement.

Two PRs writing two differently-named files cannot conflict; `core.txt` was the
largest merge-collision surface in this repository until it got the same
treatment.

## Reading it by hand

```bash
python tools/ci/shard_timings.py --show                 # what the loader merges
python tools/ci/shard_timings.py --balance --shards 4   # the partition it produces
python tools/ci/shard_timings.py --balance --shards 4 --no-timings   # baseline
```

## A stale snapshot is not a broken one

A file the snapshot has never seen — a test added since the last refresh — is
weighted at the **median** of the measured files and packed like any other. It
is never dropped. Median rather than zero, because zero declares a brand-new
test free and lets an arbitrary number of them pile onto one shard; median
rather than mean, because the mean is dragged upward by the very scanners that
caused the imbalance.

A malformed snapshot degrades to round-robin and prints a `::warning::`. It is
never an error: this directory governs how fast the gate runs, not what it
covers, and a `Test` check that goes red because a JSON file is malformed is a
check people learn to bypass.

## Packing is less stable than round-robin, on purpose

Round-robin's weakness was that one insertion reshuffles everything — which did
not bite, because `resolve()` is append-only and an insertion moved only the
tail. Greedy packing has no such property: the assignment of every unit sorted
after a new one depends on the running loads, so **one added test file
cascades**. Measured 2026-08-20, adding two files moved ~50 of the other 442
between shards.

Nothing is lost or duplicated — `partition()` asserts multiset equality — but a
file's *neighbours* change, and an order-dependent pass surfaces as a failure in
whatever PR happened to move the list. The mitigations are the ones that already
exist: `isolation_run.py` runs every changed test file **alone**, the shard runs
it **in-suite**, and a PR's own `Test` executes the exact partition it will
merge with. Fix such a failure by making the test self-sufficient; a
`shard_pins.txt` entry is a workaround, not a fix.

## The floor

`--balance` reports `lower_bound_seconds` — the heaviest single **indivisible**
unit. A partition can never finish faster than that, so once the busiest shard
sits at the bound, the critical path is one file and raising the shard count
buys nothing. Measured 2026-08-20 the bound is **699.2s of a 1791.2s suite**:
`tests/cortex/test_chat_routing.py`, 39% of the entire gated run in four tests.
Splitting that file is `crx-test-08`; adding a fifth and sixth runner would
waste them exactly the way an unbalanced partition wastes the current three.

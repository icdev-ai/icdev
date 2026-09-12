# CUI // SP-CTI

# crx-test-09 — the document-intelligence order-dependence sweep

**Measured 2026-09-12**, host: Windows 11, Python 3.14, `ICDEV_STORAGE_BACKEND=sqlite`.

---

## 1. What the card said, and what it turned out to be

The card was written from CI run **34723079230**, PR #2278 (`autonomy-act-07`), Test
Shard 2 of 4:

```
FAILED tests/document_intelligence/test_original_retention.py::
       test_a_temp_stem_title_is_replaced_by_the_uploaded_name
E   AssertionError: still the temp stem: 'tmp2iq5b0jc'
E   assert 'tmp2iq5b0jc' == 'peering-policy-update'
```

Two facts were correct and are confirmed below: the PR touched nothing in that
subsystem, and the file passes alone. From those the card inferred a third — that
some piece of state survives from whichever test ran before it, "a module-level
cache, an ambient database row, or the content-addressed originals directory
dwr-fid-01 introduced" — and instructed that a fix which does not name that carrier
is not a fix.

**There is no such carrier.** The test needs no predecessor to fail. It races its own
ingest thread, and the shard move changed only its timing. The card's own hedge —
"verify rather than assume" — is what this document does.

---

## 2. The carrier, named

`POST /document-intelligence/api/ingest` does its work on a background thread. That
thread publishes the job result and then keeps going
(`tools/document_intelligence/blueprint.py`, inside `_run()`):

```
    _JOB_RESULTS[job_id] = {"status": "done", ...}   <- _wait_result unblocks HERE
    UPDATE dic_documents SET filename = …, title = CASE … END WHERE doc_id = …
    UPDATE dic_ingest_jobs SET status='done' …
    finally: os.unlink(tmp_path)
```

`GET /api/ingest/<job_id>/result` serves the in-memory cache on its fast path, so
`_wait_result` returns at the arrow. **Every assertion the test file makes
immediately afterwards about the three statements below the arrow is a race**, and
the file has had it since it was written:

| assertion | statement it depends on | position |
|---|---|---|
| `title == "peering-policy-update"` | the filename/title restore `UPDATE` | after the arrow |
| `filename == "peering-policy-update.pdf"` | same `UPDATE` | after the arrow |
| `not os.path.exists(tmp_path)` | the `os.unlink` in `finally` | after the arrow |
| `row["original_sha256"] == …` | `record_original` | **before** the arrow — safe |

That table is also why only *some* of the file's tests can lose: the retention
assertions are written before the result is published and were never at risk.

### It is not shared state — proved by a single test

`.tmp/crx09/probe_title_causality.py` drives **one** upload with **one** test in the
process, and delays only the first `_conn()` taken after the result is published —
i.e. only the restore `UPDATE`. Nothing about ordering changes:

```
delay=0.0s  title_at_result_time='peering-policy-update'  filename='peering-policy-update.pdf'
  -> the restore UPDATE had landed by the time the result was read

delay=1.0s  title_at_result_time='tmpmxaet849'  filename='tmpmxaet849.pdf'
  -> AssertionError: still the temp stem: 'tmpmxaet849'
  -> REPRODUCED the CI failure with ONE test running: not order dependence.
```

The CI message is reproduced exactly, with no predecessor, no shared cache, no
ambient row and no originals directory involved.

---

## 3. Reproduction, as the acceptance criteria ask for it

### 3.1 Alone — the card's claim, confirmed

```
$ python -m pytest tests/document_intelligence/test_original_retention.py::\
      test_a_temp_stem_title_is_replaced_by_the_uploaded_name -q
1 passed in 7.87s
```

### 3.2 In-suite — and the surprise

The card expected the in-suite half to need the failing shard. It does not. Running
**the module by itself**, which is the weakest possible "in-suite", already fails —
on the `os.unlink` arm of the same race, which `time.sleep(0.1)` had been guessing
at:

```
$ for i in 1..5: python -m pytest tests/document_intelligence/test_original_retention.py -q

run 1  14 passed
run 2  FAILED …::test_upload_retains_before_ingest_records_sha_and_still_deletes_the_temp
run 3  FAILED …::test_upload_retains_before_ingest_records_sha_and_still_deletes_the_temp
run 4  14 passed
run 5  FAILED …::test_upload_retains_before_ingest_records_sha_and_still_deletes_the_temp
```

**3 of 5 solo runs red** (8 runs total across the session: 5 red). A file that fails
3 times out of 5 with nothing else in the process cannot be order-dependent, and
this is the measurement the sweep tool is built around.

**The file that must run before it: none.** That is the finding, not a gap in the
investigation.

### 3.3 After the fix

```
run 1..5  14 passed / 14 passed / 14 passed / 14 passed / 14 passed
```

---

## 4. The fix

`_wait_until(predicate, what, timeout=15)` — a deadline, never a sleep. It waits for
the actual post-condition and still FAILS, naming the step that never happened, if
the restore or the unlink genuinely does not occur, so no assertion loses strength.
The barrier chosen for the three title tests is the `filename` column, which the same
`UPDATE` sets **unconditionally** — that orders all three, including
`test_a_real_extracted_title_is_kept`, whose point is that the title does *not*
change and which therefore has no title-shaped barrier of its own.

The identical patch was authored concurrently on `kanban/autonomy-act-07` (commit
`cd9765e41`, PR #2278) by the session whose CI run produced the failure. It is taken
here byte-for-byte rather than re-derived, so whichever lands first the other merges
without a conflict.

**Not touched, per acceptance criterion 4:** `args/ci_test_files/shard_pins.txt`, the
bin packing in `gated_test_list.partition()`, `args/ci_test_timings/`, and the shard
count. Pinning the order would have frozen the order that happens to work today and
hidden a race that is not about order at all.

---

## 5. The sweep

`tools/ci/order_dependence_sweep.py` (new). `isolation_run.py` asks whether one
CHANGED file still passes alone; nothing swept a subsystem. The tool's whole design
follows from section 2: **"red in the suite, green alone" is not evidence of
ordering.** So every file is run ALONE `--repeats` times before any shuffling, and

* red in **any** solo repeat → `flaky_alone` — a self-flake; ordering is ruled out
* red in **every** solo repeat → `alone_red` — broken; ordering is ruled out
* green in every solo repeat, red under some seeded shuffle → candidate, then
  **confirmed** by re-running that same shuffle; an unreproduced red is
  `order_suspect`, not a finding
* `--bisect` shrinks a confirmed failing order to the smallest reproducing prefix and
  names its last file, because `order_dependent` without a predecessor is not
  actionable

A file the JUnit report never mentions is recorded `absent`, never `passed`: a
collection error that kills the run must not read as green. Exit 2 when nothing
matched — a sweep that ran nothing is not a sweep that found nothing.

### Command

```
python -m tools.ci.order_dependence_sweep --root . --match document_intelligence \
    --permutations 3 --repeats 3 --seed 20260912 --json
```

### Result

<!-- SWEEP-RESULT -->

---

## 6. What this costs next time

The sweep is minutes of wall clock and is deliberately **not** wired into a required
job. Run it against a subsystem when a shard move surfaces a failure in a PR that did
not touch it — and read `flaky_alone` as the answer it is, rather than the beginning
of a hunt for shared state.

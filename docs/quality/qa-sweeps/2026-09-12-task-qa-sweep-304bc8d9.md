# QA Agent — Full E2E Suite Sweep, 2026-09-12 (task-qa-sweep-304bc8d9)

Run id `qa-1789244530` (`ace_qa_runs`), tree `aded83efd` (origin/main at
dispatch). Playwright 1.58.2, Node 24.11.0, Python 3.14.0, win32 10.0.26200.

**Result: 88 routes clean; 853 E2E tests run, 837 passed, 0 failed, 0 flaky,
16 skipped; 69/69 spec files measured. No bug cards filed, because there were
no failures to file.** The suite ran against the throwaway `icdev_e2e`
database and the canonical board gained **zero** E2E rows (§3). The stall
sampler covered 12/12 batches and recorded **0 host stalls** (§4).

## 1. Route smoke — clean

`python tools/testing/route_smoke.py --all --json` against the live dashboard
on `http://127.0.0.1:5050`: **88 checks, 0 failures, 0 returning 5xx.**
`skipped_by_cap`, `excluded`, `tolerated` and `stale_expect_fail` are all
empty, so the 88 is the whole declared surface and not a subset. The card's
gate ("stop and report if any route returns 500") did not fire.

87 of the 88 answered `200`. The one exception is `/api/iqe-query` at `405`,
which the tool itself marks `ok: true` — the route is POST-only and a GET
probe is *supposed* to be refused. Route smoke is GET-only, so it ran against
the live dashboard; the E2E suite, which writes, did not (§3).

## 2. Full E2E run — 69/69 spec files, 12/12 batches, none truncated

`python tools/testing/qa_agent_runner.py --run --json --record --deadline-seconds 5400`,
batches 20:22:10Z → 20:43:56Z (21m46s wall clock, 1274.5s of batch time).
`spec_files_not_run` and `spec_files_no_report` are both **empty**, and every
batch exited `returncode 0` with a report on disk and zero top-level
Playwright `errors`.

| batch | expected | unexpected | flaky | skipped | seconds |
|---|---|---|---|---|---|
| 0 | 133 | 0 | 0 | 0 | 137.7 |
| 1 | 43 | 0 | 0 | 4 | 71.1 |
| 2 | 86 | 0 | 0 | 0 | 117.1 |
| 3 | 31 | 0 | 0 | 1 | 70.4 |
| 4 | 27 | 0 | 0 | 6 | 55.4 |
| 5 | 67 | 0 | 0 | 0 | 99.9 |
| 6 | 61 | 0 | 0 | 0 | 109.9 |
| 7 | 92 | 0 | 0 | 0 | 137.7 |
| 8 | 142 | 0 | 0 | 1 | 211.2 |
| 9 | 38 | 0 | 0 | 0 | 96.6 |
| 10 | 76 | 0 | 0 | 4 | 70.0 |
| 11 | 41 | 0 | 0 | 0 | 97.5 |
| **total** | **837** | **0** | **0** | **16** | **1274.5** |

**The zero was derived three ways.** The runner reports `total=853 passed=837
failed=0 skipped=16 failures_unparsed=0`. Playwright's own `stats` blocks,
summed over the 12 batch reports, give 837 expected / **0 unexpected** / 0
flaky / 16 skipped. A recursive walk of every suite tree, reading each test's
last `results[].status` at any depth, gives 837 `passed` / 16 `skipped` and
**no `failed` or `timedOut`**, across 69 distinct spec files. All three agree.

`screenshot_count` is 624, counted from Playwright report *attachments*
(`count_screenshot_attachments`) rather than a directory glob — the inflated
count fixed in #2189 is not back.

Recent rows in `ace_qa_runs` for comparison: 2026-09-11 `qa-1789161604`
849 / 829 / 4 failed (all four inside host stalls, qa-fail-5cacee65f1d03c8c);
2026-09-10 `qa-1789072164` 843 / 827 / 0; 2026-09-09 `qa-1788989390`
843 / 824 / 2; 2026-09-08 `qa-1788900790` 840 / 833 / 0.

## 3. Isolation — measured, and the guard refused the first attempt

The suite ran against `icdev_e2e`, not the canonical board, and this is the
first sweep where the isolation guard is on record **refusing a run**:

* **The first attempt was refused, all 12 batches.** Run `qa-1789244333`
  (20:18:53Z) set `ICDEV_PG_DATABASE=icdev_e2e` but named no port, so
  Playwright's `reuseExistingServer` attached to the canonical dashboard
  already up on 5050. `globalSetup` measured that server via
  `current_database()` on `/api/health`, got `verdict: mismatch — requested
  icdev_e2e, server on icdev`, and **refused every batch in 12.2s each**.
  Its `ace_qa_runs` row reads `status=no_tests, total=0` — not `passed`. This
  is exactly the defect qa-fail-6a87916931be3793 was built to catch, catching
  itself in the wild: ~850 tests' worth of fixture writes did not reach the
  canonical board, and the operator was told why rather than believing they
  were isolated. Note also that the subprocess writer measured `confirmed
  icdev_e2e` in the same report — only the *server* was wrong, which is the
  half that a re-read of one's own environment cannot detect.
* **The second attempt named a port and was confirmed.** With
  `ICDEV_E2E_BASE_URL=http://127.0.0.1:5090` and `ICDEV_DASHBOARD_PORT=5090`
  Playwright started its own server, and `/api/health` on 5090 answered
  `{"backend":"postgresql","database":"icdev_e2e","database_measured":true,
  "db":true,"status":"ok"}` — read live during batch 9, not inferred.
* **Negative control.** `kanban_tasks` rows created on the canonical `icdev`
  board in the window 20:22:10Z–20:44:30Z: **3, and none of them ours** — all
  three are `[NEEDED-A-HUMAN]` pr_watcher escalations for the concurrent
  `artifact-fresh-*` PRs, written by another live session at 20:27:44Z. Zero
  E2E residue.
* **Positive control.** The writes landed in the throwaway database instead:
  **101 `audit_trail` rows** in `icdev_e2e` inside the same window. The three
  `kanban_tasks` rows on the isolated board all date from 2026-07-29 and were
  untouched, so the kanban-writing specs cleaned up after themselves.
* **The worktree scheduler guard also fired, four times.** Playwright's
  `webServer` command is `python tools/dashboard/app.py`, whose `__main__`
  spawns a kanban scheduler — from *this worktree*. The 2026-09-10 sweep
  avoided that by importing `app` and calling `app.run()` by hand. It no
  longer has to: `.tmp/kanban_scheduler.log` shows four refusals, one per
  webServer launch, each reading "Refusing to start: this scheduler lives in
  a linked git worktree … It would dispatch THIS tree's code against the
  shared board." Nothing was dispatched.

## 4. Host-stall census — 0 stalls, and the one slow sample explains nothing

The `StallSampler` (qa-fail-5cacee65f1d03c8c) ran beside **12 of 12 batches**,
`batches_unsampled: 0`, 239 samples at 5.0s against
`http://127.0.0.1:5090/api/health`:

| signal | count |
|---|---|
| `host_stalls` (sleep overshoot ≥ 2.0s) | **0** |
| `health_slow` (server answered, ≥ 2.0s) | **1** |
| `unreachable` (between batches — expected, not a stall kind) | 26 |
| reachable samples | 213 |

Worst sleep overshoot across the whole run was **0.024s**, i.e. ordinary
jitter — the 2026-09-11 incident measured 7–9s. The single `health_slow`
sample is batch 5 at 20:30:15Z, `health_seconds: 2.058` against a 2.000s
threshold, with its own sleep overshoot at 0.010s: a borderline sample on a
healthy host, not a starved one. `failures_during_stall` is **0** and
`failures_stall_unmeasured` is **0**, which here is trivially true because
there were no failures — the census is reported for the comparison it gives
future sweeps, not because it excused anything.

## 5. The 16 skips, all declared — and 6 are the price of isolation

No skip is unexplained; each carries a written reason at its `test.skip` site.

| n | spec | reason |
|---|---|---|
| 4 | `clawhub.spec.ts` | ClawHub service not running on port 5077 |
| 4 | `skillhub.spec.ts` | SkillHub service not running on port 5077 |
| 5 | `e2e_me_conflict_lifecycle.spec.ts` | seed session `sess-9cc6891cb548` (requirements) fixture absent |
| 1 | `nav_regression_probes.spec.ts` | no seeded pulse post on this DB |
| 1 | `dwo_restart_durability.spec.ts` | opt-in `ICDEV_E2E_DWO_RESTART` — the spec restarts a dashboard it owns |
| 1 | `dwo_trigger_linkage.spec.ts` | opt-in `ICDEV_E2E_DWO_TRIGGER` — the spec spawns a gateway it owns |

Two things worth naming rather than leaving in a count:

* **6 of the 16 are caused by the isolation in §3.** The five
  `e2e_me_conflict_lifecycle` tests and the one `nav_regression_probes` test
  need seed rows that exist on the canonical board and not in `icdev_e2e`.
  That is a real coverage cost of running isolated, and it is the honest price
  of not writing fixtures into the live board — but it is a cost, not a
  no-op, and it will stay until those specs seed their own fixtures.
* **The `nav_regression_probes` skip is a security probe** ("a pulse post
  renders without executing injected script"), so it deserves a second look
  rather than a tick. It is *not* the guard firing on a broken route: `/pulse`
  answered `200` in §1's smoke and `200` live. The skip that fired is the
  spec's second one — no seeded pulse post to open — and the spec states its
  own compensating control at the skip site: script-inertness of
  `pulse_post.html` is covered at source by pytest (nav-sec-07). Declared and
  compensated, not a hole.

8 of the 16 are one absent service on port 5077 covering two canvases.

## 6. Steps 3 and 4 of the card

* **Step 3 — file bug tasks for failures: nothing to file.** `failures` is
  empty and `failures_unparsed` is 0, so `file_failure_tasks` was not called
  (`filed_tasks: null`) and **no kanban card was created**. That is the
  correct outcome for a green sweep, not a step skipped.
* **Step 4 — persist to `ace_qa_runs`: done.** `--record` wrote
  `qa-1789244530` → `status=passed, total_tests=853, passed=837, failed=0,
  screenshot_count=624`, `started_at 20:22:10.315Z`, `completed_at
  20:43:56.544Z`, with `record_error: null`. Re-read back out of PostgreSQL to
  confirm, rather than trusting the writer's own return value.
  `ace_qa_failures` holds **0** rows for this run, which matches 0 failures —
  the 2026-09-11 mismatch (4 failures, 0 rows) is not back.

## 7. What this sweep does not claim

* A green sweep is not proof the 16 skipped tests would pass; 6 of them have
  never run on an isolated database.
* The stall census proves the *runner's* process was not starved and infers
  the host from that. It does not read the dashboard's request log, so the
  20s+ server silence that was the 2026-09-11 incident's primary evidence is
  not re-derived here.
* `audit_trail` on the canonical board cannot serve as a negative control —
  fifteen other sessions were live throughout the window. The `kanban_tasks`
  count in §3 can, and does.

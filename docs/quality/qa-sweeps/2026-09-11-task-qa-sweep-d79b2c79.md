# QA Agent — Full E2E Suite Sweep, 2026-09-11 (task-qa-sweep-d79b2c79)

Run id `qa-1789161604` (`ace_qa_runs`), trigger `kanban:task-qa-sweep-d79b2c79`,
tree `f4b16d7e0` (origin/main at dispatch). Playwright 1.58.2, Node 24.11.0,
Python 3.14.0, win32 10.0.26200.

**Result: 88 routes clean; 849 E2E tests run, 829 passed, 4 failed, 0 flaky,
16 skipped; 68/68 spec files measured. One `fix` card filed for the four,
`qa-fail-5cacee65f1d03c8c`, because they share one measured cause: the
isolated server went silent for 20-59 s thirteen times during the run and
each failure is a navigation or request that fell inside one of those gaps
(§4). The same suite on the same host read 843/0 red yesterday in 19m22s; this
run took 40m28s.** The canonical board gained no fixture row (§3).

## 1. Route smoke — clean

`python tools/testing/route_smoke.py --all --json --base http://127.0.0.1:5050`:
**88 checks, 0 failures, 0 returning 5xx.** `skipped_by_cap`, `excluded`,
`tolerated` and `stale_expect_fail` are all 0, so the 88 is the whole declared
surface. The card's gate ("stop and report if any route returns 500") did not
fire. Route smoke is GET-only, so it ran against the live dashboard on 5050
(`/api/health`: `icdev`, measured); the E2E suite, which writes, did not (§3).

## 2. Full E2E run

`python tools/testing/qa_agent_runner.py --run --json --trigger kanban:task-qa-sweep-d79b2c79 --deadline-seconds 3300`
— batches ran 21:20:04Z → 22:00:32Z (40m28s wall clock, 2427.4 s of batch
time; yesterday 19m22s / 1162.8 s for 843 tests). `spec_files_not_run` and
`spec_files_no_report` are both **empty**; every batch exited with a report on
disk and zero top-level Playwright `errors`. `returncode 1` on a batch is
Playwright's own "a test failed", not a harness error.

| batch | expected | unexpected | flaky | skipped | seconds | 2026-09-10 s |
|---|---|---|---|---|---|---|
| 0 | 133 | 0 | 0 | 0 | 257.3 | 142.0 |
| 1 | 41 | 0 | 0 | 4 | 89.7 | 62.0 |
| 2 | 107 | **1** | 0 | 0 | 316.2 | 153.5 |
| 3 | 7 | 0 | 0 | 2 | 127.0 | 52.9 |
| 4 | 37 | **1** | 0 | 5 | 250.8 | 78.9 |
| 5 | 68 | **1** | 0 | 0 | 440.8 | 113.3 |
| 6 | 49 | 0 | 0 | 0 | 209.9 | 103.5 |
| 7 | 128 | **1** | 0 | 0 | 240.9 | 121.6 |
| 8 | 112 | 0 | 0 | 1 | 210.3 | 166.5 |
| 9 | 36 | 0 | 0 | 0 | 55.4 | 33.5 |
| 10 | 84 | 0 | 0 | 4 | 148.3 | 124.0 |
| 11 | 27 | 0 | 0 | 0 | 80.8 | 11.1 |
| **total** | **829** | **4** | **0** | **16** | **2427.4** | 1162.8 |

Batch membership shifted against yesterday because a 68th spec file joined
(§6), so the per-batch columns compare only as a whole: every batch ran
1.3x-7x slower than the same suite the day before.

**The four was derived three ways.** The runner reports `total=849 passed=829
failed=4 skipped=16 failures_unparsed=0`. Playwright's own `stats` blocks,
summed over the 12 batch reports, give 829 expected / **4 unexpected** / 0
flaky / 16 skipped. A recursive walk of every suite tree, reading each test's
last `results[].status` at any depth, gives 829 `passed` / 4 `failed` / 16
`skipped` and no `timedOut`, across 68 distinct spec files. All three agree.

Recent rows in `ace_qa_runs` for comparison: 2026-09-10 `qa-1789072164`
843 / 827 / 0 failed; 2026-09-09 `qa-1788989390` 843 / 824 / 2;
2026-09-08 `qa-1788900790` 840 / 833 / 0. This run's row carries
`started_at 21:20:04Z` / `completed_at 22:00:32Z` — the first sweep recorded
by yesterday's `record_run` fix, and the first whose duration the table can
answer.

## 3. Isolation — the suite never touched the canonical board

Same recipe as the 2026-09-10 sweep, re-measured rather than assumed:

* **Its own server.** `from tools.dashboard.app import app; app.run(port=5095)`
  from this worktree (`root_path` printed as this worktree's
  `tools/dashboard`), with the 20 flags `playwright.config.ts` puts in
  `webServer.env`, `ICDEV_DATABASE_URL` cleared to the empty string and
  `ICDEV_PG_DATABASE=icdev_e2e`. Not `python tools/dashboard/app.py`, whose
  `__main__` spawns a kanban scheduler against the fleet. Ports 5090 and 5094
  were held (§7), so 5095.
* **Measured.** `/api/health` on 5095 answered
  `"database":"icdev_e2e","database_measured":true` before the run, and
  `tests/e2e/fixtures/database_probe.py` under the same env — what every spec
  subprocess builds its environment from since #2218 — answered `icdev_e2e`.
* **The suite pointed at it**: `ICDEV_NO_SERVER=1`,
  `ICDEV_E2E_BASE_URL=http://127.0.0.1:5095`, `ICDEV_PG_DATABASE=icdev_e2e`.
  Batch 0's diagnostics snapshot records `webServer: external (ICDEV_NO_SERVER)`,
  that base URL and that database.
* **The run row went where it should.** The runner's own environment keeps the
  `.env` DSN, so `record_run` wrote to the canonical `icdev`
  (`active_database()` under the runner's env answered `icdev` before the run).
* **The board, before and after.** Canonical `icdev`: 4,050 `kanban_tasks`, 0 `[E2E`-titled,
  newest created 21:13:20Z before the run; after it 4,053, still 0 `[E2E`, and
  the three new rows are `xrv-docs-02`, `xrv-cost-05` (21:54:24Z) and
  `mfx-own-06` (22:01:13Z) — cards seeded by other sessions, named so the
  delta is not mistaken for residue. `icdev_e2e` holds 3 rows, all `[E2E`
  fixtures leaked in July, and **0** created during this run, so the suite's
  own cleanup held. Scope: measured on `kanban_tasks`, not on every table the
  suite writes.

## 4. Failures — four, one cause, one card

Four, all `TimeoutError`, all filed on **one** card,
`qa-fail-5cacee65f1d03c8c` (`fix`, high, backlog), with a row each in
`ace_qa_failures` pointing at it. One card rather than four because the
runner's own `--file-failures` docstring is right: one shared cause becomes N
cards and N duplicate PRs. The id is the one `file_failure_tasks` would have
derived for the first failure, through the same `create_tasks` door.

| spec | test | what timed out | started (UTC) |
|---|---|---|---|
| `cpmp_evm.spec.ts` | gcpl-evm-11 EVM tab content | `page.goto /cpmp/<id>`, 30 s, waiting for `load` | 21:28:14 |
| `finetune.spec.ts` | recent training jobs section | `page.goto /finetune`, 30 s, `load` | 21:34:30 |
| `idp_portal.spec.ts` | portal renders with catalog and ladder | `page.goto /idp/`, 30 s, `load` | 21:41:18 |
| `nav_menu_readiness.spec.ts` | menu "Build" link sweep | `apiRequestContext.get /dashboard/compliance-view`, 10 s | 21:49:59 |

No screenshot was captured for any of them (`screenshot: 'on'` fires at test
end, and each test ended inside a navigation that never settled); each carries
a video and, for the first, Playwright's `error-context.md` page snapshot,
which shows the nav bar rendered — the document had arrived and `load` was
waiting on subresources.

**The cause was measured while it was happening, not reconstructed.**

* Re-requested minutes later, every one of the four pages answers in under
  two seconds (1.7 s, 0.23 s, 0.29 s, 0.27 s). None has a failure in
  `ace_qa_failures` history. The server logged no exception.
* The server's request log went **silent** — no request of any kind completed
  — for 20 s or more **13 times** during the run, and four of those gaps sit
  inside the failure windows: 52 s after the `/finetune` document was served
  and before the browser's very next CSS request was answered; 34 s and 45 s
  around `/idp/`. The stretch 21:41-21:46Z alone holds six gaps (34, 45, 51,
  25, 59, 27 s). In the first failure the ~25 static files of `/cpmp/<id>`,
  which normally serve inside one second, trickled out over 21 s, so `load`
  fired after the 30 s budget.
* A 5-second sampler ran beside batches 6-11 (started after the third
  failure). At 21:50:02Z it saw `/api/health` answer in 0.10 s while the
  compliance-view request was already 10 s in flight — the server process was
  not frozen, a heavy route was starved — and the sampler's own loop period
  stretched to 12 s and 14 s at exactly that moment: the **host** was
  stalling, not the app. Batches during which the sampler saw no stall (6, 8,
  9, 10, 11) were all clean.
* PostgreSQL was not it: 0 ungranted locks and 0 active `icdev_e2e` backends
  in every sample, and the isolated server holds no connection between
  requests.
* Named contention on the host during the run, none attributed as *the*
  cause: a recursive `grep -rn … .` over the whole tree from another session,
  running from 20:19Z to roughly 21:5xZ; the two orphaned dashboards of §7;
  the scheduler, pr_watcher and two to three genesis daemons; host CPU
  40-60 % of 20 logical CPUs at the failure instants. Nothing on the board
  and nothing in `genesis_audit` is stamped inside the failure windows.

**What the card asks for** is a decision, not a re-run: three of the four
`goto` calls wait for `load` when the test's next line waits for
`domcontentloaded` and then only reads text; the link sweep's 10 s per-link
budget met a route that takes 0.27 s idle and 10-15 s starved; and the runner
has no way to say "the server stalled" — this sweep, with 4 timeouts and
double the wall clock, is recorded identically to 4 product defects. Re-running
until green was not done and is not the repair; the measurement stands.

## 5. Skips, enumerated

All 16 carry a stated reason and the set is **identical to
2026-09-10**. A skip is an unmeasured test, not a passing one.

| n | spec | reason |
|---|---|---|
| 5 | `e2e_me_conflict_lifecycle.spec.ts` | seed session `sess-9cc6891cb548` fixture not present in this environment |
| 4 | `clawhub.spec.ts` | ClawHub service not running on port 5077 |
| 4 | `skillhub.spec.ts` | SkillHub service not running on port 5077 |
| 1 | `dwo_restart_durability.spec.ts` | opt-in — set `ICDEV_E2E_DWO_RESTART=1` |
| 1 | `dwo_trigger_linkage.spec.ts` | opt-in — set `ICDEV_E2E_DWO_TRIGGER=1` |
| 1 | `nav_regression_probes.spec.ts` | no seeded pulse post on this DB; covered at source by pytest (nav-sec-07) |

## 6. Diagnostics — one true redaction, one false claim

The batch-0 snapshot (`.tmp/test_runs/e2e-env-diagnostics-<tag>.json`) now
shows the runner's DSN as `postgresql://icdev:<redacted>@localhost:5432/icdev`:
yesterday's `qa-fail-0992fb60b78c0b2e` (#2224) working on its first sweep, and
its spec `tests/e2e/env_diagnostics_redaction.spec.ts` is the 68th file this
run measured (yesterday: 67).

The same snapshot reports `runtime.playwright: not installed` for a run that
is executing under Playwright 1.58.2. The worktree carries no `node_modules`;
`npx` resolves up to the main checkout's (`C:\AI\ICDev\node_modules\.bin\..\@playwright\test\cli.js`
in the process table), while the diagnostics look only beside the config. A
snapshot asserting a tool is absent while that tool produces the snapshot is
a false claim; it costs nothing today but is the kind of line a reader trusts.
Named here, not carded: it is not a test failure and the card's filing step is
for those.

## 7. Found on the way — two earlier attempts of this card left their servers running

This was the card's THIRD dispatch. `kanban_status_transitions` shows the first
two parked for `token exhaustion` (retry 2/60, 3/60). Each had started its own
isolated dashboard (5094 at 20:17Z, 5090 at 20:40Z, both on `icdev_e2e`, both
`app.run()` from this worktree) and a runner whose batch reports stop at b4 and
b5 respectively — 5 and 6 of 12 batches, nothing recorded in `ace_qa_runs`,
0 rows in `ace_qa_failures`. The runners are gone; the two servers are still
listening, orphaned. They hold no canonical data and were not stopped by this
session (a process another session started is not this one's to kill); the
one this sweep started on 5095 was stopped after the run. Two partial sweeps
plus this one is ~2.4 suites of Chromium time spent for one measurement.

## 8. What this sweep did NOT measure

* Isolated database, shared host: `icdev_e2e` is a persistent throwaway with
  prior state, sharing the PostgreSQL server and the host with the scheduler,
  pr_watcher, three genesis daemons and the two orphaned servers above. This
  sweep is a measurement of the suite **on that host at that hour**; §4 says
  how much of the red is the host's.
* `ace_qa_failures` had never been written by `--run`: `record_failure` has a
  definition and no call site in the runner, so yesterday's 0 rows for 0
  failures and today's 0 rows for 4 read the same. The four rows for this run
  were written by hand through that function, with the card id. Named, not
  carded — it belongs on `qa-fail-5cacee65f1d03c8c`'s third candidate repair.
* The sampler ran beside batches 6-11 only; batches 0-5 have the server log's
  silence as their sole stall evidence.
* Only the route smoke and the E2E suite ran. The pytest suite, the coherence
  tiers and the CI gates are this PR's CI, not this card's measurement.

## Re-derive

```
python tools/testing/route_smoke.py --all --json --base http://127.0.0.1:5050
# isolated server: app.run() with playwright.config.ts's webServer.env,
#   ICDEV_DATABASE_URL= ICDEV_PG_DATABASE=icdev_e2e, on a free port
ICDEV_NO_SERVER=1 ICDEV_E2E_BASE_URL=http://127.0.0.1:<port> ICDEV_PG_DATABASE=icdev_e2e \
  python tools/testing/qa_agent_runner.py --run --json --trigger <label>
python tools/testing/qa_agent_runner.py --status qa-1789161604 --json
# stall census: gaps of 20s+ between consecutive lines of the isolated server's
#   request log, intersected with each failure's [startTime, startTime+duration]
# sampler beside the run (every 5s): curl -w %{time_total} <base>/api/health,
#   a trivial process spawn, Get-Process on the server pid, and
#   SELECT count(*) FROM pg_stat_activity WHERE datname='icdev_e2e' -- a sample
#   whose own wall clock stretches while health stays fast is a host stall
```

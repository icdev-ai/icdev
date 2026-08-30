# QA Agent — Full E2E Suite Sweep, 2026-08-29 (task-qa-sweep-c17bd3d6)

Run id `qa-1788050936` (`ace_qa_runs`), trigger `kanban-task-qa-sweep-c17bd3d6`,
against the live dashboard on `http://localhost:5050` with the PostgreSQL board
(3,696 `kanban_tasks` rows). Playwright 1.58.2, Node 24.11.0, Python 3.14.0,
win32 10.0.26200.

**Result: 88 routes clean, 830 E2E tests run, 814 passed, 0 failed, 16 skipped.
No bug cards filed, because there were no failures to file.** The
2026-08-22 sweep's 31 red tests are gone and both of its root-cause cards
(`qa-fail-49655511c721a165`, `qa-fail-5f7cf03a0b0a4351`) are `done` on the board.

## 1. Route smoke — clean

`python tools/testing/route_smoke.py --all --json`: **88 checks (79 nav routes +
9 API endpoints), 0 failures, 0 returning 5xx.** Nothing skipped by cap, nothing
excluded, nothing tolerated, no stale expect-fail entry — so the 88 is the whole
declared surface and not a subset. 87 answered `200`; the one non-200 is
`GET /api/iqe-query` -> `405`, which is a POST-only endpoint probed with GET and
is scored `ok` by the tool.

The card's gate ("stop and report if any route returns 500") did not fire.

## 2. Full E2E run — 65/65 spec files, 11/11 batches, no batch truncated

`python tools/testing/qa_agent_runner.py --run --json --trigger kanban-task-qa-sweep-c17bd3d6`
— 39.1 minutes of batch time inside the 3600s deadline. `spec_files_not_run` and
`spec_files_no_report` are both **empty**, so every one of the 65 spec files was
measured; this is a full sweep and not a deadline-truncated one reporting only
what it got through.

| batch | expected | unexpected | flaky | skipped | seconds |
|---|---|---|---|---|---|
| 0 | 133 | 0 | 0 | 0 | 292.1 |
| 1 | 41 | 0 | 0 | 4 | 116.6 |
| 2 | 108 | 0 | 0 | 0 | 301.0 |
| 3 | 9 | 0 | 0 | 8 | 35.4 |
| 4 | 67 | 0 | 0 | 0 | 284.5 |
| 5 | 40 | 0 | 0 | 0 | 127.5 |
| 6 | 46 | 0 | 0 | 0 | 172.7 |
| 7 | 139 | 0 | 0 | 0 | 374.4 |
| 8 | 103 | 0 | 0 | 0 | 292.9 |
| 9 | 68 | 0 | 0 | 4 | 125.6 |
| 10 | 60 | 0 | 0 | 0 | 220.6 |
| **total** | **814** | **0** | **0** | **16** | **2343.3** |

Every batch exited `returncode 0` with a report on disk and zero top-level
Playwright `errors`.

### The zero was derived twice, from code that shares nothing

The 2026-08-22 sweep's headline finding was that this runner reported
`status=passed failed=0` over 31 red tests, because `parse_playwright_json`
walked one suite level and every spec sits inside a `test.describe`. A green
verdict from the same tool is therefore not self-evidently green, so it was
re-derived from the 11 batch reports two ways that share no code:

* **Playwright's own `stats` block**, summed per batch: 814 expected,
  **0 unexpected**, 0 flaky, 16 skipped.
* **A recursive walk of the suite tree**, collecting every `results[].status`
  at any depth: 814 `passed`, **0 `failed`/`timedOut`**, 16 `skipped`.

The two agree, and they agree with the runner's own `total=830 passed=814
failed=0 skipped=16 failures_unparsed=0`. The 2026-08-22 repair (`_tally`
carrying `stats.unexpected` into `failed`, `_walk_suites` descending at any
depth, `failures_unparsed` never folded into `passed`) is present in this tree
and is what makes those three numbers reconcile.

### One base URL spelling, so the CSRF cookie-jar split cannot be hiding a pass

`ICDEV_E2E_BASE_URL` was unset and `ICDEV_DASHBOARD_URL` was
`http://localhost:5050`, so `resolveBaseUrl()` returned one origin for the
config, the auth fixture and every spec-local `BASE`. That is the condition
`qa-fail-e2e-baseurl-01` / `qa-fail-a5dbf266dfb0ce4a` exist for: with two
spellings in play the fixture mints its cookies at one host and the spec calls
the other, and every mutating request answers `403 CSRF_FAILED`. It could not
have happened on this run, and the 16 specs the 2026-08-22 sweep listed under
that cause all passed here.

## 3. Failures — none, so no cards were filed

`file_failure_tasks` was not invoked and `ace_qa_failures` holds 0 rows for this
run id. That is the correct outcome of step 3 and not a step that was skipped:
`--file-failures` is opt-in in this runner precisely so that one shared cause
does not become N cards and N duplicate PRs, and here the failure list is empty
either way.

The two cards the previous sweep filed were re-checked rather than assumed:

* **`qa-fail-49655511c721a165`** (30 API POSTs answering `403 CSRF_FAILED`
  across 16 specs) — `done`. All 16 named specs are in batches 0-8 of this run
  and all passed.
* **`qa-fail-5f7cf03a0b0a4351`** (`GET /coworker/<id>` serving the JSON fallback
  instead of the page) — `done`, and re-derived by hand the same way the
  previous report reproduced it:
  `curl -H 'Accept: text/html' http://localhost:5050/coworker/ace-6c446f1065d9`
  now returns **200 `text/html; charset=utf-8`** carrying the `CUI // SP-CTI`
  banner, against the 200 `application/json` it returned on 2026-08-22.

## 4. The 16 skips, enumerated

The previous report noted that its 44 skips were not enumerated and that the
E2E suite has no skip census. All 16 here carry an explicit reason, so they can
be listed in full. **A skip is an unmeasured test, not a passing one** — these
16 are outside the 814.

| n | spec | reason |
|---|---|---|
| 5 | `e2e_me_conflict_lifecycle.spec.ts` | seed session `sess-9cc6891cb548` fixture (requirements) not present in this environment |
| 4 | `clawhub.spec.ts` | ClawHub service not running on port 5077 |
| 4 | `skillhub.spec.ts` | SkillHub service not running on port 5077 |
| 1 | `dwo_mcp_step_execution.spec.ts` | opt-in until `e2p-back-03` moves the E2E suite to PostgreSQL — set `ICDEV_E2E_DWO_MCP=1` |
| 1 | `dwo_restart_durability.spec.ts` | opt-in — set `ICDEV_E2E_DWO_RESTART=1`; the spec restarts a dashboard process it owns |
| 1 | `dwo_trigger_linkage.spec.ts` | opt-in — set `ICDEV_E2E_DWO_TRIGGER=1`; the spec spawns a gateway process it owns |

Three shapes, three different repairs: 8 are a **service this host does not run**
(port 5077), 3 are **deliberately opt-in** behind an env flag, and 5 are a
**missing seed fixture**. None is an unexplained skip.

## 5. What this sweep did NOT measure

* **It is not a hermetic run.** `webServer` reports `playwright-managed` with
  `reuseExistingServer: true` and a dashboard already listening on 5050, so the
  suite exercised the live PostgreSQL deployment rather than a fresh database.
  Green here is a statement about this deployment; it is not the same evidence a
  clean-database run would give, and the 5 fixture-dependent skips above are
  exactly where that shows.
* **`screenshot_count` is structurally 0, and still is.** The runner exports
  `PLAYWRIGHT_SCREENSHOT_DIR` and **nothing reads it** — not
  `playwright.config.ts`, not `globalSetup.ts`, not one spec (verified by grep
  over `tests/`); `playwright/screenshots/qa-agent/qa-1788050936/` is empty. So
  the column can never be anything but 0 and must not be read as "no screenshots
  were taken". This was flagged as unfixed on 2026-08-22 and is unfixed here.
  It did not cost this sweep anything, because `_walk_suites` reads each
  failure's screenshot path off the Playwright **attachments** instead — so the
  card's "screenshot path required" is satisfiable per failure; only the
  aggregate count is dead. Not repaired here: with 0 failures there is no red
  case to prove a fix against, and inventing one is out of scope for a sweep.
* **`ace_qa_runs.completed_at` is NULL for every row in the table**, this one
  included — `record_run`'s INSERT does not name the column, so the 39.1 minutes
  are recoverable only from the batch reports, not from the run row.
* Skipped tests are Playwright `skipped`; the suite still has no skip *census*
  in the sense `args/ci_skip_census.txt` means for pytest. This section is a
  hand enumeration of one run, not a gate.
* Only the E2E suite and the route smoke ran. The pytest suite, the coherence
  tiers and the CI gates are not in this card's scope.

## Re-derive

```
python tools/testing/route_smoke.py --all --json
python tools/testing/qa_agent_runner.py --run --json --trigger kanban-task-qa-sweep-c17bd3d6
python tools/testing/qa_agent_runner.py --status qa-1788050936 --json
curl -H 'Accept: text/html' http://localhost:5050/coworker/<an ace_instances id>   # finding B
```

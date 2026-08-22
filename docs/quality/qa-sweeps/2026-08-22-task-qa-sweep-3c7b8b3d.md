# QA Agent — Full E2E Suite Sweep, 2026-08-22 (task-qa-sweep-3c7b8b3d)

Run id `qa-1787358426` (`ace_qa_runs`), trigger `kanban:task-qa-sweep-3c7b8b3d`,
against the live dashboard on `127.0.0.1:5050` (`ICDEV_NO_SERVER=1`) with the
PostgreSQL board. Retry #2 of the card: the first attempt ran the sweep and
produced no committed output.

## 1. Route smoke — clean

`python tools/testing/route_smoke.py --all --json`: **88 routes, 0 failures, 0
returning 500**, nothing skipped by cap, nothing tolerated, no stale expect-fail.

## 2. Full E2E run — 830 tests, 755 passed, 31 failed, 44 skipped

`ICDEV_NO_SERVER=1 python tools/testing/qa_agent_runner.py --run --json`
— 65 of 65 spec files measured in 11 batches (34 minutes, no batch deadline-killed,
no batch without a report).

| batch | expected | unexpected | skipped | seconds |
|---|---|---|---|---|
| 0 | 133 | 0 | 0 | 268 |
| 1 | 41 | 0 | 4 | 108 |
| 2 | 88 | **12** | 8 | 225 |
| 3 | 9 | 0 | 8 | 33 |
| 4 | 60 | **7** | 0 | 228 |
| 5 | 27 | **5** | 8 | 80 |
| 6 | 46 | 0 | 0 | 161 |
| 7 | 135 | **4** | 0 | 361 |
| 8 | 102 | **1** | 0 | 297 |
| 9 | 67 | **1** | 4 | 125 |
| 10 | 47 | **1** | 12 | 177 |

### The runner reported this sweep as `status=passed failed=0`

That is the first finding, and it is in the tool the card asked to run.
`qa_agent_runner.run_e2e_suite` set `failed = len(result.failures)`, and
`parse_playwright_json` walked ONE suite level. Playwright's JSON reporter
nests a FILE suite (no specs of its own) over one suite per `test.describe`,
and every spec under `tests/e2e/` sits inside a describe block — measured on
every batch report of this run: 6 file suites, 0 specs each, 1 child each.
So the parser named zero failures, `failed` read 0, `derive_status` answered
`passed`, and the 31 red tests were inside `total` but nowhere else. The
existing unit fixtures were flat (one level), which is why the parser's tests
were green.

Fixed in this PR (`icdev/tools/testing/qa_agent_runner.py`):

* `_tally` carries Playwright's own `stats.unexpected` into `failed` — the
  measurement, not the parser's count.
* `_walk_suites` descends the suite tree at any depth; the innermost suite
  names the test and the file is inherited from whichever ancestor carries it.
* `QARunResult.failures_unparsed` = `failed − len(failures)`, reported with a
  warning and never folded into `passed`: a parser blind spot cannot turn a
  red sweep green again.

Red-first regression tests added to `tests/test_qa_agent_runner.py`, which
leaves `args/ci_test_backlog.txt` and is gated by
`args/ci_test_files/core.d/task-qa-sweep-3c7b8b3d.txt` (`backlog_max` 1698 → 1697).

The `ace_qa_runs` row for `qa-1787358426` was written with the CORRECTED
aggregation (status `failed`, 31 failed) from the 11 batch reports, and all 31
failures are in `ace_qa_failures` for that run id, each linked to its
root-cause card.

## 3. Failures, by root cause — two cards, not thirty-one

### A. `qa-fail-49655511c721a165` — 30 API POSTs answer `403 CSRF_FAILED` (16 specs)

One defect in the E2E harness. Each spec imports `test`/`expect` from
`@playwright/test` and makes mutating calls through a raw
`page.request`/`APIRequestContext`, which carries neither `X-CSRF-Token` nor
`Sec-Fetch-Site`; `tools/security/csrf.py::csrf_protect` answers 403 before
the handler runs. CI never sees it because `ICDEV_AUTH_BYPASS` disarms CSRF
there. `tests/e2e/fixtures/auth.ts` (tsh-e2e-01-d2) already solves it and the
specs that import from it (`cpmp_portfolio`, `ai_ify` via #1843,
`dwo_mcp_step_execution`) pass. The fix is the import line in each of:

`cpmp_cdrl`, `cpmp_evm`, `cpmp_performance`, `govcon_capabilities`,
`govcon_drafting`, `govcon_pipeline`, `govcon_proposals`, `govcon_requirements`,
`idp_portal`, `integrity_backdoor_quarantine`, `kanban_api`, `kanban_pipeline`,
`noc_canvas`, `proposals_sections`, `slides_canvas`, `wfc_lifecycle`
(`wfc_lifecycle`'s `beforeAll` POST is the same 403; its detail test then fails
with `formId must be set by beforeAll` — a cascade, counted here).

Not a dashboard defect; no route regressed. It is the same shape the
2026-08-20 sweep found on `ai_ify.spec.ts` (`qa-fail-7fa5944682ed08ed`),
fixed there for one spec.

### B. `qa-fail-5f7cf03a0b0a4351` — `GET /coworker/<id>` serves JSON, not the page

Reproduced by hand against an existing `ace_instances` row:
`curl -H 'Accept: text/html' http://127.0.0.1:5050/coworker/<id>` returns
**200 `application/json`** `{"artifacts":[],"coworkers":[...]}`.
`tools/ace/blueprint.py::instance_detail` wraps `render_template` in an
`except Exception` that logs at INFO and returns the row data as JSON 200, so a
template failure produces no 500, no `route_smoke` signal and no CUI banner —
the E2E assertion `toContain('CUI // SP-CTI')` is the only thing that noticed.
The repair is to find why `coworker/instance.html` raises for this instance
and to make the fallback fail visibly on a page route.

## 4. What this sweep did NOT measure

* 44 skipped tests are Playwright `skipped`, not failures; they are not
  enumerated here. The E2E suite has no skip census.
* `screenshot_count` is 0 because the runner globs
  `playwright/screenshots/qa-agent/`, while the specs write their own paths and
  Playwright attaches `test-failed-1.png` under `.tmp/test_runs/`; the 31
  failure rows carry the attachment path. Not fixed here.
* The coherence `fast` tier and the CI test gates were run on the changed set;
  the full in-suite pytest run is CI's.

## Re-derive

```
python tools/testing/route_smoke.py --all --json
ICDEV_NO_SERVER=1 python tools/testing/qa_agent_runner.py --run --json
ICDEV_NO_SERVER=1 python tools/testing/qa_agent_runner.py --run --canvas cpmp --json   # card A, ~4 min
python -m pytest tests/test_qa_agent_runner.py -q
```

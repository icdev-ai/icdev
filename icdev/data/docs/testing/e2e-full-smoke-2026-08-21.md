# CUI // SP-CTI

# Playwright E2E Full Smoke — 2026-08-21 (task-e2e-be1bfa2f)

Full-suite run against all menus, use cases, canvases, and chat right panels.
Successor to [`e2e-full-smoke-2026-08-08.md`](e2e-full-smoke-2026-08-08.md).

## How it was run

The worktree carried **no `.env`**, so the first attempt attached to the shared
checkout's dashboard on `localhost:5050` — the canonical `icdev` board, where
the live scheduler dispatches whatever kanban fixtures the suite writes. That
run was killed after two tests. The `.env` was copied in and the run repeated
against an isolated port and a throwaway database, as the 2026-08-08 report
prescribes.

The run that produced these numbers:

```bash
ICDEV_DASHBOARD_PORT=5180 \
ICDEV_DASHBOARD_HOST=127.0.0.1 \
ICDEV_DASHBOARD_URL=http://127.0.0.1:5180 \
ICDEV_E2E_BASE_URL=http://127.0.0.1:5180 \
ICDEV_PG_DATABASE=icdev_e2e ICDEV_PG_DB=icdev_e2e \
ICDEV_PW_RUN_TAG=e2ebe1b \
  npx playwright test tests/e2e/ --project=chromium
```

`icdev_e2e` (1771 tables, 287 migrations) is the throwaway database. Port 5180
was probed free first — 5090, the port named in the config's own isolation
example, has been occupied by another session before.

Three things worth carrying to the next run:

1. **`ICDEV_E2E_BASE_URL` is now the right lever** (it did not exist on
   2026-08-08). It is read *before* `ICDEV_DASHBOARD_URL`, so a deployment can
   keep the container gateway for agents and still point the suite at the host.
   `ICDEV_DASHBOARD_URL` is overridden here as well, so that nothing *inside*
   the run reaches back to the shared dashboard.

2. **Do not pass `--reporter=line,html`.** `--reporter` REPLACES the config's
   reporter list, and the html reporter then falls back to its own default
   output folder — `playwright-report/`, which is **tracked in git**. Playwright
   CLEARS that folder on start, so the aborted first attempt deleted 1425
   tracked files before a single assertion ran (restored with
   `git checkout -- playwright-report/`). The config's own html reporter writes
   to `.tmp/test_runs/playwright-report<RUN_TAG>` instead. Passing no
   `--reporter` at all gives `list` + `json` + `html`, all three in the right
   place — and the JSON file is where the counts below come from.

3. **Starting a dashboard rewrites `args/projects.yaml`.** The project
   auto-registration writer fires on boot and rewrote the file in the worktree
   (+2611/-1511, mostly a CRLF-to-LF pass plus auto-registered cards). It is a
   side effect of running the app, not of the change under test — revert it
   before committing.

## Route smoke (gate, step 1)

`python tools/testing/route_smoke.py --all` — **PASS, 89/89** (79 nav routes +
10 API endpoints), no failures, so the full suite ran.

## Results

| Outcome | Count |
|---|---|
| Passed | 807 |
| Failed | 5 |
| Skipped | 26 |
| Flaky | 0 |
| **Total** | **838** (79 files) |

Wall clock 26.6m. Exit code 1. No "did not run" bucket this time — the
2026-08-08 run lost 12 tests to `describe.serial` cascade and this one lost
none.

Against 2026-08-08 (792 passed / 9 failed / 24 skipped / 12 not run): **9
failures down to 5**, and one of those 5 is fixed here.

## Failures

| # | Spec | Failure | Status |
|---|---|---|---|
| 1 | `canvases_extended.spec.ts:105` | `GET /ops` exceeded the 10s action timeout | **NOT REPRODUCIBLE** — cold-cache only |
| 2 | `noc_canvas.spec.ts:120` | `POST /api/noc/mops/generate` timed out; underlying 500 | **500 FIXED**, timeout remains |
| 3 | `integrity_backdoor_quarantine.spec.ts:40` | `POST /api/integrity/assess` exceeded the 10s action timeout | Open — endpoint correct, 34.4s |
| 4 | `govcon_pipeline.spec.ts:30` | `POST /api/govcon/sam/scan` exceeded the 10s action timeout | Open — external SAM.gov call |
| 5 | `coworker_lifecycle.spec.ts:167` | `GET /coworker/<id>` served JSON, no CUI banner | Open since 2026-08-08 |

**Four of the five are the 10s `actionTimeout`, not an assertion.** That matters,
because a timeout says nothing about whether the endpoint is correct — the call
never returned a status for the test to judge. Each was therefore re-driven
directly against an isolated dashboard on the same database, and the four
answers are genuinely different. Only #5 is an assertion failure.

### Fixed: `POST /api/noc/mops/generate` returned 500 (product defect)

Reproduced directly: **HTTP 500 after 35.7s**. The server traceback is
`RuntimeError: Failed to save MOP` from `tools/noc_canvas/mop_generator.py:170`
— a generic error raised by a loop that tries the `%s` and `?` placeholder
styles in turn and `except Exception: continue`s past both, so the real cause
never reaches the log. Running the same INSERT outside that loop produces it:

```
CheckViolation: new row for relation "noc_mops" violates check constraint
"noc_mops_generated_by_check"
```

`generate_mop` records `generated_by='ai_template'` when the LLM is unavailable
and it falls back to the deterministic template — the common CI / air-gap path,
and the path this run took. The live constraint admitted only
`('manual','ai')`.

**The source tree was already correct everywhere.** `MOP_GENERATED_BY` in
`tools/noc_canvas/constants.py`, the canvas DDL in
`tools/noc_canvas/db/init_db.py`, the `pg_consolidated.sql` baseline, and
migration `278_noc_mops_generated_by_ai_template.sql` all carry `'ai_template'`.
Only the databases were behind — and `schema_migrations` claimed 278 had been
applied, on `icdev_e2e` **and** on the canonical `icdev`, while on neither had
the constraint moved.

`migration_runner._detect_engine`'s own docstring names the mechanism:

> Defaulting to "sqlite" while connected to PostgreSQL is silent data loss:
> `_filter_sql` drops every `@pg-only` statement, and the migration is then
> recorded as applied.

278 is entirely `@pg-only`, so it was reduced to nothing and stamped. That
detection has since been fixed, but the stamped row means 278 can never run
again — a correction has to be a NEW migration. This run adds
`20260821032741_widen_noc_mops_generated_by_check_reapply` (mirrored into
`icdev/tools/db/migrations/`), idempotent via `DROP CONSTRAINT IF EXISTS`, so a
database that did get 278 is unchanged.

After it, the endpoint returns **201 with 14 generated steps**.

One caution about applying it. `migrate.py:90` reads `ICDEV_STORAGE_BACKEND`
from the **process environment**, and that does not come from `.env`. The first
`--up` invocation here was made without it set explicitly; it converged the
canonical `icdev` database as well as `icdev_e2e`, bringing over three
migrations already on `main` (`20260819021003`, `20260819030255`,
`20260820231102`) alongside this one. The outcome is the intended widening on
both databases, but the invocation reached wider than intended. Set the
variable explicitly.

**The test still fails.** The 500 is gone; the endpoint takes **11.3s** against
a 10s action timeout. See the section below.

### Not reproducible: `GET /ops` (#1)

13.9s and HTTP 200 on the first hit of a cold server. On a warm server the same
test passes in 8.1s. Cold-cache flake against a 10s budget, not a defect — it is
the first `/ops` request the process ever serves, and it fell where it did
because that spec runs early.

### Open, correct but slow: `/api/integrity/assess` (#3)

Driven directly: **HTTP 201 in 34.4s**, `verdict: "quarantine"`,
`risk_score: 100.0`, with the sast / secrets / semgrep scanners all reporting
`success: true` and persisting findings. That is *exactly* what
`integrity_backdoor_quarantine.spec.ts:40` asserts. The endpoint runs three real
static analysers over the fixture; it cannot meet a 10s budget, and there is no
product defect here.

### Open, and external: `/api/govcon/sam/scan` (#4)

Driven directly: **HTTP 200 in 15.9s** — a status the test accepts. The delay is
the outbound SAM.gov call.

Separately, and not something the test asserts: that call comes back
`Unauthorized` from `https://api.sam.gov/opportunities/v2/search`, so the scan
returns `total_fetched: 0` with the error carried inside a 200 payload. The
configured SAM API key is not being accepted. That deserves a card of its own —
**the scan reports success while fetching nothing**, which no assertion in this
suite can currently see.

### Open: `GET /coworker/<id>` has no CUI banner (#5)

Unchanged from 2026-08-08. The route serves a JSON body
(`{"artifacts": [], "coworkers": [...]}`) where the test expects an HTML page
carrying `CUI // SP-CTI`. Still open.

## The 10s action timeout is now the largest single cause

Three of the four remaining failures are `actionTimeout: 10000` in
`playwright.config.ts` against endpoints measured at 11.3s, 15.9s and 34.4s,
each of which returns the status its test asserts. That is one cause wearing
three faces, and it is worth deciding deliberately rather than per-spec:

- **Do not raise the global `actionTimeout`.** It is the budget all 838 tests
  run under, and slackening it to accommodate a 34s static-analysis sweep would
  hide a real regression in the other 834.
- The per-call fix is `request.post(url, { timeout: N })` on those three calls,
  with the measured cost written next to each one.
- `/api/govcon/sam/scan` is a different question again: its budget is an
  external service's latency, which no local number can bound. Its E2E value is
  "the route is wired", and that is provable without waiting for SAM.gov.

Not done here — three test edits with three different justifications is its own
card, not part of a smoke run.

## Artifacts

- Screenshots: `.tmp/test_runs/screenshots/`
- JSON results: `.tmp/test_runs/playwright-results-e2ebe1b.json`
- HTML report: `npx playwright show-report .tmp/test_runs/playwright-report-e2ebe1b`
- Structured build log: recorded via `build_logger.capture_playwright`
  (`playwright_run`, returncode 1, 807 passed / 5 failed / 26 skipped)

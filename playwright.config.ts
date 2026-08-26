// CUI // SP-CTI
// ICDEV™ Playwright Configuration
// Native browser test runner for E2E testing

import { defineConfig, devices } from '@playwright/test';
import path from 'path';
import { logEnvironmentDiagnostics } from './globalSetup';
import { resolveBaseUrl } from './tests/e2e/fixtures/base_url';

// Root is always the directory that contains this config file — immune to cwd changes.
const ROOT = __dirname;

// Suffix for this invocation's report + artifact paths (dwo-vv-03-d5).
//
// The reporter's outputFile / outputFolder and `outputDir` are fixed paths, and
// Playwright CLEARS outputDir on every run. So two `npx playwright test` calls in
// one CI job — the shared sweep, then the opt-in DWO V&V specs — silently
// overwrite each other's results.json and wipe each other's traces, and the
// uploaded artifact ends up describing only whichever ran last. Setting
// ICDEV_PW_RUN_TAG gives an invocation its own set of paths; unset (the default,
// and every local run) the paths are exactly what they were.
const RUN_TAG = process.env.ICDEV_PW_RUN_TAG ? `-${process.env.ICDEV_PW_RUN_TAG}` : '';

// Port the dashboard under test listens on.
//
// `reuseExistingServer` means an already-running dashboard on this port is used
// instead of starting one. That is convenient and was also a trap: a local run
// silently attached to whatever dashboard happened to be up — a different
// database, and without ICDEV_AUTH_BYPASS, so every mutating request came back
// 403 CSRF_FAILED and looked like a product defect. Overriding the port gives a
// run its own isolated server:
//
//   ICDEV_DASHBOARD_PORT=5090 ICDEV_PG_DATABASE=icdev_e2e npx playwright test
//
// ICDEV_DASHBOARD_PORT is read by `resolveBaseUrl()` below; there is no local
// PORT constant, so the port cannot be resolved one way here and another way in
// the fixtures.

// Base URL under test — and ONE VARIABLE WAS ANSWERING TWO QUESTIONS
// (qa-fail-e2e-baseurl-01).
//
// `ICDEV_DASHBOARD_URL` means "how does a process reach the dashboard", and
// `.env` set it to `http://host.docker.internal:5050` — deliberately, and
// correctly, for an agent running INSIDE a container. This config then read the
// same variable as the URL a test runner ON the host navigates to, which that
// value can never be. Every `page.goto` burned the full 30s navigationTimeout
// and died with net::ERR_CONNECTION_TIMED_OUT. MEASURED, same three specs,
// nothing else changed: 43 of 45 FAILED on host.docker.internal:5050 against
// 14 passed / 1 failed on localhost:5050.
//
// `ICDEV_E2E_BASE_URL` is the answer to the SECOND question only, so a
// deployment can keep the container gateway for agents and still run the suite:
//
//   ICDEV_E2E_BASE_URL=http://localhost:5050 npx playwright test
//
// `ICDEV_DASHBOARD_URL` is still honoured after it — pointing the suite at a
// remote dashboard that way is a legitimate use and removing it would break
// setups that do. What stops a wrong value costing 838 test failures is not
// precedence but globalSetup's reachability assert, which fails the run ONCE
// and names this exact case.
//
// The expression itself now lives in `tests/e2e/fixtures/base_url.ts` and is
// imported by `fixtures/auth.ts` and `fixtures/govcon_cpmp.ts` too. Those two
// carried their own copy that read ICDEV_DASHBOARD_URL alone, so a run setting
// ICDEV_E2E_BASE_URL bootstrapped the CSRF session on one host spelling and
// issued the specs' requests on the other — same server, two cookie jars, and
// every mutating request 403 CSRF_FAILED (qa-fail-84f92cebcf4fe498).
const DASHBOARD_URL = resolveBaseUrl();

/**
 * ICDEV™ Playwright Test Configuration
 *
 * Aligns with existing playwright-mcp-config.json settings:
 * - Chromium headless, 1920x1080 viewport, video recording
 *
 * Run: npx playwright test
 * Run specific: npx playwright test tests/e2e/dashboard_health.spec.ts
 * Report: npx playwright show-report
 */
const config = defineConfig({
  testDir: path.resolve(ROOT, 'tests/e2e'),
  // Wired as the documented hook, but the report is emitted at config load
  // instead (see below) — Playwright skips globalSetup for `--list`.
  globalSetup: path.resolve(ROOT, 'globalSetup.ts'),
  timeout: 60000, // cold-start server + beforeEach login flows need >30s
  // Sequential for Gov/DoD audit traceability. UNCHANGED, and deliberately so:
  // CI parallelises this suite by SHARDING it across jobs (`--shard=k/N`), not
  // by raising `workers` inside one job. Order within a shard therefore stays
  // deterministic and every spec still runs exactly once — the traceability
  // property is about a spec's execution being reproducible, and splitting the
  // suite across runners does not weaken that, whereas interleaving specs in
  // one process would.
  fullyParallel: false,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  // Single worker for deterministic execution order — WITHIN a shard. Do not
  // raise this to parallelise: the specs share one dashboard server and one
  // database per job, so concurrent workers would contend on both. Scale by
  // adding shards (each gets its own server and its own PostgreSQL container),
  // not by adding workers to a shard.
  workers: 1,
  reporter: [
    ['list'],
    ['json', { outputFile: path.resolve(ROOT, `.tmp/test_runs/playwright-results${RUN_TAG}.json`) }],
    ['html', { outputFolder: path.resolve(ROOT, `.tmp/test_runs/playwright-report${RUN_TAG}`), open: 'never' }],
  ],
  outputDir: path.resolve(ROOT, `.tmp/test_runs/playwright-artifacts${RUN_TAG}`),

  use: {
    baseURL: DASHBOARD_URL,
    trace: 'on-first-retry',
    screenshot: 'on',
    video: 'on',
    viewport: { width: 1920, height: 1080 },
    headless: true,
    actionTimeout: 10000,
    navigationTimeout: 30000,
  },

  projects: [
    {
      name: 'chromium',
      use: {
        ...devices['Desktop Chrome'],
        // Use a SYSTEM-INSTALLED browser instead of Playwright's downloaded
        // Chromium. Enclaves that cannot pull the ~267MB browser bundle can
        // still run E2E against the Chrome or Edge already on the box:
        //
        //   ICDEV_PLAYWRIGHT_CHANNEL=chrome    (or msedge / chrome-beta / msedge-dev)
        //   ICDEV_PLAYWRIGHT_EXECUTABLE=C:/Program Files/.../chrome.exe
        //
        // Unset (the default) keeps bundled Chromium, so connected machines and
        // CI are unaffected. See docs/ops/airgap-runbook.md §11.
        ...(process.env.ICDEV_PLAYWRIGHT_CHANNEL
          ? { channel: process.env.ICDEV_PLAYWRIGHT_CHANNEL }
          : {}),
        ...(process.env.ICDEV_PLAYWRIGHT_EXECUTABLE
          ? { launchOptions: { executablePath: process.env.ICDEV_PLAYWRIGHT_EXECUTABLE } }
          : {}),
      },
    },
    // firefox and webkit removed for demo run — chromium covers all functional checks
  ],

  // Dashboard server configuration
  // Always configure webServer so Playwright starts the app when needed.
  // reuseExistingServer:true means: if that port is already up, skip start —
  // set ICDEV_DASHBOARD_PORT to get an isolated server instead (see above).
  // Set ICDEV_NO_SERVER=1 to disable (e.g. when an external server manages lifecycle).
  webServer: process.env.ICDEV_NO_SERVER ? undefined : {
    command: `python ${path.resolve(ROOT, 'tools/dashboard/app.py')}`,
    url: DASHBOARD_URL,
    reuseExistingServer: true,
    timeout: 60000,
    cwd: ROOT,
    env: {
      ICDEV_GOVCON_ENABLED: 'true',
      // e2p-back-03: this env no longer pins SQLite.
      //
      // It applies only when Playwright starts the dashboard itself. CI sets
      // ICDEV_NO_SERVER=1, so `webServer` is undefined there and the job's own
      // ICDEV_STORAGE_BACKEND=postgresql governs — CI has always exercised the
      // primary backend. A local run used to pin `sqlite` plus
      // ICDEV_ALLOW_SQLITE_SERVER (which opts past the boot guard in
      // tools/db/backend_guard.py), so ~800 tests ran against the fallback:
      // no PG-only defect was visible, and the run depended on data/icdev.db,
      // which nothing maintains and which has drifted before.
      //
      // Both lines are now gone, so the backend comes from .env like every
      // other entry point — PostgreSQL. Nothing is pinned here on purpose: a
      // config that hardcoded a database would either point every developer at
      // the canonical one or need credentials in the repo.
      //
      // e2p-back-01 — MEASURED, so the switch is not a guess:
      //
      //   full suite on PostgreSQL   732 passed, 55 failed, 25 skipped (41.5m)
      //
      // The same ten specs holding most of those failures, run on each backend
      // with nothing else changed:
      //
      //   SQLite       166 passed, 51 failed
      //   PostgreSQL   172 passed, 45 failed
      //
      // So the ~55 local failures are PRE-EXISTING and backend-independent —
      // PostgreSQL fixes six of them and breaks none. Sampling the errors agrees:
      // they are 403s and timeouts on an onboarding overlay, not SQL. The local
      // suite's gap versus CI is real and still unexplained, but it is not the
      // backend, and it is not a reason to keep running on the fallback.
      //
      // POINT LOCAL RUNS AT A THROWAWAY DATABASE. This env merges with
      // process.env, so the database is selectable per run, and the suite
      // writes fixtures — running it against the canonical `icdev` would leave
      // them there. CI uses a disposable container for exactly this reason:
      //
      //   python tools/db/bootstrap_pg.py          # once, with the vars below
      //   ICDEV_PG_DATABASE=icdev_e2e ICDEV_PG_DB=icdev_e2e \
      //     npx playwright test
      ICDEV_AAC_ENABLED: 'true',
      ICDEV_CUI_BANNER_ENABLED: 'true',
      ICDEV_MISSION_CANVAS_ENABLED: 'true',
      ICDEV_AUTH_BYPASS: 'true',
      // nav-sec-01: local E2E server opts into dev auto-login explicitly now
      // that merely having ICDEV_DASHBOARD_API_KEY set no longer authenticates.
      ICDEV_DASHBOARD_DEV_AUTOLOGIN: 'true',
      ICDEV_OPS_HUB_ENABLED: 'true',
      // NOC Operations Canvas — required by tests/e2e/noc_canvas.spec.ts (cnr-ops-02).
      // Registry default_enabled is false; without this the noc_canvas blueprint
      // never registers and every /noc route 404s / redirects.
      ICDEV_NOCC_ENABLED: 'true',
      ICDEV_MIGRATION_CANVAS_ENABLED: 'true',
      ICDEV_INNOVATION_ENABLED: 'true',

      // ── Parity with the CI E2E job's env ────────────────────────────────
      //
      // These mirror .github/workflows/icdev-ci.yml jobs.e2e.env, which set
      // sixteen variables this config did not. The PG connection vars from that
      // block are deliberately NOT copied: they belong in .env, and hardcoding a
      // database here is what the note above warns against. If you add a flag to
      // the CI job, add it here too, or a local run stops predicting CI.
      //
      // Honest scope: adding these did NOT fix the local failures (measured —
      // 45 failed before and after on the same ten specs). They are here because
      // a local run diverging from CI on sixteen flags is wrong on its own, not
      // because they were the cause of anything. See e2p-back-01 in the note
      // above `webServer` for what the failures actually are.
      ICDEV_ACE_ENABLED: 'true',
      ICDEV_AIIFY_ENABLED: 'true',
      ICDEV_AIML_CANVAS_ENABLED: 'true',
      ICDEV_AISG_ENABLED: 'true',
      ICDEV_SLIDES_ENABLED: 'true',
      // Explicitly OFF in CI — leaving them unset locally lets a registry
      // default flip them on and change which routes exist.
      ICDEV_NDC_ENABLED: 'false',
      ICDEV_NETWORK_ENABLED: 'false',
      // Canvas-local stores stay on SQLite even in CI: these are separate
      // per-canvas databases, not the platform backend this card switched.
      AIMC_STORAGE_BACKEND: 'sqlite',
      MC_STORAGE_BACKEND: 'sqlite',
      SC_STORAGE_BACKEND: 'sqlite',
    },
  },
});

// tsh-e2e-01-d1: print the local-vs-CI environment diff before anything runs.
//
// Called here rather than left to `globalSetup` because Playwright does not run
// globalSetup for `npx playwright test --list` — and listing is exactly the
// cheap command you reach for when a local run disagrees with CI. Reading the
// values back off `config` keeps the report describing the config that is
// actually in force instead of a second copy of it that could drift.
//
// Silence with ICDEV_E2E_ENV_DIAG=0; diff against a real CI run's snapshot with
// ICDEV_E2E_ENV_BASELINE=<path to e2e-env-diagnostics.json>.
const webServer = config.webServer as { env?: Record<string, string> } | undefined;
logEnvironmentDiagnostics({
  root: ROOT,
  webServerEnv: webServer?.env,
  webServerActive: !!webServer,
  dashboardUrl: DASHBOARD_URL,
});

export default config;
// CUI // SP-CTI

// CUI // SP-CTI
// ICDEV™ Playwright Configuration
// Native browser test runner for E2E testing

import { defineConfig, devices } from '@playwright/test';
import path from 'path';

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
export default defineConfig({
  testDir: path.resolve(ROOT, 'tests/e2e'),
  timeout: 60000, // cold-start server + beforeEach login flows need >30s
  fullyParallel: false, // Sequential for Gov/DoD audit traceability
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  workers: 1, // Single worker for deterministic execution order
  reporter: [
    ['list'],
    ['json', { outputFile: path.resolve(ROOT, `.tmp/test_runs/playwright-results${RUN_TAG}.json`) }],
    ['html', { outputFolder: path.resolve(ROOT, `.tmp/test_runs/playwright-report${RUN_TAG}`), open: 'never' }],
  ],
  outputDir: path.resolve(ROOT, `.tmp/test_runs/playwright-artifacts${RUN_TAG}`),

  use: {
    baseURL: process.env.ICDEV_DASHBOARD_URL || 'http://localhost:5050',
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
      use: { ...devices['Desktop Chrome'] },
    },
    // firefox and webkit removed for demo run — chromium covers all functional checks
  ],

  // Dashboard server configuration
  // Always configure webServer so Playwright starts the app when needed.
  // reuseExistingServer:true means: if localhost:5050 is already up, skip start.
  // Set ICDEV_NO_SERVER=1 to disable (e.g. when an external server manages lifecycle).
  webServer: process.env.ICDEV_NO_SERVER ? undefined : {
    command: `python ${path.resolve(ROOT, 'tools/dashboard/app.py')}`,
    url: 'http://localhost:5050',
    reuseExistingServer: true,
    timeout: 60000,
    cwd: ROOT,
    env: {
      ICDEV_GOVCON_ENABLED: 'true',
      ICDEV_STORAGE_BACKEND: 'sqlite',
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
    },
  },
});
// CUI // SP-CTI

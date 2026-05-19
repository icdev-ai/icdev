// CUI // SP-CTI
// ICDEV™ Playwright Configuration
// Native browser test runner for E2E testing

import { defineConfig, devices } from '@playwright/test';
import path from 'path';

// Root is always the directory that contains this config file — immune to cwd changes.
const ROOT = __dirname;

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
    ['json', { outputFile: path.resolve(ROOT, '.tmp/test_runs/playwright-results.json') }],
    ['html', { outputFolder: path.resolve(ROOT, '.tmp/test_runs/playwright-report'), open: 'never' }],
  ],
  outputDir: path.resolve(ROOT, '.tmp/test_runs/playwright-artifacts'),

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
  },
});
// CUI // SP-CTI

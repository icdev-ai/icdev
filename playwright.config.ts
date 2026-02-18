// CUI // SP-CTI
// ICDEV Playwright Configuration
// Native browser test runner for E2E testing

import { defineConfig, devices } from '@playwright/test';

/**
 * ICDEV Playwright Test Configuration
 *
 * Aligns with existing playwright-mcp-config.json settings:
 * - Chromium headless, 1920x1080 viewport, video recording
 *
 * Run: npx playwright test
 * Run specific: npx playwright test tests/e2e/dashboard_health.spec.ts
 * Report: npx playwright show-report
 */
export default defineConfig({
  testDir: './tests/e2e',
  fullyParallel: false, // Sequential for Gov/DoD audit traceability
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  workers: 1, // Single worker for deterministic execution order
  reporter: [
    ['list'],
    ['json', { outputFile: '.tmp/test_runs/playwright-results.json' }],
    ['html', { outputFolder: '.tmp/test_runs/playwright-report', open: 'never' }],
  ],
  outputDir: '.tmp/test_runs/playwright-artifacts',

  use: {
    baseURL: process.env.ICDEV_DASHBOARD_URL || 'http://localhost:5000',
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
    // Additional browsers for cross-browser compliance verification
    {
      name: 'firefox',
      use: { ...devices['Desktop Firefox'] },
    },
    {
      name: 'webkit',
      use: { ...devices['Desktop Safari'] },
    },
  ],

  // Dashboard server configuration
  webServer: process.env.ICDEV_START_SERVER
    ? {
        command: 'python tools/dashboard/app.py',
        url: 'http://localhost:5000',
        reuseExistingServer: true,
        timeout: 30000,
      }
    : undefined,
});
// CUI // SP-CTI

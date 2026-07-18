// CUI // SP-CTI
// E2E Test: NSA ZIG (Zero Trust Implementation Guide) Pages
// Verifies /security/zig/* pages render their core widgets (radar canvas, phase
// progress, FY2027 readiness bar) and that each page injects exactly ONE
// `.iqe-widget` (provided once via security_canvas/base.html).
//
// How to run:
//   Native (Playwright):  npx playwright test tests/e2e/security_zig.spec.ts
//   Via ICDEV runner:     python tools/testing/e2e_runner.py \
//                           --test-file tests/e2e/security_zig.spec.ts --mode native
//   Discover:             python tools/testing/e2e_runner.py --discover
// Base URL comes from ICDEV_DASHBOARD_URL (default http://localhost:5050,
// see playwright.config.ts). The dashboard must be started with
// ICDEV_AUTH_BYPASS=1 so the auth-guarded /security/zig/* routes are reachable.
//
// Screenshot convention: playwright/screenshots/<name>.png (repo guardrail).
// Note: playwright.config.ts `outputDir` (.tmp/test_runs/playwright-artifacts)
// is wiped each run; these explicit screenshots live under playwright/screenshots
// and are NOT wiped by the runner.

import { test, expect } from '@playwright/test';
import path from 'path';
import fs from 'fs';

const SHOTS = path.resolve(__dirname, '../../playwright/screenshots');
const PAGE_TIMEOUT = 30_000;

// Every /security/zig/* page extends security_canvas/base.html, which includes
// the IQE query widget exactly once.
async function expectSingleIqeWidget(page: import('@playwright/test').Page) {
  await expect(page.locator('.iqe-widget')).toHaveCount(1);
}

test.describe('NSA ZIG Pages', () => {
  test.beforeAll(() => {
    fs.mkdirSync(SHOTS, { recursive: true });
  });

  test.beforeEach(async ({ page }) => {
    // Suppress first-run product tour overlays that can intercept the viewport.
    await page.addInitScript(() => {
      localStorage.setItem('icdev_tour_completed', '1');
      localStorage.setItem('icdev_tour_last_step', '999');
    });
  });

  test('zig dashboard renders radar, phase progress, and FY2027 bar', async ({ page }) => {
    await page.goto('/security/zig/');
    await page.waitForLoadState('domcontentloaded');
    await page.waitForSelector('.zig-hero, h1', { timeout: PAGE_TIMEOUT });

    // Hero heading
    await expect(page.locator('h1')).toContainText(/Zero Trust Implementation Guide/i);

    // Radar chart canvas
    await expect(page.locator('canvas#zigRadar')).toHaveCount(1);

    // Phase progress grid
    await expect(page.locator('.phase-grid')).toBeVisible();
    expect(await page.locator('.phase-grid .phase-box').count()).toBeGreaterThan(0);
    expect(await page.locator('.phase-box .phase-pct').count()).toBeGreaterThan(0);

    // FY2027 readiness bar
    await expect(page.locator('.fy-bar-container')).toBeVisible();
    await expect(page.locator('.fy-bar-container .fy-bar-fill')).toHaveCount(1);
    await expect(page.locator('.fy-bar-container .fy-bar-label')).toBeVisible();

    await expectSingleIqeWidget(page);

    await page.screenshot({ path: path.join(SHOTS, 'security_zig_01_dashboard.png'), fullPage: true });
  });

  test('pillar detail (user) renders hero and capability cards', async ({ page }) => {
    await page.goto('/security/zig/pillar/user');
    await page.waitForLoadState('domcontentloaded');
    await page.waitForSelector('.pillar-hero, h1', { timeout: PAGE_TIMEOUT });

    await expect(page.locator('.pillar-hero')).toBeVisible();
    await expect(page.locator('h1')).toContainText(/Pillar/i);
    expect(await page.locator('.cap-card').count()).toBeGreaterThan(0);

    await expectSingleIqeWidget(page);

    await page.screenshot({ path: path.join(SHOTS, 'security_zig_02_pillar_user.png'), fullPage: true });
  });

  test('phase tracker renders phase headers and progress rings', async ({ page }) => {
    await page.goto('/security/zig/phase');
    await page.waitForLoadState('domcontentloaded');
    await page.waitForSelector('.phase-header, h1', { timeout: PAGE_TIMEOUT });

    await expect(page.locator('h1')).toContainText(/Phase Tracker/i);
    expect(await page.locator('.phase-header').count()).toBeGreaterThan(0);
    expect(await page.locator('.phase-pct-ring').count()).toBeGreaterThan(0);

    await expectSingleIqeWidget(page);

    await page.screenshot({ path: path.join(SHOTS, 'security_zig_03_phase.png'), fullPage: true });
  });

  test('assessment page renders run button and score cards', async ({ page }) => {
    await page.goto('/security/zig/assessment');
    await page.waitForLoadState('domcontentloaded');
    await page.waitForSelector('#runAssessBtn, h1', { timeout: PAGE_TIMEOUT });

    await expect(page.locator('h1')).toContainText(/Gap Assessment/i);
    await expect(page.locator('#runAssessBtn')).toBeVisible();
    expect(await page.locator('.assess-card').count()).toBeGreaterThan(0);

    await expectSingleIqeWidget(page);

    await page.screenshot({ path: path.join(SHOTS, 'security_zig_04_assessment.png'), fullPage: true });
  });

  test('roadmap page renders stat grid and milestone timeline', async ({ page }) => {
    await page.goto('/security/zig/roadmap');
    await page.waitForLoadState('domcontentloaded');
    await page.waitForSelector('.stat-grid, h1', { timeout: PAGE_TIMEOUT });

    await expect(page.locator('h1')).toContainText(/Compliance Roadmap/i);
    await expect(page.locator('.stat-grid')).toBeVisible();
    await expect(page.locator('.timeline')).toBeVisible();
    expect(await page.locator('.milestone').count()).toBeGreaterThan(0);

    await expectSingleIqeWidget(page);

    await page.screenshot({ path: path.join(SHOTS, 'security_zig_05_roadmap.png'), fullPage: true });
  });

  test('portfolio page renders health panel and compare radar', async ({ page }) => {
    await page.goto('/security/zig/portfolio');
    await page.waitForLoadState('domcontentloaded');
    await page.waitForSelector('.port-hero, h1', { timeout: PAGE_TIMEOUT });

    await expect(page.locator('h1')).toContainText(/Portfolio/i);
    await expect(page.locator('.health-panel')).toBeVisible();
    await expect(page.locator('.health-panel .kpi-row')).toBeVisible();
    await expect(page.locator('canvas#portRadar')).toHaveCount(1);

    await expectSingleIqeWidget(page);

    await page.screenshot({ path: path.join(SHOTS, 'security_zig_06_portfolio.png'), fullPage: true });
  });
});
// CUI // SP-CTI

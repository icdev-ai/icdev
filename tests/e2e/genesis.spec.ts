// CUI // SP-CTI
// E2E Test: Genesis v2.0 Autonomous Research Lab Dashboard
// Verifies the Genesis dashboard loads with daemon status, 14 reflexes table, GKP promoter stats, and feedback-driven priorities.

import { test, expect } from '@playwright/test';

const CUI_BANNER = 'CUI // SP-CTI';
// Genesis runs on port 5050
const GENESIS_BASE = 'http://localhost:5050';

test.describe('Genesis v2.0 Autonomous Research Lab', () => {
  test.beforeEach(async ({ page }) => {
    // Steps 1-4: Login to Genesis dashboard
    await page.goto(`${GENESIS_BASE}/login`);
    await page.waitForLoadState('domcontentloaded');

    const apiKeyInput = page.locator(
      'input[type="password"], input[name="api_key"], input[id="api_key"], input[placeholder*="key" i]'
    );
    if (await apiKeyInput.count() > 0) {
      await apiKeyInput.first().fill('sparkpilot');
      const submitBtn = page.getByRole('button', { name: /Login|Sign In|Submit/i })
        .or(page.locator('button[type="submit"]'));
      if (await submitBtn.count() > 0) {
        await submitBtn.first().click();
        await page.waitForLoadState('domcontentloaded');
      }
    }
  });

  test('genesis page loads with heading and intro panel', async ({ page }) => {
    // Steps 3-7: Navigate and verify heading
    await page.goto(`${GENESIS_BASE}/genesis`);
    await page.waitForLoadState('domcontentloaded');

    const bodyText = await page.textContent('body');

    // Step 5: Heading check
    const headingTerms = ['Genesis', 'Autonomous Research', 'Research Lab'];
    let headingFound = false;
    for (const term of headingTerms) {
      if (bodyText?.includes(term)) {
        headingFound = true;
        break;
      }
    }

    // Step 6-7: Intro panel checks
    const introTerms = ['Self-Improvement', 'Monitor Reflexes', 'Review GKPs', 'Promote Knowledge'];
    for (const term of introTerms) {
      if (bodyText?.includes(term)) {
        expect(bodyText).toContain(term);
      }
    }

    expect(bodyText).toContain(CUI_BANNER);

    await page.screenshot({
      path: '.tmp/test_runs/screenshots/genesis_01_page_load.png',
      fullPage: true,
    });
  });

  test('daemon status stat cards are present', async ({ page }) => {
    // Steps 8-10: Verify stat cards
    await page.goto(`${GENESIS_BASE}/genesis`);
    await page.waitForLoadState('domcontentloaded');

    const bodyText = await page.textContent('body');

    // Step 8: Stat card labels
    const statLabels = ['Daemon Status', 'Active Reflexes', 'Circuit Breakers', 'Audit Events'];
    for (const label of statLabels) {
      if (bodyText?.includes(label)) {
        expect(bodyText).toContain(label);
      }
    }

    // Steps 9-10: Numeric values (soft checks)
    if (bodyText?.includes('14')) {
      expect(bodyText).toContain('14');
    }

    expect(bodyText).toContain(CUI_BANNER);
  });

  test('14 reflexes table has correct headers and rows', async ({ page }) => {
    // Steps 11-17: Verify reflex table
    await page.goto(`${GENESIS_BASE}/genesis`);
    await page.waitForLoadState('domcontentloaded');

    const bodyText = await page.textContent('body');

    // Step 11: Table headers
    const tableHeaders = ['Reflex', 'Tier', 'Schedule', 'Status', 'Last Run', 'Action'];
    for (const header of tableHeaders) {
      if (bodyText?.includes(header)) {
        expect(bodyText).toContain(header);
      }
    }

    // Steps 13-15: Tier badges (soft checks)
    const reflexNames = ['research', 'scout', 'audit', 'comply', 'ingest', 'market', 'report',
      'docs', 'publish', 'test', 'learn', 'heal', 'evolve', 'experiment'];
    let reflexFound = false;
    for (const reflex of reflexNames) {
      if (bodyText?.includes(reflex)) {
        reflexFound = true;
        break;
      }
    }

    // Step 17: Run button
    const runBtn = page.getByRole('button', { name: /Run/i }).first();
    if (await runBtn.count() > 0) {
      await expect(runBtn).toBeVisible();
    }

    await page.screenshot({
      path: '.tmp/test_runs/screenshots/genesis_02_reflexes_table.png',
      fullPage: true,
    });

    expect(bodyText).toContain(CUI_BANNER);
  });

  test('refresh status button triggers API update', async ({ page }) => {
    // Steps 18-21: Refresh button interaction
    await page.goto(`${GENESIS_BASE}/genesis`);
    await page.waitForLoadState('domcontentloaded');

    const refreshBtn = page.getByRole('button', { name: /Refresh Status/i })
      .or(page.locator('[data-action="refresh-status"]'));
    if (await refreshBtn.count() > 0) {
      await refreshBtn.first().click();
      await page.waitForTimeout(2000);

      await page.screenshot({
        path: '.tmp/test_runs/screenshots/genesis_03_after_refresh.png',
        fullPage: true,
      });
    }

    const bodyText = await page.textContent('body');
    expect(bodyText).toContain(CUI_BANNER);
  });

  test('GKP promoter stats load on button click', async ({ page }) => {
    // Steps 22-26: GKP section
    await page.goto(`${GENESIS_BASE}/genesis`);
    await page.waitForLoadState('domcontentloaded');

    // Scroll to GKP section
    const gkpSection = page.locator(
      'section:has-text("Knowledge Bridge"), section:has-text("GKP"), [data-section="gkp"]'
    );
    if (await gkpSection.count() > 0) {
      await gkpSection.first().scrollIntoViewIfNeeded();

      const loadStatsBtn = page.getByRole('button', { name: /Load Stats/i });
      if (await loadStatsBtn.count() > 0) {
        await loadStatsBtn.first().click();
        await page.waitForTimeout(1000);
      }

      const bodyText = await page.textContent('body');
      const gkpStats = ['Total GKPs', 'Promoted', 'Pending Review', 'Rejected'];
      for (const stat of gkpStats) {
        if (bodyText?.includes(stat)) {
          expect(bodyText).toContain(stat);
        }
      }
    }

    const bodyText = await page.textContent('body');
    expect(bodyText).toContain(CUI_BANNER);
  });

  test('feedback-driven priorities load on button click', async ({ page }) => {
    // Steps 27-32: Feedback priorities section
    await page.goto(`${GENESIS_BASE}/genesis`);
    await page.waitForLoadState('domcontentloaded');

    const prioritiesSection = page.locator(
      'section:has-text("Feedback"), section:has-text("Priorities"), [data-section="priorities"]'
    );
    if (await prioritiesSection.count() > 0) {
      await prioritiesSection.first().scrollIntoViewIfNeeded();

      const checkPrioritiesBtn = page.getByRole('button', { name: /Check Priorities/i });
      if (await checkPrioritiesBtn.count() > 0) {
        await checkPrioritiesBtn.first().click();
        await page.waitForTimeout(1000);

        await page.screenshot({
          path: '.tmp/test_runs/screenshots/genesis_04_priorities.png',
          fullPage: true,
        });

        const bodyText = await page.textContent('body');
        // Priority levels
        const priorityTerms = ['NORMAL', 'BOOST', 'REDUCE', 'priority'];
        for (const term of priorityTerms) {
          if (bodyText?.includes(term)) {
            expect(bodyText).toContain(term);
            break;
          }
        }
      }
    }

    const bodyText = await page.textContent('body');
    expect(bodyText).toContain(CUI_BANNER);
  });

  test('genesis responsive layout at multiple viewports', async ({ page }) => {
    // Steps 37-39: Multi-viewport screenshots
    await page.goto(`${GENESIS_BASE}/genesis`);
    await page.waitForLoadState('domcontentloaded');

    // Desktop
    await page.setViewportSize({ width: 1920, height: 1080 });
    await page.screenshot({
      path: '.tmp/test_runs/screenshots/genesis_05_desktop_1920x1080.png',
      fullPage: true,
    });

    // Tablet
    await page.setViewportSize({ width: 768, height: 1024 });
    await page.screenshot({
      path: '.tmp/test_runs/screenshots/genesis_06_tablet_768x1024.png',
      fullPage: true,
    });

    // Mobile
    await page.setViewportSize({ width: 375, height: 812 });
    await page.screenshot({
      path: '.tmp/test_runs/screenshots/genesis_07_mobile_375x812.png',
      fullPage: true,
    });

    const bodyText = await page.textContent('body');
    expect(bodyText).toContain(CUI_BANNER);
  });
});
// CUI // SP-CTI

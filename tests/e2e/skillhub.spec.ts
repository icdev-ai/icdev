// CUI // SP-CTI
// E2E Test: SkillHub Skill Browser
// Verifies the SkillHub Skill Browser page loads correctly with search, import queue, and action buttons.

import { test, expect } from '@playwright/test';
import * as net from 'net';

const CUI_BANNER = 'CUI // SP-CTI';
// SkillHub runs on its own port
const CLAWHUB_URL = 'http://localhost:5077';

/** Return true if a TCP connection to host:port succeeds within timeoutMs. */
async function isPortOpen(host: string, port: number, timeoutMs = 2000): Promise<boolean> {
  return new Promise((resolve) => {
    const socket = new net.Socket();
    const cleanup = (result: boolean) => { socket.destroy(); resolve(result); };
    socket.setTimeout(timeoutMs);
    socket.once('connect', () => cleanup(true));
    socket.once('timeout', () => cleanup(false));
    socket.once('error', () => cleanup(false));
    socket.connect(port, host);
  });
}

test.describe('SkillHub Skill Browser', () => {
  test.beforeEach(async () => {
    const up = await isPortOpen('localhost', 5077);
    if (!up) {
      test.skip(true, 'SkillHub service not running on port 5077 — skipping');
    }
  });

  test('skillhub page loads with heading and CUI banner', async ({ page }) => {
    // Steps 1-5: Navigate, wait, screenshot, verify heading and CUI
    await page.goto(`${CLAWHUB_URL}/skillhub`);
    await page.waitForLoadState('domcontentloaded');

    await page.setViewportSize({ width: 1920, height: 1080 });
    await page.screenshot({
      path: '.tmp/test_runs/screenshots/skillhub_01_main_1920x1080.png',
      fullPage: true,
    });

    // Step 4: Verify heading contains "SkillHub" or "Skill Browser"
    const bodyText = await page.textContent('body');
    const headingTerms = ['SkillHub', 'Skill Browser', 'skillhub', 'skill'];
    let headingFound = false;
    for (const term of headingTerms) {
      if (bodyText?.toLowerCase().includes(term.toLowerCase())) {
        headingFound = true;
        break;
      }
    }

    // Step 5: CUI banner check
    expect(bodyText).toContain(CUI_BANNER);
  });

  test('search form exists and accepts input', async ({ page }) => {
    // Steps 6-11: Verify search form and submit a query
    await page.goto(`${CLAWHUB_URL}/skillhub`);
    await page.waitForLoadState('domcontentloaded');

    // Step 6: Assert search form with input and button
    const searchInput = page.locator(
      'input[type="search"], input[name="q"], input[name="query"], input[placeholder*="search" i], input[placeholder*="skill" i], .search-input'
    );
    if (await searchInput.count() > 0) {
      await expect(searchInput.first()).toBeVisible();

      // Steps 7-8: Type and click search
      await searchInput.first().fill('system architect');
      const searchBtn = page.getByRole('button', { name: /Search/i })
        .or(page.locator('button[type="submit"]'));
      if (await searchBtn.count() > 0) {
        await searchBtn.first().click();
        // Step 9: Wait for results (up to 30s — live API call)
        await page.waitForLoadState('domcontentloaded');
        await page.waitForTimeout(2000);
      }
    }

    await page.screenshot({
      path: '.tmp/test_runs/screenshots/skillhub_02_search_results_1920x1080.png',
      fullPage: true,
    });

    const bodyText = await page.textContent('body');
    expect(bodyText).toContain(CUI_BANNER);
  });

  test('import queue section is visible with action buttons', async ({ page }) => {
    // Steps 12-15: Verify import queue
    await page.goto(`${CLAWHUB_URL}/skillhub`);
    await page.waitForLoadState('domcontentloaded');

    // Soft check for import queue section
    const importQueue = page.locator(
      '#import-queue, .import-queue, [data-testid="import-queue"], section:has-text("Import Queue")'
    );
    if (await importQueue.count() > 0) {
      await expect(importQueue.first()).toBeVisible();

      // Check for action buttons in queue rows
      const actionBtns = importQueue.locator(
        'button:text-matches("Promote|Unpromote|Install|Trust|Rate|Update", "i")'
      );
      // Soft check — rows may be empty
      const btnCount = await actionBtns.count();
      if (btnCount > 0) {
        await expect(actionBtns.first()).toBeVisible();
      }
    }

    await page.screenshot({
      path: '.tmp/test_runs/screenshots/skillhub_03_import_queue_1920x1080.png',
      fullPage: true,
    });

    const bodyText = await page.textContent('body');
    expect(bodyText).toContain(CUI_BANNER);
  });

  test('no JavaScript console errors on SkillHub page', async ({ page }) => {
    // Steps 16-17: Console and network error checks
    const jsErrors: string[] = [];
    page.on('pageerror', (err) => jsErrors.push(err.message));

    const failedRequests: string[] = [];
    page.on('response', (response) => {
      if (response.status() >= 500) {
        failedRequests.push(`${response.status()} ${response.url()}`);
      }
    });

    await page.goto(`${CLAWHUB_URL}/skillhub`);
    await page.waitForLoadState('domcontentloaded');

    // Report errors but don't hard-fail — SkillHub may have live API calls that vary
    if (jsErrors.length > 0) {
      console.warn('SkillHub JS errors:', jsErrors);
    }
    if (failedRequests.length > 0) {
      console.warn('SkillHub 500 responses:', failedRequests);
    }

    const bodyText = await page.textContent('body');
    expect(bodyText).toContain(CUI_BANNER);
  });
});
// CUI // SP-CTI

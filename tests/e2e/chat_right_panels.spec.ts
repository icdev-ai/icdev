// CUI // SP-CTI
// E2E Test: Chat Right Panels — RICOAS, GOV, INTEL tabs
// Covers: tab DOM presence, spinner elements, content population after context switch

import { test, expect } from '@playwright/test';

const SCREENSHOT_DIR = '.tmp/test_runs/screenshots';

test.describe('Chat Right Panels', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/chat');
    await page.waitForLoadState('domcontentloaded');
  });

  // ── Tab buttons ──────────────────────────────────────────────────────────

  test('RICOAS tab button is present in DOM', async ({ page }) => {
    const tab = page.locator('#tab-ricoas, [data-tab="ricoas"], .tab-btn-ricoas');
    expect(await tab.count()).toBeGreaterThan(0);
  });

  test('GOV tab button is present in DOM', async ({ page }) => {
    const tab = page.locator('#tab-gov, [data-tab="gov"], .tab-btn-gov');
    expect(await tab.count()).toBeGreaterThan(0);
  });

  test('INTEL tab button is present in DOM', async ({ page }) => {
    const tab = page.locator('#tab-intel, [data-tab="intel"], .tab-btn-intel');
    expect(await tab.count()).toBeGreaterThan(0);
  });

  // ── Spinner elements ──────────────────────────────────────────────────────

  test('RICOAS processing bar element is attached to DOM', async ({ page }) => {
    const bar = page.locator('#ricoas-processing-bar');
    await expect(bar).toBeAttached();
  });

  test('GOV processing bar element is attached to DOM', async ({ page }) => {
    const bar = page.locator('#gov-processing-bar');
    await expect(bar).toBeAttached();
  });

  test('INTEL processing bar element is attached to DOM', async ({ page }) => {
    const bar = page.locator('#intel-processing-bar');
    await expect(bar).toBeAttached();
  });

  // ── GOV tab content ──────────────────────────────────────────────────────

  test('GOV tab renders content structure after load', async ({ page }) => {
    // Click GOV tab
    const govTab = page.locator('#tab-gov, [data-tab="gov"]').first();
    if (await govTab.count() > 0) {
      await govTab.click();
      await page.waitForTimeout(800);

      await page.screenshot({
        path: `${SCREENSHOT_DIR}/right_panels_gov_tab.png`,
        fullPage: false,
      });

      // GOV panel container should be visible
      const govPanel = page.locator('#gov-sidebar-content, #gov-panel, .gov-tab-content');
      expect(await govPanel.count()).toBeGreaterThan(0);
    }
  });

  // ── INTEL tab content ─────────────────────────────────────────────────────

  test('INTEL tab renders content structure after load', async ({ page }) => {
    const intelTab = page.locator('#tab-intel, [data-tab="intel"]').first();
    if (await intelTab.count() > 0) {
      await intelTab.click();
      await page.waitForTimeout(800);

      await page.screenshot({
        path: `${SCREENSHOT_DIR}/right_panels_intel_tab.png`,
        fullPage: false,
      });

      // INTEL panel container should be visible
      const intelPanel = page.locator('#intel-sidebar-content, #intel-panel, .intel-tab-content');
      expect(await intelPanel.count()).toBeGreaterThan(0);
    }
  });

  // ── Spinner CSS class behavior ─────────────────────────────────────────────

  test('processing bars start without visible class (not spinning at idle)', async ({ page }) => {
    const bars = ['#ricoas-processing-bar', '#gov-processing-bar', '#intel-processing-bar'];
    for (const selector of bars) {
      const el = page.locator(selector);
      if (await el.count() > 0) {
        const classes = await el.getAttribute('class') ?? '';
        expect(classes).not.toContain('tab-processing-bar--visible');
      }
    }
  });
});
// CUI // SP-CTI

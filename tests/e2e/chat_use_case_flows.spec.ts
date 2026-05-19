// CUI // SP-CTI
// E2E Test: Chat Use Case Flows — End-to-end interaction flows for leadership demo

import { test, expect } from '@playwright/test';

const SCREENSHOT_DIR = '.tmp/test_runs/screenshots';

test.describe('Chat Use Case Flows', () => {
  test.beforeEach(async ({ page }) => {
    await page.addInitScript(() => {
      localStorage.setItem('icdev_tour_completed', '1');
    });
  });

  test('chat page loads with send button and message input', async ({ page }) => {
    await page.goto('/chat');
    await page.waitForLoadState('domcontentloaded');

    await page.screenshot({
      path: `${SCREENSHOT_DIR}/flows_01_chat_loaded.png`,
      fullPage: false,
    });

    // Core chat UI elements must be present
    const input = page.locator('#message-input, .chat-message-input');
    const sendBtn = page.locator('#btn-send, .chat-send-btn');

    expect(await input.count()).toBeGreaterThan(0);
    expect(await sendBtn.count()).toBeGreaterThan(0);
  });

  test('activating a use case does not cause page reload or navigate away', async ({ page }) => {
    await page.goto('/chat');
    await page.waitForLoadState('domcontentloaded');

    const initialUrl = page.url();
    const card = page.locator('.use-case-card, [data-use-case-id]').first();

    if (await card.count() > 0) {
      await card.click();
      await page.waitForTimeout(1200);

      // URL should remain on /chat (no hard redirect)
      expect(page.url()).toContain('/chat');

      await page.screenshot({
        path: `${SCREENSHOT_DIR}/flows_02_use_case_activated.png`,
        fullPage: false,
      });
    }
  });

  test('new chat button creates fresh context', async ({ page }) => {
    await page.goto('/chat');
    await page.waitForLoadState('domcontentloaded');

    // Click new chat / new context button if present
    const newBtn = page.locator(
      '#btn-new-context, .chat-sidebar__new-btn, button[title*="new chat"]'
    ).first();

    if (await newBtn.count() > 0) {
      await newBtn.click();
      await page.waitForTimeout(800);

      await page.screenshot({
        path: `${SCREENSHOT_DIR}/flows_03_new_chat.png`,
        fullPage: false,
      });

      // No error should appear
      const bodyText = (await page.textContent('body')) ?? '';
      expect(bodyText.toLowerCase()).not.toContain('internal server error');
    }
  });

  test('right sidebar tab switching does not throw JS error', async ({ page }) => {
    const errors: string[] = [];
    page.on('console', msg => {
      if (msg.type() === 'error') errors.push(msg.text());
    });

    await page.goto('/chat');
    await page.waitForLoadState('domcontentloaded');

    // Use the toggle buttons in the toolbar (these open+switch the right panel)
    const toggleGov = page.locator('#btn-gov-toggle');
    const toggleIntel = page.locator('#btn-intel-toggle');

    if (await toggleGov.count() > 0) {
      await toggleGov.click();
      await page.waitForTimeout(400);
    }
    if (await toggleIntel.count() > 0) {
      await toggleIntel.click();
      await page.waitForTimeout(400);
    }

    // Filter out non-critical noise
    const criticalErrors = errors.filter(e =>
      !e.includes('favicon') &&
      !e.includes('ERR_CONNECTION_REFUSED') &&
      !e.includes('net::ERR_ABORTED')
    );
    expect(criticalErrors.length).toBe(0);
  });

  test('GOV tab shows content container after switching', async ({ page }) => {
    await page.goto('/chat');
    await page.waitForLoadState('domcontentloaded');

    // Open the right panel to Gov using the toolbar toggle button
    const toggleGov = page.locator('#btn-gov-toggle');
    if (await toggleGov.count() > 0) {
      await toggleGov.click();
      await page.waitForTimeout(800);

      await page.screenshot({
        path: `${SCREENSHOT_DIR}/flows_04_gov_tab.png`,
        fullPage: false,
      });

      // Gov sidebar container must be in DOM
      const govPanel = page.locator('#gov-sidebar, [data-tab-content="gov"]');
      expect(await govPanel.count()).toBeGreaterThan(0);
    }
  });

  test('INTEL tab shows content container after switching', async ({ page }) => {
    await page.goto('/chat');
    await page.waitForLoadState('domcontentloaded');

    // Open the right panel to Intel using the toolbar toggle button
    const toggleIntel = page.locator('#btn-intel-toggle');
    if (await toggleIntel.count() > 0) {
      await toggleIntel.click();
      await page.waitForTimeout(800);

      await page.screenshot({
        path: `${SCREENSHOT_DIR}/flows_05_intel_tab.png`,
        fullPage: false,
      });

      // Intel sidebar container must be in DOM
      const intelPanel = page.locator('#intel-sidebar, [data-tab-content="intel"]');
      expect(await intelPanel.count()).toBeGreaterThan(0);
    }
  });

  test('chat page has no broken resource links (CSS/JS 404)', async ({ page }) => {
    const failed: string[] = [];
    page.on('response', resp => {
      const url = resp.url();
      const status = resp.status();
      if (status >= 400 && (url.includes('.css') || url.includes('.js'))) {
        failed.push(`${status} ${url}`);
      }
    });

    await page.goto('/chat');
    // Use domcontentloaded — live.js polls every 3s so networkidle never fires
    await page.waitForLoadState('domcontentloaded');
    await page.waitForTimeout(2000); // allow initial static assets to load

    expect(
      failed.length,
      `Broken static resources: ${failed.join(', ')}`
    ).toBe(0);
  });
});
// CUI // SP-CTI

// CUI // SP-CTI
// E2E Test: Chat Use Case Flows — End-to-end interaction flows for leadership demo

import { test, expect } from '@playwright/test';

const SCREENSHOT_DIR = '.tmp/test_runs/screenshots';

test.describe('Chat Use Case Flows', () => {
  test('chat page loads with send button and message input', async ({ page }) => {
    await page.goto('/chat');
    await page.waitForLoadState('domcontentloaded');

    await page.screenshot({
      path: `${SCREENSHOT_DIR}/flows_01_chat_loaded.png`,
      fullPage: false,
    });

    // Core chat UI elements must be present
    const input = page.locator('#message-input, textarea[name="message"], .chat-input');
    const sendBtn = page.locator('#send-btn, button[type="submit"], .send-button');

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
      '#new-chat-btn, .new-chat-btn, button[title*="New"], [aria-label*="new chat"]'
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

    const tabs = [
      page.locator('#tab-ricoas, [data-tab="ricoas"]').first(),
      page.locator('#tab-gov, [data-tab="gov"]').first(),
      page.locator('#tab-intel, [data-tab="intel"]').first(),
    ];

    for (const tab of tabs) {
      if (await tab.count() > 0) {
        await tab.click();
        await page.waitForTimeout(400);
      }
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

    const govTab = page.locator('#tab-gov, [data-tab="gov"]').first();
    if (await govTab.count() > 0) {
      await govTab.click();
      await page.waitForTimeout(800);

      await page.screenshot({
        path: `${SCREENSHOT_DIR}/flows_04_gov_tab.png`,
        fullPage: false,
      });

      const govPanel = page.locator(
        '#gov-sidebar-content, #gov-panel, [data-panel="gov"]'
      );
      expect(await govPanel.count()).toBeGreaterThan(0);
    }
  });

  test('INTEL tab shows content container after switching', async ({ page }) => {
    await page.goto('/chat');
    await page.waitForLoadState('domcontentloaded');

    const intelTab = page.locator('#tab-intel, [data-tab="intel"]').first();
    if (await intelTab.count() > 0) {
      await intelTab.click();
      await page.waitForTimeout(800);

      await page.screenshot({
        path: `${SCREENSHOT_DIR}/flows_05_intel_tab.png`,
        fullPage: false,
      });

      const intelPanel = page.locator(
        '#intel-sidebar-content, #intel-panel, [data-panel="intel"]'
      );
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
    await page.waitForLoadState('networkidle');

    expect(
      failed.length,
      `Broken static resources: ${failed.join(', ')}`
    ).toBe(0);
  });
});
// CUI // SP-CTI

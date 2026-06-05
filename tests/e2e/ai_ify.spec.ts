// CUI // SP-CTI
// E2E Test: AI-ify Canvas (formerly "AI Augmentation Canvas" / AAC) — page
// renders, the guided scan wizard is present, the scan API rejects empty input,
// and the legacy /ai-augmentation route redirects to /ai-ify.

import { test, expect } from '@playwright/test';

const BASE  = process.env.ICDEV_DASHBOARD_URL || 'http://localhost:5050';
const AIIFY = `${BASE}/ai-ify/`;

test.describe('AI-ify Canvas', () => {
  test.beforeEach(async ({ page }) => {
    await page.addInitScript(() => {
      localStorage.setItem('icdev_tour_completed', '1');
      localStorage.setItem('icdev_tour_last_step', '999');
    });
  });

  test('HTTP 200 — page loads without server error', async ({ page }) => {
    const resp = await page.request.get(AIIFY);
    expect(resp.status(), `GET /ai-ify/ returned ${resp.status()}`).toBeLessThan(400);

    await page.goto(AIIFY);
    await page.waitForLoadState('domcontentloaded');

    const body = (await page.textContent('body')) ?? '';
    expect(body).not.toContain('Internal Server Error');
    expect(body).not.toContain('Traceback');
    expect(body).not.toContain('werkzeug');

    await page.screenshot({
      path: 'playwright/screenshots/ai_ify_01_page_load.png',
      fullPage: true,
    });
  });

  test('CUI banner is visible', async ({ page }) => {
    await page.goto(AIIFY);
    await page.waitForLoadState('domcontentloaded');
    const body = (await page.textContent('body')) ?? '';
    expect(body).toContain('CUI');
  });

  test('page heading and description are present', async ({ page }) => {
    await page.goto(AIIFY);
    await page.waitForLoadState('domcontentloaded');
    const body = (await page.textContent('body')) ?? '';
    expect(body).toContain('AI-ify');
    expect(body).toContain('Codebase AI Opportunity Assessment');
  });

  test('scan wizard elements are present', async ({ page }) => {
    await page.goto(AIIFY);
    await page.waitForLoadState('domcontentloaded');

    // Wizard steps may be display:none until navigated, so assert presence in
    // the DOM rather than visibility.
    await expect(page.locator('#stepper')).toBeAttached();
    await expect(page.locator('#ilLevel')).toBeAttached();
    await expect(page.locator('#runBtn')).toBeAttached();
  });

  test('IQE widget is attached', async ({ page }) => {
    await page.goto(AIIFY);
    await page.waitForLoadState('domcontentloaded');
    const widget = page.locator('.iqe-widget, #iqe-minibar, [data-api], [id*="iqe"]');
    await expect(widget.first()).toBeAttached();
  });

  test('scan API returns 400 for empty input_ref', async ({ page }) => {
    const resp = await page.request.post(`${BASE}/ai-ify/api/scan`, {
      data: { input_type: 'local_path', input_ref: '', il_level: 'il4' },
    });
    expect(resp.status()).toBe(400);
    const body = await resp.json();
    expect(body.error).toBeTruthy();
  });

  test('legacy /ai-augmentation redirects to /ai-ify', async ({ page }) => {
    const resp = await page.request.get(`${BASE}/ai-augmentation/`, { maxRedirects: 0 });
    expect([301, 302, 307, 308]).toContain(resp.status());
    expect(resp.headers()['location'] ?? '').toContain('/ai-ify');
  });
});
// CUI // SP-CTI

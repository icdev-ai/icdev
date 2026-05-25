// CUI // SP-CTI
// E2E Test: AI Augmentation Canvas — page renders, scan form is present, API rejects empty input

import { test, expect } from '@playwright/test';

const BASE = process.env.ICDEV_DASHBOARD_URL || 'http://localhost:5050';
const AAC  = `${BASE}/ai-augmentation/`;

test.describe('AI Augmentation Canvas (AAC)', () => {
  test.beforeEach(async ({ page }) => {
    await page.addInitScript(() => {
      localStorage.setItem('icdev_tour_completed', '1');
      localStorage.setItem('icdev_tour_last_step', '999');
    });
  });

  test('HTTP 200 — page loads without server error', async ({ page }) => {
    const resp = await page.request.get(AAC);
    expect(resp.status(), `GET /ai-augmentation/ returned ${resp.status()}`).toBeLessThan(400);

    await page.goto(AAC);
    await page.waitForLoadState('domcontentloaded');

    const body = (await page.textContent('body')) ?? '';
    expect(body).not.toContain('Internal Server Error');
    expect(body).not.toContain('Traceback');
    expect(body).not.toContain('werkzeug');

    await page.screenshot({
      path: 'playwright/screenshots/aac_01_page_load.png',
      fullPage: true,
    });
  });

  test('CUI banner is visible', async ({ page }) => {
    await page.goto(AAC);
    await page.waitForLoadState('domcontentloaded');
    const body = (await page.textContent('body')) ?? '';
    expect(body).toContain('CUI');
  });

  test('page heading and description are present', async ({ page }) => {
    await page.goto(AAC);
    await page.waitForLoadState('domcontentloaded');
    const body = (await page.textContent('body')) ?? '';
    expect(body).toContain('AI Augmentation Canvas');
    expect(body).toContain('modernization roadmap');
  });

  test('scan form elements are present', async ({ page }) => {
    await page.goto(AAC);
    await page.waitForLoadState('domcontentloaded');

    await expect(page.locator('#aac-input-type')).toBeVisible();
    await expect(page.locator('#aac-input-ref')).toBeVisible();
    await expect(page.locator('#aac-il-level')).toBeVisible();
    await expect(page.locator('#aac-run-btn')).toBeVisible();
  });

  test('opportunities section is present', async ({ page }) => {
    await page.goto(AAC);
    await page.waitForLoadState('domcontentloaded');
    const body = (await page.textContent('body')) ?? '';
    expect(body).toContain('Opportunities');
  });

  test('roadmap section is present', async ({ page }) => {
    await page.goto(AAC);
    await page.waitForLoadState('domcontentloaded');
    const body = (await page.textContent('body')) ?? '';
    expect(body).toContain('Roadmap');
  });

  test('IQE widget is attached', async ({ page }) => {
    await page.goto(AAC);
    await page.waitForLoadState('domcontentloaded');
    const widget = page.locator('.iqe-widget, #iqe-minibar, [data-api]');
    await expect(widget.first()).toBeVisible();
  });

  test('scan API returns 400 for empty input_ref', async ({ page }) => {
    const resp = await page.request.post(`${BASE}/ai-augmentation/api/scan`, {
      data: { input_type: 'local_path', input_ref: '', il_level: 'il4' },
    });
    expect(resp.status()).toBe(400);
    const body = await resp.json();
    expect(body.error).toBeTruthy();
  });

  test('scan form shows error when run with empty path', async ({ page }) => {
    await page.goto(AAC);
    await page.waitForLoadState('domcontentloaded');

    // Leave input_ref blank and click Run Scan
    await page.locator('#aac-run-btn').click();

    const statusEl = page.locator('#aac-scan-status');
    await expect(statusEl).toBeVisible();
    const statusText = (await statusEl.textContent()) ?? '';
    expect(statusText.toLowerCase()).toContain('required');
  });
});
// CUI // SP-CTI

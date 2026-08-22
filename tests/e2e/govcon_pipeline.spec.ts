// CUI // SP-CTI
// E2E: gcpl-disc-* — DISCOVER Epic
// GovCon Pipeline Hub: SAM.gov scan API + /govcon page + stat cards + responsive screenshots

import { test, expect } from './fixtures/auth';
import { BASE, SS, CUI } from './fixtures/govcon_cpmp';

test.describe('gcpl-disc: GovCon Pipeline Hub — DISCOVER', () => {
  test.beforeEach(async ({ page }) => {
    await page.addInitScript(() => {
      localStorage.setItem('icdev_tour_completed', '1');
      localStorage.setItem('icdev_tour_last_step', '999');
    });
  });

  // ── API smoke tests ────────────────────────────────────────────────────

  test('gcpl-disc-01: GET /api/govcon/sam/opportunities returns HTTP < 400', async ({ request }) => {
    const resp = await request.get(`${BASE}/api/govcon/sam/opportunities`);
    expect(resp.status(), `Expected < 400, got ${resp.status()}`).toBeLessThan(400);
  });

  test('gcpl-disc-02: GET /api/govcon/pipeline/status returns daemon state JSON', async ({ request }) => {
    const resp = await request.get(`${BASE}/api/govcon/pipeline/status`);
    expect(resp.status()).toBeLessThan(400);
    const body = await resp.json().catch(() => null);
    expect(body, 'Response should be JSON').not.toBeNull();
  });

  test('gcpl-disc-09: POST /api/govcon/sam/scan triggers scan (200 or 202)', async ({ request }) => {
    // The scan runs SYNCHRONOUSLY against SAM.gov (one call per NAICS x notice
    // type; measured 26s on a host whose key SAM rejects), which is longer than
    // the 10s `actionTimeout` every test-runner `request` context inherits. It
    // was never visible while the POST 403'd instantly on CSRF.
    const resp = await request.post(`${BASE}/api/govcon/sam/scan`, { timeout: 45_000 });
    // 200 = ran immediately, 202 = accepted (async), 503 = no API key (graceful)
    expect([200, 202, 503]).toContain(resp.status());
  });

  // ── Page load tests ────────────────────────────────────────────────────

  test('gcpl-disc-03: /govcon HTTP < 400, no server error', async ({ page }) => {
    const resp = await page.request.get(`${BASE}/govcon`);
    expect(resp.status()).toBeLessThan(400);
  });

  test('gcpl-disc-04: /govcon — CUI banner "CUI // SP-CTI" present', async ({ page }) => {
    await page.goto(`${BASE}/govcon`);
    await page.waitForLoadState('domcontentloaded');
    const body = await page.textContent('body');
    expect(body).not.toContain('Internal Server Error');
    expect(body).not.toContain('Traceback');
    expect(body).toContain(CUI);
  });

  test('gcpl-disc-05: /govcon — SAM.gov scan status card visible', async ({ page }) => {
    await page.goto(`${BASE}/govcon`);
    await page.waitForLoadState('domcontentloaded');
    const body = await page.textContent('body') ?? '';
    // Pipeline hub should show scan-related content
    const hasScanContent = body.includes('SAM') || body.includes('scan') || body.includes('Scan') ||
                           body.includes('opportunity') || body.includes('Opportunity') ||
                           body.includes('pipeline') || body.includes('Pipeline');
    expect(hasScanContent, 'Expected SAM.gov or pipeline content on /govcon').toBe(true);
  });

  test('gcpl-disc-06: /govcon — opportunity count stat card visible', async ({ page }) => {
    await page.goto(`${BASE}/govcon`);
    await page.waitForLoadState('domcontentloaded');
    const body = await page.textContent('body') ?? '';
    const hasCount = body.includes('Opportunit') || body.includes('opportunit') ||
                     body.includes('Active') || body.includes('Total');
    expect(hasCount, 'Expected opportunity count content').toBe(true);
  });

  test('gcpl-disc-07: /govcon — domain distribution section visible', async ({ page }) => {
    await page.goto(`${BASE}/govcon`);
    await page.waitForLoadState('domcontentloaded');
    const body = await page.textContent('body') ?? '';
    const hasDomain = body.includes('domain') || body.includes('Domain') ||
                      body.includes('NAICS') || body.includes('distribution') ||
                      body.includes('devsecops') || body.includes('compliance');
    expect(hasDomain, 'Expected domain distribution content').toBe(true);
  });

  test('gcpl-disc-08: /govcon — pipeline controls or action buttons exist', async ({ page }) => {
    await page.goto(`${BASE}/govcon`);
    await page.waitForLoadState('domcontentloaded');
    // At least one action button should be present (Run, Scan, Pipeline, etc.)
    const buttons = await page.locator('button, a.btn, input[type="button"]').count();
    expect(buttons, 'Expected at least one action button on /govcon').toBeGreaterThan(0);
  });

  // ── Responsive screenshots ─────────────────────────────────────────────

  test('gcpl-disc-11: /govcon responsive — 1920x1080 screenshot', async ({ page }) => {
    await page.setViewportSize({ width: 1920, height: 1080 });
    await page.goto(`${BASE}/govcon`);
    await page.waitForLoadState('domcontentloaded');
    await page.screenshot({
      path: `${SS}/gcpl_disc_govcon_1920x1080.png`,
      fullPage: true,
    });
    const body = await page.textContent('body');
    expect(body).toContain(CUI);
  });

  test('gcpl-disc-12: /govcon responsive — 768x1024 screenshot', async ({ page }) => {
    await page.setViewportSize({ width: 768, height: 1024 });
    await page.goto(`${BASE}/govcon`);
    await page.waitForLoadState('domcontentloaded');
    await page.screenshot({
      path: `${SS}/gcpl_disc_govcon_768x1024.png`,
      fullPage: true,
    });
    const body = await page.textContent('body');
    expect(body).toContain(CUI);
  });

  test('gcpl-disc-13: /govcon responsive — 375x812 screenshot', async ({ page }) => {
    await page.setViewportSize({ width: 375, height: 812 });
    await page.goto(`${BASE}/govcon`);
    await page.waitForLoadState('domcontentloaded');
    await page.screenshot({
      path: `${SS}/gcpl_disc_govcon_375x812.png`,
      fullPage: true,
    });
    const body = await page.textContent('body');
    expect(body).toContain(CUI);
  });
});
// CUI // SP-CTI

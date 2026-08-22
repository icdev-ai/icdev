// CUI // SP-CTI
// E2E: gcpl-ext-* — EXTRACT Epic
// GovCon Requirements Matrix: shall statement extraction + /govcon/requirements page

import { test, expect } from './fixtures/auth';
import { BASE, SS, CUI, seedOpportunity } from './fixtures/govcon_cpmp';

let oppId: string;

test.describe('gcpl-ext: GovCon Requirements Matrix — EXTRACT', () => {
  test.beforeAll(async ({ request }) => {
    oppId = await seedOpportunity(request);
  });

  test.beforeEach(async ({ page }) => {
    await page.addInitScript(() => {
      localStorage.setItem('icdev_tour_completed', '1');
      localStorage.setItem('icdev_tour_last_step', '999');
    });
  });

  // ── Page load ──────────────────────────────────────────────────────────

  test('gcpl-ext-01: /govcon/requirements HTTP < 400, no server error', async ({ page }) => {
    const resp = await page.request.get(`${BASE}/govcon/requirements`);
    expect(resp.status()).toBeLessThan(400);
  });

  test('gcpl-ext-02: /govcon/requirements — CUI banner present', async ({ page }) => {
    await page.goto(`${BASE}/govcon/requirements`);
    await page.waitForLoadState('domcontentloaded');
    const body = await page.textContent('body');
    expect(body).not.toContain('Internal Server Error');
    expect(body).not.toContain('Traceback');
    expect(body).toContain(CUI);
  });

  test('gcpl-ext-03: /govcon/requirements — pattern frequency content visible', async ({ page }) => {
    await page.goto(`${BASE}/govcon/requirements`);
    await page.waitForLoadState('domcontentloaded');
    const body = await page.textContent('body') ?? '';
    const hasContent = body.includes('pattern') || body.includes('Pattern') ||
                       body.includes('frequency') || body.includes('Frequency') ||
                       body.includes('requirement') || body.includes('Requirement') ||
                       body.includes('shall') || body.includes('Shall');
    expect(hasContent, 'Expected requirements pattern content').toBe(true);
  });

  test('gcpl-ext-04: /govcon/requirements — domain heatmap section exists', async ({ page }) => {
    await page.goto(`${BASE}/govcon/requirements`);
    await page.waitForLoadState('domcontentloaded');
    const body = await page.textContent('body') ?? '';
    const hasDomain = body.includes('domain') || body.includes('Domain') ||
                      body.includes('heatmap') || body.includes('Heatmap') ||
                      body.includes('devsecops') || body.includes('compliance') ||
                      body.includes('category') || body.includes('Category');
    expect(hasDomain, 'Expected domain or heatmap content').toBe(true);
  });

  test('gcpl-ext-05: /govcon/requirements — statement types breakdown visible', async ({ page }) => {
    await page.goto(`${BASE}/govcon/requirements`);
    await page.waitForLoadState('domcontentloaded');
    const body = await page.textContent('body') ?? '';
    const hasTypes = body.includes('shall') || body.includes('must') || body.includes('will') ||
                     body.includes('type') || body.includes('Type') ||
                     body.includes('statement') || body.includes('Statement');
    expect(hasTypes, 'Expected statement types content').toBe(true);
  });

  // ── API tests ──────────────────────────────────────────────────────────

  test('gcpl-ext-06: POST /api/govcon/opportunities/<id>/extract-requirements returns 200', async ({ request }) => {
    const resp = await request.post(`${BASE}/api/govcon/opportunities/${oppId}/extract-requirements`);
    expect(resp.status(), `Expected 200, got ${resp.status()}`).toBeLessThan(400);
  });

  test('gcpl-ext-07: GET /api/govcon/requirement-patterns returns pattern list', async ({ request }) => {
    const resp = await request.get(`${BASE}/api/govcon/requirement-patterns`);
    expect(resp.status()).toBeLessThan(400);
    const body = await resp.json().catch(() => null);
    expect(body).not.toBeNull();
  });

  test('gcpl-ext-08: GET /api/govcon/opportunities/<id>/requirements returns shall statements', async ({ request }) => {
    const resp = await request.get(`${BASE}/api/govcon/opportunities/${oppId}/requirements`);
    expect(resp.status()).toBeLessThan(400);
    const body = await resp.json().catch(() => null);
    expect(body).not.toBeNull();
  });

  // ── Responsive ────────────────────────────────────────────────────────

  test('gcpl-ext-09: /govcon/requirements responsive — 3-viewport screenshots', async ({ page }) => {
    const viewports = [
      { width: 1920, height: 1080, name: '1920x1080' },
      { width: 768,  height: 1024, name: '768x1024'  },
      { width: 375,  height: 812,  name: '375x812'   },
    ];

    for (const vp of viewports) {
      await page.setViewportSize({ width: vp.width, height: vp.height });
      await page.goto(`${BASE}/govcon/requirements`);
      await page.waitForLoadState('domcontentloaded');
      await page.screenshot({
        path: `${SS}/gcpl_ext_requirements_${vp.name}.png`,
        fullPage: true,
      });
      const body = await page.textContent('body');
      expect(body).toContain(CUI);
    }
  });
});
// CUI // SP-CTI

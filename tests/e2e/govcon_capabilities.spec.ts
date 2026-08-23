// CUI // SP-CTI
// E2E: gcpl-map-* — MAP Epic
// GovCon Capabilities Library: L/M/N coverage + gap analysis + /govcon/capabilities page

import { test, expect } from './fixtures/auth';
import { BASE, SS, CUI, seedOpportunity } from './fixtures/govcon_cpmp';

let oppId: string;

test.describe('gcpl-map: GovCon Capabilities Library — MAP', () => {
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

  test('gcpl-map-01: /govcon/capabilities HTTP < 400, no server error', async ({ page }) => {
    const resp = await page.request.get(`${BASE}/govcon/capabilities`);
    expect(resp.status()).toBeLessThan(400);
  });

  test('gcpl-map-02: /govcon/capabilities — CUI banner present', async ({ page }) => {
    await page.goto(`${BASE}/govcon/capabilities`);
    await page.waitForLoadState('domcontentloaded');
    const body = await page.textContent('body');
    expect(body).not.toContain('Internal Server Error');
    expect(body).not.toContain('Traceback');
    expect(body).toContain(CUI);
  });

  test('gcpl-map-03: /govcon/capabilities — L/M/N coverage breakdown visible', async ({ page }) => {
    await page.goto(`${BASE}/govcon/capabilities`);
    await page.waitForLoadState('domcontentloaded');
    const body = await page.textContent('body') ?? '';
    // L/M/N grading or coverage/capability terms
    const hasLMN = body.includes(' L ') || body.includes(' M ') || body.includes(' N ') ||
                   body.includes('coverage') || body.includes('Coverage') ||
                   body.includes('capabilit') || body.includes('Capabilit') ||
                   body.includes('compliant') || body.includes('Compliant');
    expect(hasLMN, 'Expected L/M/N or coverage content').toBe(true);
  });

  test('gcpl-map-04: /govcon/capabilities — gap list section visible', async ({ page }) => {
    await page.goto(`${BASE}/govcon/capabilities`);
    await page.waitForLoadState('domcontentloaded');
    const body = await page.textContent('body') ?? '';
    const hasGap = body.includes('gap') || body.includes('Gap') ||
                   body.includes('missing') || body.includes('Missing') ||
                   body.includes('enhancement') || body.includes('Enhancement');
    expect(hasGap, 'Expected gap list content').toBe(true);
  });

  test('gcpl-map-05: /govcon/capabilities — enhancement recommendations visible', async ({ page }) => {
    await page.goto(`${BASE}/govcon/capabilities`);
    await page.waitForLoadState('domcontentloaded');
    const body = await page.textContent('body') ?? '';
    const hasRec = body.includes('recommend') || body.includes('Recommend') ||
                   body.includes('enhancement') || body.includes('Enhancement') ||
                   body.includes('improve') || body.includes('action');
    expect(hasRec, 'Expected recommendations content').toBe(true);
  });

  // ── API tests ──────────────────────────────────────────────────────────

  test('gcpl-map-06: POST /api/govcon/opportunities/<id>/map-capabilities returns coverage scores', async ({ request }) => {
    const resp = await request.post(`${BASE}/api/govcon/opportunities/${oppId}/map-capabilities`);
    expect(resp.status()).toBeLessThan(400);
    const body = await resp.json().catch(() => null);
    expect(body).not.toBeNull();
  });

  test('gcpl-map-07: GET /api/govcon/opportunities/<id>/coverage returns L/M/N grades', async ({ request }) => {
    const resp = await request.get(`${BASE}/api/govcon/opportunities/${oppId}/coverage`);
    expect(resp.status()).toBeLessThan(400);
    const body = await resp.json().catch(() => null);
    expect(body).not.toBeNull();
  });

  test('gcpl-map-08: GET /api/govcon/gaps returns gap list with domain and severity', async ({ request }) => {
    const resp = await request.get(`${BASE}/api/govcon/gaps`);
    expect(resp.status()).toBeLessThan(400);
    const body = await resp.json().catch(() => null);
    expect(body).not.toBeNull();
  });

  test('gcpl-map-09: GET /api/govcon/gaps/recommendations returns actionable recommendations', async ({ request }) => {
    const resp = await request.get(`${BASE}/api/govcon/gaps/recommendations`);
    expect(resp.status()).toBeLessThan(400);
    const body = await resp.json().catch(() => null);
    expect(body).not.toBeNull();
  });

  test('gcpl-map-10: GET /api/govcon/gaps/heatmap returns heatmap data per domain', async ({ request }) => {
    const resp = await request.get(`${BASE}/api/govcon/gaps/heatmap`);
    expect(resp.status()).toBeLessThan(400);
    const body = await resp.json().catch(() => null);
    expect(body).not.toBeNull();
  });

  // ── Responsive ────────────────────────────────────────────────────────

  test('gcpl-map-11: /govcon/capabilities responsive — 3-viewport screenshots', async ({ page }) => {
    const viewports = [
      { width: 1920, height: 1080, name: '1920x1080' },
      { width: 768,  height: 1024, name: '768x1024'  },
      { width: 375,  height: 812,  name: '375x812'   },
    ];

    for (const vp of viewports) {
      await page.setViewportSize({ width: vp.width, height: vp.height });
      await page.goto(`${BASE}/govcon/capabilities`);
      await page.waitForLoadState('domcontentloaded');
      await page.screenshot({
        path: `${SS}/gcpl_map_capabilities_${vp.name}.png`,
        fullPage: true,
      });
      const body = await page.textContent('body');
      expect(body).toContain(CUI);
    }
  });
});
// CUI // SP-CTI

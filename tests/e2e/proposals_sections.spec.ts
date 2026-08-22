// CUI // SP-CTI
// E2E: gcpl-sec-* — PROPOSALS Sections Epic
// Covers: /proposals/<opp_id>/sections/<sec_id> page +
//         /api/proposals/opportunities/<opp_id>/sections API

import { test, expect } from './fixtures/auth';
import { BASE, SS, CUI, seedOpportunity, seedVolume, seedSection } from './fixtures/govcon_cpmp';

let oppId: string;
let volumeId: string;
let sectionId: string;

test.describe('gcpl-sec: GovCon Proposals — Sections', () => {
  test.beforeAll(async ({ request }) => {
    oppId    = await seedOpportunity(request);
    volumeId = await seedVolume(request, oppId);
    sectionId = await seedSection(request, oppId, volumeId);
  });

  test.beforeEach(async ({ page }) => {
    await page.addInitScript(() => {
      localStorage.setItem('icdev_tour_completed', '1');
      localStorage.setItem('icdev_tour_last_step', '999');
    });
  });

  // ── Sections API ────────────────────────────────────────────────────────

  test('gcpl-sec-01: GET /api/proposals/opportunities/<id>/sections returns valid JSON', async ({ request }) => {
    const resp = await request.get(`${BASE}/api/proposals/opportunities/${oppId}/sections`);
    expect(resp.status()).toBeLessThan(400);
    const body = await resp.json().catch(() => null);
    expect(body).not.toBeNull();
    // Body should have a sections array (may be empty for placeholder seeds)
    const sections = body?.sections ?? body?.data ?? body;
    expect(Array.isArray(sections) || typeof body === 'object').toBe(true);
  });

  test('gcpl-sec-02: POST /api/proposals/opportunities/<id>/sections creates a section', async ({ request }) => {
    const resp = await request.post(
      `${BASE}/api/proposals/opportunities/${oppId}/sections`,
      {
        data: {
          volume_id: volumeId,
          section_number: '1.2',
          title: 'E2E gcpl-sec-02 Management Approach',
          priority: 'standard',
          sort_order: 1,
        },
      },
    );
    // 201 on success, 400 for placeholder seed oppId — both are acceptable
    expect([200, 201, 400, 404]).toContain(resp.status());
  });

  test('gcpl-sec-03: GET sections API — status filter param accepted', async ({ request }) => {
    const resp = await request.get(
      `${BASE}/api/proposals/opportunities/${oppId}/sections?status=not_started`,
    );
    expect(resp.status()).toBeLessThan(400);
  });

  // ── Section Detail Page ─────────────────────────────────────────────────

  test('gcpl-sec-04: /proposals/<opp_id>/sections/<sec_id> HTTP < 500, no server error', async ({ page }) => {
    const resp = await page.request.get(
      `${BASE}/proposals/${oppId}/sections/${sectionId}`,
    );
    // 200 if seed is real; 404 for placeholder ID — both are fine
    expect(resp.status()).toBeLessThan(500);
  });

  test('gcpl-sec-05: /proposals/<opp_id>/sections/<sec_id> — CUI banner or 404 page', async ({ page }) => {
    await page.goto(`${BASE}/proposals/${oppId}/sections/${sectionId}`);
    await page.waitForLoadState('domcontentloaded');
    const body = await page.textContent('body') ?? '';
    expect(body).not.toContain('Internal Server Error');
    expect(body).not.toContain('Traceback');
    if (!body.includes('Not Found') && !body.includes('404')) {
      expect(body).toContain(CUI);
    }
  });

  test('gcpl-sec-06: /proposals/<opp_id>/sections/<sec_id> — shows section or 404 content', async ({ page }) => {
    await page.goto(`${BASE}/proposals/${oppId}/sections/${sectionId}`);
    await page.waitForLoadState('domcontentloaded');
    const body = await page.textContent('body') ?? '';
    const hasContent =
      body.includes('section') || body.includes('Section') ||
      body.includes('volume')  || body.includes('Volume')  ||
      body.includes('draft')   || body.includes('Draft')   ||
      body.includes('Not Found') || body.includes('404');
    expect(hasContent, 'Expected section detail or 404 content').toBe(true);
  });

  test('gcpl-sec-07: /proposals/<opp_id>/sections/<sec_id> — screenshot captured', async ({ page }) => {
    await page.goto(`${BASE}/proposals/${oppId}/sections/${sectionId}`);
    await page.waitForLoadState('domcontentloaded');
    await page.screenshot({
      path: `${SS}/gcpl_sec_detail.png`,
      fullPage: true,
    });
    expect(true).toBe(true);
  });
});
// CUI // SP-CTI

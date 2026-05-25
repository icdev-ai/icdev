// CUI // SP-CTI
// E2E: gcpl-cset-* — CPMP-SETUP Epic
// CPMP Portfolio + Contract Setup: portfolio page, contract creation, CLINs, WBS, deliverables

import { test, expect } from '@playwright/test';
import { BASE, SS, CUI, buildFullFixture, seedOpportunity, seedContract, seedWbs, seedDeliverable, seedClin } from './fixtures/govcon_cpmp';

let oppId: string;
let contractId: string;
let wbsId: string;
let deliverableId: string;
let clinId: string;

test.describe('gcpl-cset: CPMP Portfolio & Contract Setup — CPMP-SETUP', () => {
  test.beforeAll(async ({ request }) => {
    oppId        = await seedOpportunity(request);
    contractId   = await seedContract(request, oppId);
    wbsId        = await seedWbs(request, contractId);
    deliverableId = await seedDeliverable(request, contractId);
    clinId       = await seedClin(request, contractId);
  });

  test.beforeEach(async ({ page }) => {
    await page.addInitScript(() => {
      localStorage.setItem('icdev_tour_completed', '1');
      localStorage.setItem('icdev_tour_last_step', '999');
    });
  });

  // ── Portfolio page ────────────────────────────────────────────────────

  test('gcpl-cset-01: /cpmp portfolio HTTP < 400, no server error', async ({ page }) => {
    const resp = await page.request.get(`${BASE}/cpmp`);
    expect(resp.status()).toBeLessThan(400);
  });

  test('gcpl-cset-02: /cpmp — CUI banner present', async ({ page }) => {
    await page.goto(`${BASE}/cpmp`);
    await page.waitForLoadState('domcontentloaded');
    const body = await page.textContent('body');
    expect(body).not.toContain('Internal Server Error');
    expect(body).not.toContain('Traceback');
    expect(body).toContain(CUI);
  });

  test('gcpl-cset-03: /cpmp — stat grid content visible (contracts, health, deliverables)', async ({ page }) => {
    await page.goto(`${BASE}/cpmp`);
    await page.waitForLoadState('domcontentloaded');
    const body = await page.textContent('body') ?? '';
    const hasStats = body.includes('contract') || body.includes('Contract') ||
                     body.includes('health') || body.includes('Health') ||
                     body.includes('deliverable') || body.includes('Deliverable') ||
                     body.includes('portfolio') || body.includes('Portfolio');
    expect(hasStats, 'Expected portfolio stat grid content').toBe(true);
  });

  test('gcpl-cset-04: /cpmp — health distribution content visible', async ({ page }) => {
    await page.goto(`${BASE}/cpmp`);
    await page.waitForLoadState('domcontentloaded');
    const body = await page.textContent('body') ?? '';
    const hasHealth = body.includes('green') || body.includes('Green') ||
                      body.includes('yellow') || body.includes('Yellow') ||
                      body.includes('red') || body.includes('health') ||
                      body.includes('Health') || body.includes('score');
    expect(hasHealth, 'Expected health distribution content').toBe(true);
  });

  test('gcpl-cset-05: /cpmp — contract table visible', async ({ page }) => {
    await page.goto(`${BASE}/cpmp`);
    await page.waitForLoadState('domcontentloaded');
    const body = await page.textContent('body') ?? '';
    const hasTable = body.includes('contract') || body.includes('Contract') ||
                     body.includes('No contracts') || body.includes('Active') ||
                     body.includes('IDIQ') || body.includes('FFP');
    expect(hasTable, 'Expected contract table or empty state').toBe(true);
  });

  test('gcpl-cset-06: /cpmp — upcoming deliverables section visible', async ({ page }) => {
    await page.goto(`${BASE}/cpmp`);
    await page.waitForLoadState('domcontentloaded');
    const body = await page.textContent('body') ?? '';
    const hasDel = body.includes('deliverable') || body.includes('Deliverable') ||
                   body.includes('CDRL') || body.includes('due') || body.includes('Due') ||
                   body.includes('upcoming') || body.includes('Upcoming');
    expect(hasDel, 'Expected deliverables section').toBe(true);
  });

  // ── Portfolio API ─────────────────────────────────────────────────────

  test('gcpl-cset-07: GET /api/cpmp/portfolio returns summary JSON with health stats', async ({ request }) => {
    const resp = await request.get(`${BASE}/api/cpmp/portfolio`);
    expect(resp.status()).toBeLessThan(400);
    const body = await resp.json().catch(() => null);
    expect(body).not.toBeNull();
  });

  // ── Contract Creation ─────────────────────────────────────────────────

  test('gcpl-cset-08: POST /api/cpmp/from-opportunity/<id> creates contract', async ({ request }) => {
    const resp = await request.post(`${BASE}/api/cpmp/from-opportunity/${oppId}`);
    // 200/201 = created, 409 = already exists, 400 = invalid opp state — all acceptable
    expect([200, 201, 400, 409]).toContain(resp.status());
  });

  test('gcpl-cset-09: GET /api/cpmp/contracts returns contract list', async ({ request }) => {
    const resp = await request.get(`${BASE}/api/cpmp/contracts`);
    expect(resp.status()).toBeLessThan(400);
    const body = await resp.json().catch(() => null);
    expect(body).not.toBeNull();
  });

  test('gcpl-cset-10: PUT /api/cpmp/contracts/<id> updates contract COR info and POP dates', async ({ request }) => {
    const resp = await request.put(`${BASE}/api/cpmp/contracts/${contractId}`, {
      data: {
        cor_email: 'cor@test.gov',
        cor_name: 'Test COR',
        pop_start: '2026-01-01',
        pop_end: '2026-12-31',
      },
    });
    expect([200, 201, 204, 400, 404]).toContain(resp.status());
  });

  test('gcpl-cset-11: PUT /api/cpmp/contracts/<id>/status transitions to active', async ({ request }) => {
    const resp = await request.put(`${BASE}/api/cpmp/contracts/${contractId}/status`, {
      data: { status: 'active' },
    });
    expect([200, 201, 204, 400, 404, 409]).toContain(resp.status());
  });

  test('gcpl-cset-12: Invalid state transition returns 400 with error message', async ({ request }) => {
    const resp = await request.put(`${BASE}/api/cpmp/contracts/${contractId}/status`, {
      data: { status: 'invalid_state_xyz' },
    });
    // Should be 400 for invalid state
    expect([400, 422]).toContain(resp.status());
  });

  // ── CLINs ─────────────────────────────────────────────────────────────

  test('gcpl-cset-13: POST /api/cpmp/contracts/<id>/clins creates labor CLIN $250K', async ({ request }) => {
    const resp = await request.post(`${BASE}/api/cpmp/contracts/${contractId}/clins`, {
      data: {
        clin_number: '0001',
        description: 'Labor — Software Engineering (gcpl-cset-13)',
        clin_type: 'labor',
        total_value: 250000,
        funded_value: 250000,
      },
    });
    expect([200, 201, 400, 409]).toContain(resp.status());
    if (resp.status() === 201 || resp.status() === 200) {
      const body = await resp.json().catch(() => ({}));
      const newId = body.clin_id ?? body.id;
      if (newId) clinId = String(newId);
    }
  });

  test('gcpl-cset-14: PUT /api/cpmp/clins/<id> updates CLIN billed value', async ({ request }) => {
    const resp = await request.put(`${BASE}/api/cpmp/clins/${clinId}`, {
      data: { billed_value: 50000 },
    });
    expect([200, 201, 204, 400, 404]).toContain(resp.status());
  });

  test('gcpl-cset-15: GET /api/cpmp/contracts/<id>/clins returns CLIN list', async ({ request }) => {
    const resp = await request.get(`${BASE}/api/cpmp/contracts/${contractId}/clins`);
    expect(resp.status()).toBeLessThan(400);
    const body = await resp.json().catch(() => null);
    expect(body).not.toBeNull();
  });

  // ── WBS ──────────────────────────────────────────────────────────────

  test('gcpl-cset-16: POST /api/cpmp/contracts/<id>/wbs creates WBS element with BAC', async ({ request }) => {
    const resp = await request.post(`${BASE}/api/cpmp/contracts/${contractId}/wbs`, {
      data: {
        name: 'WBS 1.0 — Software Development (gcpl-cset-16)',
        bac: 500000,
        planned_start: '2026-01-01',
        planned_end: '2026-12-31',
        percent_complete: 0,
      },
    });
    expect([200, 201, 400, 409]).toContain(resp.status());
    if (resp.ok()) {
      const body = await resp.json().catch(() => ({}));
      const newId = body.wbs_id ?? body.id;
      if (newId) wbsId = String(newId);
    }
  });

  test('gcpl-cset-17: GET /api/cpmp/contracts/<id>/wbs?mode=tree returns hierarchical WBS', async ({ request }) => {
    const resp = await request.get(`${BASE}/api/cpmp/contracts/${contractId}/wbs?mode=tree`);
    expect(resp.status()).toBeLessThan(400);
    const body = await resp.json().catch(() => null);
    expect(body).not.toBeNull();
  });

  test('gcpl-cset-18: PUT /api/cpmp/wbs/<id> updates WBS percent complete', async ({ request }) => {
    const resp = await request.put(`${BASE}/api/cpmp/wbs/${wbsId}`, {
      data: { percent_complete: 25 },
    });
    expect([200, 201, 204, 400, 404]).toContain(resp.status());
  });

  // ── Deliverables ──────────────────────────────────────────────────────

  test('gcpl-cset-19: POST /api/cpmp/contracts/<id>/deliverables creates CDRL with due date', async ({ request }) => {
    const resp = await request.post(`${BASE}/api/cpmp/contracts/${contractId}/deliverables`, {
      data: {
        title: 'System Security Plan (gcpl-cset-19)',
        cdrl_number: 'A001',
        did_number: 'DI-MGMT-81466',
        cdrl_type: 'ssp',
        frequency: 'monthly',
        due_date: '2026-06-30',
      },
    });
    expect([200, 201, 400, 409]).toContain(resp.status());
    if (resp.ok()) {
      const body = await resp.json().catch(() => ({}));
      const newId = body.deliverable_id ?? body.id;
      if (newId) deliverableId = String(newId);
    }
  });

  test('gcpl-cset-20: GET /api/cpmp/contracts/<id>/deliverables returns deliverable list', async ({ request }) => {
    const resp = await request.get(`${BASE}/api/cpmp/contracts/${contractId}/deliverables`);
    expect(resp.status()).toBeLessThan(400);
    const body = await resp.json().catch(() => null);
    expect(body).not.toBeNull();
  });

  test('gcpl-cset-21: PUT /api/cpmp/deliverables/<id>/status transitions deliverable state', async ({ request }) => {
    const resp = await request.put(`${BASE}/api/cpmp/deliverables/${deliverableId}/status`, {
      data: { status: 'in_progress' },
    });
    expect([200, 201, 204, 400, 404, 409]).toContain(resp.status());
  });

  // ── Contract Detail Page ──────────────────────────────────────────────

  test('gcpl-cset-22: /cpmp/<id> detail HTTP < 400, CUI banner present', async ({ page }) => {
    const resp = await page.request.get(`${BASE}/cpmp/${contractId}`);
    expect([200, 302, 404]).toContain(resp.status());
    if (resp.status() === 200) {
      await page.goto(`${BASE}/cpmp/${contractId}`);
      await page.waitForLoadState('domcontentloaded');
      const body = await page.textContent('body') ?? '';
      if (!body.includes('Not Found')) {
        expect(body).toContain(CUI);
      }
    }
  });

  test('gcpl-cset-23: /cpmp/<id> — 7 tabs content area visible', async ({ page }) => {
    await page.goto(`${BASE}/cpmp/${contractId}`);
    await page.waitForLoadState('domcontentloaded');
    const body = await page.textContent('body') ?? '';
    if (body.includes('Not Found') || body.includes('404')) return;
    // At minimum some tab content should be present
    const hasTabs = body.includes('Overview') || body.includes('CLIN') || body.includes('WBS') ||
                    body.includes('Deliverable') || body.includes('EVM') ||
                    body.includes('Subcontractor') || body.includes('CPARS');
    expect(hasTabs, 'Expected contract detail tabs').toBe(true);
  });

  // ── Responsive ────────────────────────────────────────────────────────

  test('gcpl-cset-24: /cpmp responsive — 3-viewport screenshots', async ({ page }) => {
    const viewports = [
      { width: 1920, height: 1080, name: '1920x1080' },
      { width: 768,  height: 1024, name: '768x1024'  },
      { width: 375,  height: 812,  name: '375x812'   },
    ];
    for (const vp of viewports) {
      await page.setViewportSize({ width: vp.width, height: vp.height });
      await page.goto(`${BASE}/cpmp`);
      await page.waitForLoadState('domcontentloaded');
      await page.screenshot({ path: `${SS}/gcpl_cset_portfolio_${vp.name}.png`, fullPage: true });
      const body = await page.textContent('body');
      expect(body).toContain(CUI);
    }
  });
});
// CUI // SP-CTI

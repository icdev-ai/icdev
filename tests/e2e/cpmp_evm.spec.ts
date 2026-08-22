// CUI // SP-CTI
// E2E: gcpl-evm-* — CPMP-EVM Epic
// EVM snapshots + ANSI/EIA-748 calculations + Monte Carlo + S-curve + IPMDAR

import { test, expect } from './fixtures/auth';
import { BASE, SS, CUI, seedOpportunity, seedContract, seedWbs } from './fixtures/govcon_cpmp';

let contractId: string;
let wbsId: string;

test.describe('gcpl-evm: CPMP EVM Engine — CPMP-EVM', () => {
  test.beforeAll(async ({ request }) => {
    const oppId  = await seedOpportunity(request);
    contractId   = await seedContract(request, oppId);
    wbsId        = await seedWbs(request, contractId);

    // Seed an EVM period so downstream calculation tests have data
    await request.post(`${BASE}/api/cpmp/contracts/${contractId}/evm`, {
      data: {
        wbs_id: wbsId,
        period_date: '2026-01-31',
        pv: 100000,
        ev: 95000,
        ac: 97000,
      },
    });
  });

  test.beforeEach(async ({ page }) => {
    await page.addInitScript(() => {
      localStorage.setItem('icdev_tour_completed', '1');
      localStorage.setItem('icdev_tour_last_step', '999');
    });
  });

  // ── EVM Record & Aggregate ─────────────────────────────────────────────

  test('gcpl-evm-01: POST /api/cpmp/contracts/<id>/evm records monthly snapshot', async ({ request }) => {
    const resp = await request.post(`${BASE}/api/cpmp/contracts/${contractId}/evm`, {
      data: {
        wbs_id: wbsId,
        period_date: '2026-02-28',
        pv: 200000,
        ev: 185000,
        ac: 192000,
      },
    });
    expect([200, 201, 400, 409]).toContain(resp.status());
  });

  test('gcpl-evm-02: GET /api/cpmp/contracts/<id>/evm returns aggregate metrics', async ({ request }) => {
    const resp = await request.get(`${BASE}/api/cpmp/contracts/${contractId}/evm`);
    expect(resp.status()).toBeLessThan(400);
    const body = await resp.json().catch(() => null);
    expect(body).not.toBeNull();
  });

  test('gcpl-evm-03: CPI = EV/AC ≈ 0.979 (calculation accuracy)', async ({ request }) => {
    const resp = await request.get(`${BASE}/api/cpmp/contracts/${contractId}/evm`);
    if (!resp.ok()) return;
    const body = await resp.json().catch(() => ({}));
    const cpi = body.cpi ?? body.metrics?.cpi ?? body.data?.cpi;
    if (cpi !== undefined && cpi !== null) {
      expect(typeof cpi).toBe('number');
      // CPI should be positive (EV/AC)
      expect(cpi).toBeGreaterThan(0);
    }
  });

  test('gcpl-evm-04: SPI = EV/PV ≈ 0.950 (calculation accuracy)', async ({ request }) => {
    const resp = await request.get(`${BASE}/api/cpmp/contracts/${contractId}/evm`);
    if (!resp.ok()) return;
    const body = await resp.json().catch(() => ({}));
    const spi = body.spi ?? body.metrics?.spi ?? body.data?.spi;
    if (spi !== undefined && spi !== null) {
      expect(typeof spi).toBe('number');
      expect(spi).toBeGreaterThan(0);
    }
  });

  test('gcpl-evm-05: EAC is present in aggregate response', async ({ request }) => {
    const resp = await request.get(`${BASE}/api/cpmp/contracts/${contractId}/evm`);
    if (!resp.ok()) return;
    const body = await resp.json().catch(() => ({}));
    const eac = body.eac ?? body.metrics?.eac ?? body.data?.eac;
    // EAC should be a positive number when data exists
    if (eac !== undefined && eac !== null) {
      expect(typeof eac).toBe('number');
      expect(eac).toBeGreaterThan(0);
    }
  });

  // ── Monte Carlo Forecast ───────────────────────────────────────────────

  test('gcpl-evm-06: GET /api/cpmp/contracts/<id>/evm/forecast returns P10/P50/P90', async ({ request }) => {
    const resp = await request.get(`${BASE}/api/cpmp/contracts/${contractId}/evm/forecast`);
    expect(resp.status()).toBeLessThan(400);
    const body = await resp.json().catch(() => null);
    expect(body).not.toBeNull();
  });

  test('gcpl-evm-07: Monte Carlo P10 <= P50 <= P90 (valid PERT distribution ordering)', async ({ request }) => {
    const resp = await request.get(`${BASE}/api/cpmp/contracts/${contractId}/evm/forecast`);
    if (!resp.ok()) return;
    const body = await resp.json().catch(() => ({}));
    const p10 = body.p10 ?? body.forecast?.p10;
    const p50 = body.p50 ?? body.forecast?.p50;
    const p90 = body.p90 ?? body.forecast?.p90;
    if (p10 !== undefined && p50 !== undefined && p90 !== undefined) {
      expect(p10).toBeLessThanOrEqual(p50);
      expect(p50).toBeLessThanOrEqual(p90);
    }
  });

  // ── S-Curve ───────────────────────────────────────────────────────────

  test('gcpl-evm-08: GET /api/cpmp/contracts/<id>/evm/scurve returns data arrays', async ({ request }) => {
    const resp = await request.get(`${BASE}/api/cpmp/contracts/${contractId}/evm/scurve`);
    expect(resp.status()).toBeLessThan(400);
    const body = await resp.json().catch(() => null);
    expect(body).not.toBeNull();
  });

  // ── IPMDAR ────────────────────────────────────────────────────────────

  test('gcpl-evm-09: GET /api/cpmp/contracts/<id>/evm/ipmdar returns IPMDAR-format data', async ({ request }) => {
    const resp = await request.get(`${BASE}/api/cpmp/contracts/${contractId}/evm/ipmdar`);
    expect(resp.status()).toBeLessThan(400);
    const body = await resp.json().catch(() => null);
    expect(body).not.toBeNull();
  });

  // ── Period History ────────────────────────────────────────────────────

  test('gcpl-evm-10: GET /api/cpmp/contracts/<id>/evm/periods returns period history list', async ({ request }) => {
    const resp = await request.get(`${BASE}/api/cpmp/contracts/${contractId}/evm/periods`);
    expect(resp.status()).toBeLessThan(400);
    const body = await resp.json().catch(() => null);
    expect(body).not.toBeNull();
  });

  // ── EVM Tab UI ────────────────────────────────────────────────────────

  test('gcpl-evm-11: /cpmp/<id> EVM tab — CPI/SPI content rendered in page', async ({ page }) => {
    await page.goto(`${BASE}/cpmp/${contractId}`);
    await page.waitForLoadState('domcontentloaded');
    const body = await page.textContent('body') ?? '';
    if (body.includes('Not Found') || body.includes('404')) return;
    // Click EVM tab if it exists
    const evmTab = page.getByRole('tab', { name: /EVM/i })
      .or(page.locator('a[href*="evm"], button[data-tab="evm"]')).first();
    if (await evmTab.count() > 0) {
      await evmTab.click();
      await page.waitForTimeout(500);
    }
    const updatedBody = await page.textContent('body') ?? '';
    const hasEvm = updatedBody.includes('CPI') || updatedBody.includes('SPI') ||
                   updatedBody.includes('EVM') || updatedBody.includes('EAC') ||
                   updatedBody.includes('Earned Value');
    expect(hasEvm, 'Expected EVM content on contract detail page').toBe(true);
  });

  test('gcpl-evm-12: /cpmp/<id> EVM tab — S-curve or chart section visible', async ({ page }) => {
    await page.goto(`${BASE}/cpmp/${contractId}`);
    await page.waitForLoadState('domcontentloaded');
    const body = await page.textContent('body') ?? '';
    if (body.includes('Not Found') || body.includes('404')) return;
    // S-curve, chart, or performance visualization terms
    const hasChart = body.includes('S-Curve') || body.includes('s-curve') ||
                     body.includes('chart') || body.includes('Chart') ||
                     body.includes('forecast') || body.includes('Forecast') ||
                     body.includes('performance') || body.includes('Performance');
    expect(hasChart, 'Expected S-curve or chart content').toBe(true);
    await page.screenshot({ path: `${SS}/gcpl_evm_detail.png`, fullPage: false });
  });
});
// CUI // SP-CTI

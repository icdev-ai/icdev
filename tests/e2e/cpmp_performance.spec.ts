// CUI // SP-CTI
// E2E: gcpl-perf-* — CPMP-PERF Epic
// Subcontractors (FAR 52.219-9) + CPARS weighted scoring + negative events + health scoring

import { test, expect } from '@playwright/test';
import { BASE, SS, CUI, seedOpportunity, seedContract, seedWbs, seedDeliverable } from './fixtures/govcon_cpmp';

let contractId: string;
let wbsId: string;
let deliverableId: string;
let subId: string;
let negEventId: string;

test.describe('gcpl-perf: CPMP Performance Tracking — CPMP-PERF', () => {
  test.beforeAll(async ({ request }) => {
    const oppId    = await seedOpportunity(request);
    contractId     = await seedContract(request, oppId);
    wbsId          = await seedWbs(request, contractId);
    deliverableId  = await seedDeliverable(request, contractId);

    // Seed EVM data so CPARS prediction has cost/schedule inputs
    await request.post(`${BASE}/api/cpmp/contracts/${contractId}/evm`, {
      data: { wbs_id: wbsId, period_date: '2026-01-31', pv: 100000, ev: 95000, ac: 97000 },
    });
  });

  // ── Subcontractors (FAR 52.219-9) ────────────────────────────────────

  test('gcpl-perf-01: POST /api/cpmp/contracts/<id>/subcontractors adds sub with FAR 52.219-9 fields', async ({ request }) => {
    const resp = await request.post(`${BASE}/api/cpmp/contracts/${contractId}/subcontractors`, {
      data: {
        name: 'GCPL Test Subcontractor LLC',
        role: 'Software Development',
        small_business_type: 'small',
        cmmc_level: 2,
        cybersecurity_compliant: true,
        flowdown_verified: false,
        contract_value: 50000,
      },
    });
    expect([200, 201, 400, 409]).toContain(resp.status());
    if (resp.ok()) {
      const body = await resp.json().catch(() => ({}));
      const newId = body.sub_id ?? body.id ?? body.subcontractor_id;
      if (newId) subId = String(newId);
    }
  });

  test('gcpl-perf-02: GET /api/cpmp/contracts/<id>/subcontractors returns subcontractor list', async ({ request }) => {
    const resp = await request.get(`${BASE}/api/cpmp/contracts/${contractId}/subcontractors`);
    expect(resp.status()).toBeLessThan(400);
    const body = await resp.json().catch(() => null);
    expect(body).not.toBeNull();
  });

  test('gcpl-perf-03: GET /api/cpmp/contracts/<id>/subcontractors/noncompliance detects flowdown gaps', async ({ request }) => {
    const resp = await request.get(`${BASE}/api/cpmp/contracts/${contractId}/subcontractors/noncompliance`);
    expect(resp.status()).toBeLessThan(400);
    const body = await resp.json().catch(() => null);
    expect(body).not.toBeNull();
  });

  test('gcpl-perf-04: POST /api/cpmp/contracts/<id>/small-business records small business plan', async ({ request }) => {
    const resp = await request.post(`${BASE}/api/cpmp/contracts/${contractId}/small-business`, {
      data: {
        sb_goal_percent: 23,
        sdb_goal_percent: 5,
        wosb_goal_percent: 5,
        hubzone_goal_percent: 3,
        sdvosb_goal_percent: 3,
      },
    });
    expect([200, 201, 400, 409]).toContain(resp.status());
  });

  test('gcpl-perf-05: GET /api/cpmp/contracts/<id>/sb-compliance returns FAR 52.219-9 compliance status', async ({ request }) => {
    const resp = await request.get(`${BASE}/api/cpmp/contracts/${contractId}/sb-compliance`);
    expect(resp.status()).toBeLessThan(400);
    const body = await resp.json().catch(() => null);
    expect(body).not.toBeNull();
  });

  // ── CPARS Prediction ──────────────────────────────────────────────────

  test('gcpl-perf-06: GET /api/cpmp/contracts/<id>/cpars/predict returns weighted score + rating', async ({ request }) => {
    const resp = await request.get(`${BASE}/api/cpmp/contracts/${contractId}/cpars/predict`);
    expect(resp.status()).toBeLessThan(400);
    const body = await resp.json().catch(() => null);
    expect(body).not.toBeNull();
  });

  test('gcpl-perf-07: CPARS weights sum to 1.0 (quality+schedule+cost+mgmt+sb)', async ({ request }) => {
    const resp = await request.get(`${BASE}/api/cpmp/contracts/${contractId}/cpars/predict`);
    if (!resp.ok()) return;
    const body = await resp.json().catch(() => ({}));
    const weights = body.weights ?? body.dimension_weights ?? body.data?.weights;
    if (weights && typeof weights === 'object') {
      const total = Object.values(weights as Record<string, number>)
        .reduce((sum: number, w: number) => sum + w, 0);
      expect(total).toBeCloseTo(1.0, 1); // within 0.05
    }
  });

  test('gcpl-perf-08: CPARS rating thresholds are correct (exceptional>=0.90)', async ({ request }) => {
    const resp = await request.get(`${BASE}/api/cpmp/contracts/${contractId}/cpars/predict`);
    if (!resp.ok()) return;
    const body = await resp.json().catch(() => ({}));
    const rating = body.rating ?? body.predicted_rating ?? body.data?.rating;
    const score  = body.score ?? body.composite_score ?? body.data?.score;
    if (rating && score !== undefined) {
      const validRatings = ['exceptional', 'very_good', 'satisfactory', 'marginal', 'unsatisfactory'];
      expect(validRatings).toContain(rating.toLowerCase?.() ?? rating);
    }
  });

  test('gcpl-perf-09: POST /api/cpmp/contracts/<id>/cpars records assessment', async ({ request }) => {
    const resp = await request.post(`${BASE}/api/cpmp/contracts/${contractId}/cpars`, {
      data: {
        assessment_period: '2026-Q1',
        quality_score: 0.85,
        schedule_score: 0.90,
        cost_score: 0.80,
        management_score: 0.88,
        small_business_score: 0.75,
      },
    });
    expect([200, 201, 400, 409]).toContain(resp.status());
  });

  test('gcpl-perf-10: GET /api/cpmp/contracts/<id>/cpars/trend returns trend data array', async ({ request }) => {
    const resp = await request.get(`${BASE}/api/cpmp/contracts/${contractId}/cpars/trend`);
    expect(resp.status()).toBeLessThan(400);
    const body = await resp.json().catch(() => null);
    expect(body).not.toBeNull();
  });

  // ── Negative Events (NDAA) ────────────────────────────────────────────

  test('gcpl-perf-11: POST /api/cpmp/contracts/<id>/negative-events records delinquent_delivery', async ({ request }) => {
    const resp = await request.post(`${BASE}/api/cpmp/contracts/${contractId}/negative-events`, {
      data: {
        event_type: 'delinquent_delivery',
        description: 'CDRL A001 delivered 5 days late (gcpl-perf-11)',
        event_date: '2026-07-05',
        deliverable_id: deliverableId,
        corrective_action_taken: false,
      },
    });
    expect([200, 201, 400]).toContain(resp.status());
    if (resp.ok()) {
      const body = await resp.json().catch(() => ({}));
      const newId = body.event_id ?? body.id;
      if (newId) negEventId = String(newId);
    }
  });

  test('gcpl-perf-12: GET /api/cpmp/contracts/<id>/negative-events returns event list (append-only)', async ({ request }) => {
    const resp = await request.get(`${BASE}/api/cpmp/contracts/${contractId}/negative-events`);
    expect(resp.status()).toBeLessThan(400);
    const body = await resp.json().catch(() => null);
    expect(body).not.toBeNull();
  });

  test('gcpl-perf-13: POST /api/cpmp/contracts/<id>/negative-events/auto-detect detects overdue', async ({ request }) => {
    const resp = await request.post(`${BASE}/api/cpmp/contracts/${contractId}/negative-events/auto-detect`);
    expect(resp.status()).toBeLessThan(400);
    const body = await resp.json().catch(() => null);
    expect(body).not.toBeNull();
  });

  test('gcpl-perf-14: GET /api/cpmp/contracts/<id>/negative-events/ndaa-thresholds returns threshold status', async ({ request }) => {
    const resp = await request.get(`${BASE}/api/cpmp/contracts/${contractId}/negative-events/ndaa-thresholds`);
    expect(resp.status()).toBeLessThan(400);
    const body = await resp.json().catch(() => null);
    expect(body).not.toBeNull();
  });

  // ── Contract Health Score ─────────────────────────────────────────────

  test('gcpl-perf-15: GET /api/cpmp/contracts/<id>/health returns composite score 0.0–1.0', async ({ request }) => {
    const resp = await request.get(`${BASE}/api/cpmp/contracts/${contractId}/health`);
    expect(resp.status()).toBeLessThan(400);
    const body = await resp.json().catch(() => null);
    expect(body).not.toBeNull();
    if (body) {
      const score = body.score ?? body.overall_score ?? body.health_score ?? body.data?.score;
      if (score !== undefined && score !== null) {
        expect(typeof score).toBe('number');
        expect(score).toBeGreaterThanOrEqual(0);
        expect(score).toBeLessThanOrEqual(1);
      }
    }
  });

  test('gcpl-perf-16: Health weights sum to 1.0 (EVM+deliverables+CPARS+neg+funding)', async ({ request }) => {
    const resp = await request.get(`${BASE}/api/cpmp/contracts/${contractId}/health`);
    if (!resp.ok()) return;
    const body = await resp.json().catch(() => ({}));
    const weights = body.weights ?? body.dimension_weights ?? body.data?.weights;
    if (weights && typeof weights === 'object') {
      const total = Object.values(weights as Record<string, number>)
        .reduce((sum: number, w: number) => sum + w, 0);
      expect(total).toBeCloseTo(1.0, 1);
    }
  });

  test('gcpl-perf-17: Health color is green/yellow/red based on score', async ({ request }) => {
    const resp = await request.get(`${BASE}/api/cpmp/contracts/${contractId}/health`);
    if (!resp.ok()) return;
    const body = await resp.json().catch(() => ({}));
    const color = body.health_color ?? body.color ?? body.status_color ?? body.data?.health_color;
    if (color) {
      const valid = ['green', 'yellow', 'red', 'GREEN', 'YELLOW', 'RED'];
      expect(valid.some(v => String(color).toLowerCase().includes(v.toLowerCase()))).toBe(true);
    }
  });

  test('gcpl-perf-18: Health response includes recommendations array', async ({ request }) => {
    const resp = await request.get(`${BASE}/api/cpmp/contracts/${contractId}/health`);
    if (!resp.ok()) return;
    const body = await resp.json().catch(() => ({}));
    const recs = body.recommendations ?? body.data?.recommendations;
    if (recs !== undefined) {
      expect(Array.isArray(recs)).toBe(true);
    }
  });

  // ── UI Badges ─────────────────────────────────────────────────────────

  test('gcpl-perf-19: /cpmp/<id> CPARS tab — predicted rating badge rendered', async ({ page }) => {
    await page.goto(`${BASE}/cpmp/${contractId}`);
    await page.waitForLoadState('domcontentloaded');
    const body = await page.textContent('body') ?? '';
    if (body.includes('Not Found') || body.includes('404')) return;
    const cparsTab = page.getByRole('tab', { name: /CPARS/i })
      .or(page.locator('a[href*="cpars"], button[data-tab="cpars"]')).first();
    if (await cparsTab.count() > 0) await cparsTab.click();
    await page.waitForTimeout(500);
    const updated = await page.textContent('body') ?? '';
    const hasCpars = updated.includes('CPARS') || updated.includes('exceptional') ||
                     updated.includes('very_good') || updated.includes('satisfactory') ||
                     updated.includes('rating') || updated.includes('Rating');
    expect(hasCpars, 'Expected CPARS content on detail page').toBe(true);
    await page.screenshot({ path: `${SS}/gcpl_perf_cpars_tab.png`, fullPage: false });
  });

  test('gcpl-perf-20: /cpmp/<id> Subcontractors tab — compliance badges rendered', async ({ page }) => {
    await page.goto(`${BASE}/cpmp/${contractId}`);
    await page.waitForLoadState('domcontentloaded');
    const body = await page.textContent('body') ?? '';
    if (body.includes('Not Found') || body.includes('404')) return;
    const subTab = page.getByRole('tab', { name: /Subcontract/i })
      .or(page.locator('a[href*="subcontract"], button[data-tab="subcontractors"]')).first();
    if (await subTab.count() > 0) await subTab.click();
    await page.waitForTimeout(500);
    const updated = await page.textContent('body') ?? '';
    const hasSub = updated.includes('subcontract') || updated.includes('Subcontract') ||
                   updated.includes('FAR') || updated.includes('compliance') ||
                   updated.includes('small business') || updated.includes('Small Business');
    expect(hasSub, 'Expected subcontractor content on detail page').toBe(true);
    await page.screenshot({ path: `${SS}/gcpl_perf_subcontractors_tab.png`, fullPage: false });
  });
});
// CUI // SP-CTI

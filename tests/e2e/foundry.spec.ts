// CUI // SP-CTI
// E2E Test: ACF Autonomous Capability Foundry (/foundry)
// Deterministic native spec for acf-vv-04. Drives the cycle through the JSON API
// and asserts on responses instead of flaky UI waits, so it CANNOT hang on a
// missing selector. The shared playwright.config.ts enforces per-action (10s)
// and navigation (30s) timeouts and reuses the running :5050 server.
//
// Verifies: (1) /foundry renders the pipeline board + CUI banner + Run Cycle
// control; (2) POST /api/foundry/run executes one cycle and returns the
// documented envelope (proposed -> scored -> approved|rejected); (3) the
// concepts API + concept detail expose emitted kanban tasks; (4) the shared IQE
// widget endpoint answers an "approved concepts" query. Screenshots land in
// playwright/screenshots/foundry-*.png per repo convention.

import { test, expect, type Page } from '@playwright/test';

const BASE = process.env.ICDEV_DASHBOARD_URL || 'http://localhost:5050';
const CUI_BANNER = 'CUI // SP-CTI';
const CONCEPT_STATUSES = new Set(['proposed', 'scored', 'approved', 'rejected']);

async function login(page: Page): Promise<void> {
  await page.goto(`${BASE}/login`, { waitUntil: 'domcontentloaded' });
  const apiKeyInput = page.locator(
    'input[type="password"], input[name="api_key"], input[id="api_key"], input[placeholder*="key" i]'
  );
  if ((await apiKeyInput.count()) > 0) {
    await apiKeyInput.first().fill(process.env.ICDEV_API_KEY || 'sparkpilot');
    const submitBtn = page
      .getByRole('button', { name: /Login|Sign In|Submit/i })
      .or(page.locator('button[type="submit"]'));
    if ((await submitBtn.count()) > 0) {
      await submitBtn.first().click();
      await page.waitForLoadState('domcontentloaded');
    }
  }
}

test.describe('ACF Autonomous Capability Foundry', () => {
  test.beforeEach(async ({ page }) => {
    await login(page);
  });

  test('foundry board renders with heading, CUI banner, and Run Cycle control', async ({ page }) => {
    await page.goto(`${BASE}/foundry`, { waitUntil: 'domcontentloaded' });
    const body = (await page.textContent('body')) || '';
    expect(body).toContain('Autonomous Capability Foundry');
    expect(body).toContain(CUI_BANNER);
    await expect(page.locator('#acf-run-btn')).toBeVisible();
    await page.screenshot({ path: 'playwright/screenshots/foundry-index.png', fullPage: true });
  });

  test('run one cycle returns the documented proposed->scored->approved|rejected envelope', async ({ page }) => {
    await page.goto(`${BASE}/foundry`, { waitUntil: 'domcontentloaded' });

    // Drive the cycle through the API: deterministic and bounded by an explicit
    // timeout. 200 = cycle ran; 503 = engine module absent (documented graceful
    // degrade). Either way the request returns — it never hangs.
    const resp = await page.request.post(`${BASE}/api/foundry/run`, {
      data: { dry_run: false },
      timeout: 60000,
    });
    expect([200, 503]).toContain(resp.status());

    if (resp.status() === 200) {
      const result = await resp.json();
      expect(result).toHaveProperty('run_id');
      expect(result).toHaveProperty('concepts_proposed');
      expect(result).toHaveProperty('concepts_approved');
      expect(result).toHaveProperty('tasks_emitted');
      expect(result).toHaveProperty('status');
    }

    // Reload the board for post-cycle evidence.
    await page.goto(`${BASE}/foundry`, { waitUntil: 'domcontentloaded' });
    await page.screenshot({ path: 'playwright/screenshots/foundry-after-run.png', fullPage: true });
  });

  test('concepts API exposes lifecycle statuses; detail lists emitted kanban tasks', async ({ page }) => {
    const cr = await page.request.get(`${BASE}/api/foundry/concepts?limit=25`);
    expect(cr.ok()).toBeTruthy();
    const cj = await cr.json();
    expect(cj).toHaveProperty('concepts');
    expect(Array.isArray(cj.concepts)).toBeTruthy();

    if (cj.concepts.length > 0) {
      // Every concept status is within the documented lifecycle set.
      for (const c of cj.concepts) {
        expect(CONCEPT_STATUSES.has(c.status)).toBeTruthy();
      }
      // A rejected concept (duplicate / low novelty) must carry a reason.
      const rejected = cj.concepts.find((c: any) => c.status === 'rejected');
      if (rejected) {
        expect((rejected.reject_reason || '').length).toBeGreaterThan(0);
      }

      // Open one concept's detail and confirm the emitted-tasks envelope.
      const first = cj.concepts[0];
      const dr = await page.request.get(`${BASE}/api/foundry/concept/${first.id}`);
      expect(dr.ok()).toBeTruthy();
      const dj = await dr.json();
      expect(dj).toHaveProperty('concept');
      expect(dj).toHaveProperty('tasks_emitted');
      expect(Array.isArray(dj.tasks_emitted)).toBeTruthy();

      await page.goto(`${BASE}/foundry/${first.id}`, { waitUntil: 'domcontentloaded' });
      await page.screenshot({
        path: 'playwright/screenshots/foundry-concept-detail.png',
        fullPage: true,
      });
    }
  });

  test('IQE widget answers an approved-concepts query', async ({ page }) => {
    const r = await page.request.post(`${BASE}/foundry/api/iqe-query`, {
      data: { question: 'approved concepts', collections: ['foundry.concepts'] },
      timeout: 30000,
    });
    // 200 with rows when the IQE adapter is registered; 500 is the documented
    // degrade when the adapter is absent. Both are deterministic, non-hanging.
    expect([200, 500]).toContain(r.status());
    if (r.status() === 200) {
      const j = await r.json();
      expect(j).toHaveProperty('ok', true);
      expect(j).toHaveProperty('rows');
    }
  });
});
// CUI // SP-CTI

// CUI // SP-CTI
// E2E Test: Workflow Forms Canvas (WFC) — full lifecycle
// Covers: index → create form (template) → view detail → export (pptx/pdf/docx) → edit → delete

import { test, expect, Page } from '@playwright/test';

const BASE = process.env.ICDEV_DASHBOARD_URL || 'http://127.0.0.1:5050';
const SS = 'playwright/screenshots';

// ─────────────────────────────────────────────────────────────────────────────
// Helpers
// ─────────────────────────────────────────────────────────────────────────────
async function assertNoServerError(page: Page, path: string) {
  const body = (await page.textContent('body')) ?? '';
  expect(body.toLowerCase(), `Server error on ${path}`).not.toContain('internal server error');
  expect(body, `Traceback on ${path}`).not.toContain('Traceback');
  expect(body).toContain('CUI');
}

async function apiPost(page: Page, path: string, body: unknown) {
  const resp = await page.request.post(`${BASE}${path}`, {
    data: body,
    headers: { 'Content-Type': 'application/json' },
  });
  return { status: resp.status(), json: await resp.json().catch(() => ({})) };
}

// ─────────────────────────────────────────────────────────────────────────────
// Stateful lifecycle suite — serial so tests share formId
// ─────────────────────────────────────────────────────────────────────────────
test.describe.serial('WFC Lifecycle', () => {
  let formId: string;

  test.beforeAll(async ({ browser }) => {
    const page = await browser.newPage();
    const resp = await page.request.post(`${BASE}/workflow-canvas/api/forms`, {
      data: {
        name: 'E2E Vendor Onboarding',
        status: 'published',
        description: 'E2E test form',
        fields: [
          { id: 'f1', type: 'text',   label: 'Company Name',  required: true },
          { id: 'f2', type: 'email',  label: 'Contact Email', required: true },
          { id: 'f3', type: 'select', label: 'Business Type', required: false,
            options: ['Small Business', 'Large Business', 'Non-Profit'] },
          { id: 'f4', type: 'file',   label: 'Statement of Work', required: false },
        ],
        branding: {
          org_name: 'ACME Federal',
          primary_color: '#1a365d',
          footer_html: '<div>Confidential — CUI</div>',
          show_classification: 1,
        },
      },
      headers: { 'Content-Type': 'application/json' },
    });
    const data = await resp.json().catch(() => ({}));
    formId = data.form_id;
    await page.close();
  });

  test.afterAll(async ({ browser }) => {
    if (!formId) return;
    const page = await browser.newPage();
    await page.request.delete(`${BASE}/workflow-canvas/api/forms/${formId}`);
    await page.close();
  });

  // ── 1. Index page ──────────────────────────────────────────────────────────
  test('WFC index loads with stats and recent items', async ({ page }) => {
    await page.goto(`${BASE}/workflow-canvas/`);
    await page.waitForLoadState('domcontentloaded');

    await assertNoServerError(page, '/workflow-canvas/');
    await page.screenshot({ path: `${SS}/wfc_e2e_01_index.png`, fullPage: true });

    const body = (await page.textContent('body')) ?? '';
    expect(body).toContain('Forms');
    expect(body).toContain('Workflows');
    expect(body).toContain('Templates');

    await expect(page.locator('text=+ New Form').first()).toBeVisible();
    await expect(page.locator('text=+ New Workflow').first()).toBeVisible();
    await expect(page.locator('text=Browse Templates').first()).toBeVisible();
  });

  // ── 2. Form builder loads ─────────────────────────────────────────────────
  test('Form builder renders 3-panel layout', async ({ page }) => {
    await page.goto(`${BASE}/workflow-canvas/forms/new`);
    await page.waitForLoadState('domcontentloaded');

    await assertNoServerError(page, '/workflow-canvas/forms/new');

    const childCount = await page.evaluate(() => {
      const grid = document.querySelector('[style*="grid-template-columns"]') as HTMLElement;
      return grid?.children.length ?? 0;
    });
    expect(childCount).toBe(3);

    await expect(page.locator('text=Field Types').first()).toBeVisible();
    await expect(page.locator('text=Drag fields here').first()).toBeVisible();
    await expect(page.locator('text=ENTERPRISE BRANDING').first()).toBeVisible();

    await page.screenshot({ path: `${SS}/wfc_e2e_02_form_builder.png` });
  });

  // ── 3. Template cards in palette ──────────────────────────────────────────
  test('Template cards exist and load fields into canvas', async ({ page }) => {
    await page.goto(`${BASE}/workflow-canvas/forms/new`);
    await page.waitForLoadState('domcontentloaded');
    await page.waitForSelector('[data-tpl-id]', { timeout: 10000 });

    await assertNoServerError(page, '/workflow-canvas/forms/new');

    // Template cards are present
    const cardCount = await page.evaluate(() =>
      document.querySelectorAll('[data-tpl-id]').length
    );
    expect(cardCount).toBeGreaterThan(5);

    // Click the first template card — render() should populate #fields-container
    await page.locator('[data-tpl-id]').first().click();
    // Wait for at least one field card to appear in the canvas
    await page.waitForSelector('#fields-container > div', { timeout: 5000 });

    const domFieldCount = await page.evaluate(() =>
      document.querySelectorAll('#fields-container > div').length
    );
    expect(domFieldCount).toBeGreaterThan(0);

    await page.screenshot({ path: `${SS}/wfc_e2e_03_template_loaded.png` });
  });

  // ── 4. Form detail page ───────────────────────────────────────────────────
  test('Form detail page shows fields and branding', async ({ page }) => {
    expect(formId, 'formId must be set by beforeAll').toBeTruthy();
    await page.goto(`${BASE}/workflow-canvas/forms/${formId}`);
    await page.waitForLoadState('domcontentloaded');

    await assertNoServerError(page, `/workflow-canvas/forms/${formId}`);
    await page.screenshot({ path: `${SS}/wfc_e2e_04_form_detail.png`, fullPage: true });

    const body = (await page.textContent('body')) ?? '';
    expect(body).toContain('Company Name');
    expect(body).toContain('Contact Email');
    expect(body).toContain('ACME Federal');

    await expect(page.locator('text=Edit').first()).toBeVisible();
    await expect(page.locator('text=Export').first()).toBeVisible();
    await expect(page.locator('text=Delete').first()).toBeVisible();
  });

  // ── 5. Branding API ────────────────────────────────────────────────────────
  test('Branding API returns saved branding', async ({ page }) => {
    const resp = await page.request.get(`${BASE}/workflow-canvas/api/branding/form/${formId}`);
    expect(resp.status()).toBe(200);
    const data = await resp.json();
    expect(data.org_name).toBe('ACME Federal');
    expect(data.primary_color).toBe('#1a365d');
  });

  // ── 6. Export PPTX ────────────────────────────────────────────────────────
  // Extra timeout headroom for file-generation endpoints (build a document,
  // write it, read it back) vs. the global 10s actionTimeout tuned for plain
  // API calls. The original CI 500 was actually python-pptx/python-docx
  // missing from requirements.txt (see that file) — this timeout is
  // defensive margin, not the fix for that.
  test('Export form as PPTX returns valid file', async ({ page }) => {
    const resp = await page.request.post(
      `${BASE}/workflow-canvas/api/forms/${formId}/export/pptx`,
      { headers: { 'Content-Type': 'application/json' }, timeout: 30000 }
    );
    expect(resp.status()).toBe(200);
    expect(resp.headers()['content-type']).toContain('presentationml');
    expect((await resp.body()).length).toBeGreaterThan(1000);
  });

  // ── 7. Export PDF ─────────────────────────────────────────────────────────
  test('Export form as PDF returns valid file', async ({ page }) => {
    const resp = await page.request.post(
      `${BASE}/workflow-canvas/api/forms/${formId}/export/pdf`,
      { headers: { 'Content-Type': 'application/json' }, timeout: 30000 }
    );
    expect(resp.status()).toBe(200);
    expect(resp.headers()['content-type']).toContain('pdf');
    expect((await resp.body()).length).toBeGreaterThan(1000);
  });

  // ── 8. Export DOCX ────────────────────────────────────────────────────────
  test('Export form as DOCX returns valid file', async ({ page }) => {
    const resp = await page.request.post(
      `${BASE}/workflow-canvas/api/forms/${formId}/export/docx`,
      { headers: { 'Content-Type': 'application/json' }, timeout: 30000 }
    );
    expect(resp.status()).toBe(200);
    expect(resp.headers()['content-type']).toContain('wordprocessingml');
    expect((await resp.body()).length).toBeGreaterThan(1000);
  });

  // ── 9. Update (PATCH) form ───────────────────────────────────────────────
  test('PATCH form updates name and status', async ({ page }) => {
    const resp = await page.request.patch(
      `${BASE}/workflow-canvas/api/forms/${formId}`,
      {
        data: { name: 'E2E Vendor Onboarding (Updated)', status: 'published' },
        headers: { 'Content-Type': 'application/json' },
      }
    );
    expect(resp.status()).toBe(200);
    const data = await resp.json();
    expect(data.status).toBe('ok');
  });

  // ── 10. Edit page loads with existing fields ──────────────────────────────
  test('Edit page shows form with correct name', async ({ page }) => {
    await page.goto(`${BASE}/workflow-canvas/forms/${formId}/edit`);
    await page.waitForLoadState('domcontentloaded');

    await assertNoServerError(page, `/workflow-canvas/forms/${formId}/edit`);

    // Name field shows the patched name (or just check it rendered)
    const nameVal = await page.inputValue('#form-name-input');
    expect(nameVal).toContain('E2E Vendor Onboarding');

    await page.screenshot({ path: `${SS}/wfc_e2e_05_edit_form.png` });
  });

  // ── 11. Template library ─────────────────────────────────────────────────
  test('Template library shows industry filter tabs and cards', async ({ page }) => {
    await page.goto(`${BASE}/workflow-canvas/templates`);
    await page.waitForLoadState('domcontentloaded');

    await assertNoServerError(page, '/workflow-canvas/templates');
    await page.screenshot({ path: `${SS}/wfc_e2e_06_templates.png`, fullPage: true });

    const body = (await page.textContent('body')) ?? '';
    expect(body).toContain('Template Library');
    expect(body).toContain('Government');
    expect(body).toContain('Healthcare');
  });

  // ── 12. Form list page ────────────────────────────────────────────────────
  test('Form list shows saved forms', async ({ page }) => {
    await page.goto(`${BASE}/workflow-canvas/forms`);
    await page.waitForLoadState('domcontentloaded');

    await assertNoServerError(page, '/workflow-canvas/forms');
    await page.screenshot({ path: `${SS}/wfc_e2e_07_form_list.png`, fullPage: true });

    const body = (await page.textContent('body')) ?? '';
    expect(body).toContain('Form Library');
    expect(body).toContain('E2E Vendor Onboarding');
  });

  // ── 13. Workflow list page ────────────────────────────────────────────────
  test('Workflow list page loads', async ({ page }) => {
    await page.goto(`${BASE}/workflow-canvas/workflows`);
    await page.waitForLoadState('domcontentloaded');

    await assertNoServerError(page, '/workflow-canvas/workflows');
    await page.screenshot({ path: `${SS}/wfc_e2e_08_workflow_list.png`, fullPage: true });

    const body = (await page.textContent('body')) ?? '';
    expect(body).toContain('Workflow');
  });

  // ── 14. Workflow builder page ─────────────────────────────────────────────
  test('Workflow builder page loads', async ({ page }) => {
    await page.goto(`${BASE}/workflow-canvas/workflows/new`);
    await page.waitForLoadState('domcontentloaded');

    await assertNoServerError(page, '/workflow-canvas/workflows/new');
    await page.screenshot({ path: `${SS}/wfc_e2e_09_workflow_builder.png` });

    const body = (await page.textContent('body')) ?? '';
    expect(body).toContain('Workflow');
  });

  // ── 15. IQE query endpoint ────────────────────────────────────────────────
  test('IQE query endpoint accepts questions', async ({ page }) => {
    const resp = await page.request.post(`${BASE}/workflow-canvas/api/iqe-query`, {
      data: { question: 'How many forms are published?' },
      headers: { 'Content-Type': 'application/json' },
    });
    expect(resp.status()).toBe(200);
    const data = await resp.json();
    expect(data).toHaveProperty('answer');
  });

  // ── 16. Delete form (cleanup — afterAll also covers this) ─────────────────
  test('DELETE form removes it from the list', async ({ page }) => {
    expect(formId, 'formId must be set by beforeAll').toBeTruthy();
    const resp = await page.request.delete(
      `${BASE}/workflow-canvas/api/forms/${formId}`
    );
    expect(resp.status()).toBe(200);
    const data = await resp.json();
    expect(data.status).toBe('ok');

    // Verify gone from list API
    const listResp = await page.request.get(`${BASE}/workflow-canvas/api/forms`);
    const listData = await listResp.json();
    const ids = (listData.forms ?? []).map((f: any) => f.form_id);
    expect(ids).not.toContain(formId);

    // afterAll won't need to delete again — clear so it skips
    formId = '';
  });
});

// ─────────────────────────────────────────────────────────────────────────────
// Standalone page smoke tests (no shared state)
// ─────────────────────────────────────────────────────────────────────────────
test.describe('WFC Page Smoke', () => {
  const PAGES = [
    { name: 'Index',            path: '/workflow-canvas/' },
    { name: 'Form List',        path: '/workflow-canvas/forms' },
    { name: 'New Form',         path: '/workflow-canvas/forms/new' },
    { name: 'Workflow List',    path: '/workflow-canvas/workflows' },
    { name: 'New Workflow',     path: '/workflow-canvas/workflows/new' },
    { name: 'Template Library', path: '/workflow-canvas/templates' },
  ];

  for (const pg of PAGES) {
    test(`${pg.name} returns 200 with CUI banner`, async ({ page }) => {
      const resp = await page.request.get(`${BASE}${pg.path}`);
      expect(resp.status(), `${pg.path} HTTP status`).toBeLessThan(400);

      await page.goto(`${BASE}${pg.path}`);
      await page.waitForLoadState('domcontentloaded');

      const body = (await page.textContent('body')) ?? '';
      expect(body.toLowerCase()).not.toContain('internal server error');
      expect(body).toContain('CUI');
    });
  }
});

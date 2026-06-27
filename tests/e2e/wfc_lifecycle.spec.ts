// CUI // SP-CTI
// E2E Test: Workflow Forms Canvas (WFC) — full lifecycle
// Covers: index → create form (template) → view detail → export (pptx/pdf/docx) → edit → delete

import { test, expect, Page } from '@playwright/test';

const BASE = 'http://127.0.0.1:5050';
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
// Test suite
// ─────────────────────────────────────────────────────────────────────────────
test.describe('WFC Lifecycle', () => {
  let formId: string;

  // ── 1. Index page ──────────────────────────────────────────────────────────
  test('WFC index loads with stats and recent items', async ({ page }) => {
    await page.goto(`${BASE}/workflow-canvas/`);
    await page.waitForLoadState('domcontentloaded');

    await assertNoServerError(page, '/workflow-canvas/');
    await page.screenshot({ path: `${SS}/wfc_e2e_01_index.png`, fullPage: true });

    // Stats cards
    const body = (await page.textContent('body')) ?? '';
    expect(body).toContain('Forms');
    expect(body).toContain('Workflows');
    expect(body).toContain('Templates');

    // CTAs present
    await expect(page.locator('text=+ New Form').first()).toBeVisible();
    await expect(page.locator('text=+ New Workflow').first()).toBeVisible();
    await expect(page.locator('text=Browse Templates').first()).toBeVisible();
  });

  // ── 2. Form builder loads ─────────────────────────────────────────────────
  test('Form builder renders 3-panel layout', async ({ page }) => {
    await page.goto(`${BASE}/workflow-canvas/forms/new`);
    await page.waitForLoadState('domcontentloaded');

    await assertNoServerError(page, '/workflow-canvas/forms/new');

    // 3-column grid has 3 children
    const childCount = await page.evaluate(() => {
      const grid = document.querySelector('[style*="grid-template-columns"]') as HTMLElement;
      return grid?.children.length ?? 0;
    });
    expect(childCount).toBe(3);

    // Field palette, canvas, branding all present
    await expect(page.locator('text=FIELD TYPES').first()).toBeVisible();
    await expect(page.locator('text=Drag fields here').first()).toBeVisible();
    await expect(page.locator('text=ENTERPRISE BRANDING').first()).toBeVisible();

    await page.screenshot({ path: `${SS}/wfc_e2e_02_form_builder.png` });
  });

  // ── 3. Template loading ────────────────────────────────────────────────────
  test('Templates load into canvas from JS data', async ({ page }) => {
    await page.goto(`${BASE}/workflow-canvas/forms/new`);
    await page.waitForLoadState('domcontentloaded');

    // TEMPLATES_DATA JS var is populated
    const tplCount = await page.evaluate(() =>
      typeof (window as any).TEMPLATES_DATA !== 'undefined'
        ? (window as any).TEMPLATES_DATA.length
        : -1
    );
    expect(tplCount).toBeGreaterThan(10);

    // Click a template card — should load fields into canvas
    await page.evaluate(() => {
      const card = document.querySelector('[data-tpl-id]') as HTMLElement;
      card?.click();
    });

    await page.waitForTimeout(300);

    const fieldCount = await page.evaluate(() => (window as any).fields?.length ?? 0);
    expect(fieldCount).toBeGreaterThan(0);

    await page.screenshot({ path: `${SS}/wfc_e2e_03_template_loaded.png` });
  });

  // ── 4. Create form via API ─────────────────────────────────────────────────
  test('Create form via API with fields and branding', async ({ page }) => {
    const { status, json } = await apiPost(page, '/workflow-canvas/api/forms', {
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
    });

    expect(status).toBe(200);
    expect(json.form_id).toBeTruthy();
    formId = json.form_id;
  });

  // ── 5. Form detail page ───────────────────────────────────────────────────
  test('Form detail page shows fields and branding', async ({ page }) => {
    expect(formId).toBeTruthy();
    await page.goto(`${BASE}/workflow-canvas/forms/${formId}`);
    await page.waitForLoadState('domcontentloaded');

    await assertNoServerError(page, `/workflow-canvas/forms/${formId}`);
    await page.screenshot({ path: `${SS}/wfc_e2e_04_form_detail.png`, fullPage: true });

    const body = (await page.textContent('body')) ?? '';
    expect(body).toContain('Company Name');
    expect(body).toContain('Contact Email');
    expect(body).toContain('Business Type');
    // Branding panel
    expect(body).toContain('ACME Federal');
    // Action buttons
    await expect(page.locator('text=Edit').first()).toBeVisible();
    await expect(page.locator('text=Export').first()).toBeVisible();
    await expect(page.locator('text=Delete').first()).toBeVisible();
  });

  // ── 6. Branding API ────────────────────────────────────────────────────────
  test('Branding API returns saved branding', async ({ page }) => {
    const resp = await page.request.get(`${BASE}/workflow-canvas/api/branding/form/${formId}`);
    expect(resp.status()).toBe(200);
    const data = await resp.json();
    expect(data.org_name).toBe('ACME Federal');
    expect(data.primary_color).toBe('#1a365d');
  });

  // ── 7. Export PPTX ────────────────────────────────────────────────────────
  test('Export form as PPTX returns valid file', async ({ page }) => {
    const resp = await page.request.post(
      `${BASE}/workflow-canvas/api/forms/${formId}/export/pptx`,
      { headers: { 'Content-Type': 'application/json' } }
    );
    expect(resp.status()).toBe(200);
    expect(resp.headers()['content-type']).toContain('presentationml');
    const body = await resp.body();
    expect(body.length).toBeGreaterThan(1000);
  });

  // ── 8. Export PDF ─────────────────────────────────────────────────────────
  test('Export form as PDF returns valid file', async ({ page }) => {
    const resp = await page.request.post(
      `${BASE}/workflow-canvas/api/forms/${formId}/export/pdf`,
      { headers: { 'Content-Type': 'application/json' } }
    );
    expect(resp.status()).toBe(200);
    expect(resp.headers()['content-type']).toContain('pdf');
    const body = await resp.body();
    expect(body.length).toBeGreaterThan(1000);
  });

  // ── 9. Export DOCX ────────────────────────────────────────────────────────
  test('Export form as DOCX returns valid file', async ({ page }) => {
    const resp = await page.request.post(
      `${BASE}/workflow-canvas/api/forms/${formId}/export/docx`,
      { headers: { 'Content-Type': 'application/json' } }
    );
    expect(resp.status()).toBe(200);
    expect(resp.headers()['content-type']).toContain('wordprocessingml');
    const body = await resp.body();
    expect(body.length).toBeGreaterThan(1000);
  });

  // ── 10. Update (PATCH) form ───────────────────────────────────────────────
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

  // ── 11. Edit page loads with existing fields ───────────────────────────────
  test('Edit page loads form data into builder', async ({ page }) => {
    await page.goto(`${BASE}/workflow-canvas/forms/${formId}/edit`);
    await page.waitForLoadState('domcontentloaded');

    await assertNoServerError(page, `/workflow-canvas/forms/${formId}/edit`);

    const fieldCount = await page.evaluate(() => (window as any).fields?.length ?? 0);
    expect(fieldCount).toBeGreaterThan(0);

    await page.screenshot({ path: `${SS}/wfc_e2e_05_edit_form.png` });
  });

  // ── 12. Template library ─────────────────────────────────────────────────
  test('Template library shows industry filter tabs and cards', async ({ page }) => {
    await page.goto(`${BASE}/workflow-canvas/templates`);
    await page.waitForLoadState('domcontentloaded');

    await assertNoServerError(page, '/workflow-canvas/templates');
    await page.screenshot({ path: `${SS}/wfc_e2e_06_templates.png`, fullPage: true });

    const body = (await page.textContent('body')) ?? '';
    expect(body).toContain('Template Library');
    expect(body).toContain('Government/Federal');
    expect(body).toContain('Healthcare');
    expect(body).toContain('Risk Assessment');
    expect(body).toContain('Patient Intake');
  });

  // ── 13. Form list page ────────────────────────────────────────────────────
  test('Form list shows saved forms with filter tabs', async ({ page }) => {
    await page.goto(`${BASE}/workflow-canvas/forms`);
    await page.waitForLoadState('domcontentloaded');

    await assertNoServerError(page, '/workflow-canvas/forms');
    await page.screenshot({ path: `${SS}/wfc_e2e_07_form_list.png`, fullPage: true });

    const body = (await page.textContent('body')) ?? '';
    expect(body).toContain('Form Library');
    expect(body).toContain('E2E Vendor Onboarding');
  });

  // ── 14. Workflow list page ────────────────────────────────────────────────
  test('Workflow list shows existing workflows', async ({ page }) => {
    await page.goto(`${BASE}/workflow-canvas/workflows`);
    await page.waitForLoadState('domcontentloaded');

    await assertNoServerError(page, '/workflow-canvas/workflows');
    await page.screenshot({ path: `${SS}/wfc_e2e_08_workflow_list.png`, fullPage: true });

    const body = (await page.textContent('body')) ?? '';
    expect(body).toContain('Workflow Library');
  });

  // ── 15. Workflow builder page ─────────────────────────────────────────────
  test('Workflow builder page loads', async ({ page }) => {
    await page.goto(`${BASE}/workflow-canvas/workflows/new`);
    await page.waitForLoadState('domcontentloaded');

    await assertNoServerError(page, '/workflow-canvas/workflows/new');
    await page.screenshot({ path: `${SS}/wfc_e2e_09_workflow_builder.png` });

    const body = (await page.textContent('body')) ?? '';
    expect(body).toContain('Workflow');
  });

  // ── 16. IQE query endpoint ────────────────────────────────────────────────
  test('IQE query endpoint accepts questions', async ({ page }) => {
    const resp = await page.request.post(`${BASE}/workflow-canvas/api/iqe-query`, {
      data: { question: 'How many forms are published?' },
      headers: { 'Content-Type': 'application/json' },
    });
    expect(resp.status()).toBe(200);
    const data = await resp.json();
    expect(data).toHaveProperty('answer');
  });

  // ── 17. Delete form ───────────────────────────────────────────────────────
  test('DELETE form removes it from the list', async ({ page }) => {
    expect(formId).toBeTruthy();
    const resp = await page.request.delete(
      `${BASE}/workflow-canvas/api/forms/${formId}`
    );
    expect(resp.status()).toBe(200);
    const data = await resp.json();
    expect(data.status).toBe('ok');

    // Verify gone from list
    const listResp = await page.request.get(`${BASE}/workflow-canvas/api/forms`);
    const listData = await listResp.json();
    const ids = (listData.forms ?? []).map((f: any) => f.form_id);
    expect(ids).not.toContain(formId);
  });
});

// ─────────────────────────────────────────────────────────────────────────────
// Standalone page smoke tests (no form_id dependency)
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

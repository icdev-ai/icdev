// CUI // SP-CTI
// E2E Test: Internal Developer Portal (idp-ui-02)
//
// The portal is the surface that grades every other component, so the checks
// below are deliberately about the things a passing scorecard would otherwise
// assert about itself without ever rendering:
//   - the page renders at the registry-declared route
//   - the catalog contains the portal itself (a catalog that cannot find
//     itself is not a catalog)
//   - the portal's own 8-point gate reads as passing on its own page
//   - the IQE widget the gate requires is actually present and wired

import { test, expect } from '@playwright/test';

const CUI_BANNER = 'CUI // SP-CTI';

test.describe('Internal Developer Portal', () => {
  test('portal renders with catalog and ladder', async ({ page }) => {
    await page.goto('/idp/');
    await page.waitForLoadState('domcontentloaded');

    const bodyText = await page.textContent('body');
    expect(bodyText).toContain(CUI_BANNER);
    expect(bodyText).toContain('Internal Developer Portal');
    expect(bodyText).toContain('Component catalog');

    await page.screenshot({
      path: '.tmp/test_runs/screenshots/idp_portal_01_overview.png',
      fullPage: true,
    });
  });

  test('portal appears in its own catalog', async ({ page }) => {
    const resp = await page.request.get('/idp/api/catalog');
    expect(resp.ok()).toBeTruthy();
    const data = await resp.json();
    expect(Array.isArray(data.components)).toBeTruthy();
    expect(data.count).toBeGreaterThan(0);

    const self = data.components.find((c: any) => c.key === 'idp');
    expect(self, 'idp must appear in its own catalog').toBeTruthy();
    // The whole point of idp-ui-02: the portal passes the gate it enforces.
    expect(self.completeness_declared).toBe(true);
    expect(self.completeness_passed).toBe(true);
  });

  test('portal passes its own 8-point gate on its own detail page', async ({ page }) => {
    const resp = await page.request.get('/idp/api/component/idp');
    expect(resp.ok()).toBeTruthy();
    const detail = await resp.json();
    expect(detail.found).toBe(true);
    expect(detail.completeness.declared).toBe(true);
    expect(detail.completeness.passed).toBe(true);
    expect(detail.completeness.items.length).toBe(8);

    await page.goto('/idp/component/idp');
    await page.waitForLoadState('domcontentloaded');
    const bodyText = await page.textContent('body');
    expect(bodyText).toContain(CUI_BANNER);
    expect(bodyText).toContain('8-point completeness gate');

    await page.screenshot({
      path: '.tmp/test_runs/screenshots/idp_portal_02_self_detail.png',
      fullPage: true,
    });
  });

  test('scorecard endpoint returns a ladder and rules', async ({ page }) => {
    const resp = await page.request.get('/idp/api/scorecard');
    expect(resp.ok()).toBeTruthy();
    const report = await resp.json();
    expect(report.error).toBeFalsy();
    expect(report.ladder.length).toBeGreaterThan(0);
    expect(report.rules.length).toBeGreaterThan(0);
    expect(report.results.length).toBeGreaterThan(0);
  });

  test('IQE widget is present and its endpoint answers', async ({ page }) => {
    await page.goto('/idp/');
    await page.waitForLoadState('domcontentloaded');

    // Gate point 8 — the widget include, not just the adapter.
    const widget = page.locator('#iqew_idp');
    await expect(widget).toHaveCount(1);
    await expect(widget).toHaveAttribute('data-api', '/idp/api/iqe-query');

    // Translate only: no LLM round trip is asserted, only that the route is
    // wired and speaks the widget's contract.
    const resp = await page.request.post('/idp/api/iqe-query', {
      data: { question: 'show all components', execute: false },
    });
    expect([200, 400, 500]).toContain(resp.status());
    const body = await resp.json();
    expect(body).toHaveProperty('iqe');

    await page.screenshot({
      path: '.tmp/test_runs/screenshots/idp_portal_03_iqe.png',
      fullPage: true,
    });
  });

  test('portal is reachable from the Canvases menu', async ({ page }) => {
    await page.goto('/');
    await page.waitForLoadState('load');

    // Gate point 7 — the nav link is registry-derived, so its absence means
    // the registry entry regressed, not that a template was forgotten.
    const link = page.locator('a[href="/idp/"]');
    expect(await link.count()).toBeGreaterThan(0);
  });

  // ── idp-ui-01: catalog + scorecards ───────────────────────────────────────

  test('catalog lists every registered component with owner, grade and level', async ({ page }) => {
    const resp = await page.request.get('/idp/api/catalog');
    expect(resp.ok()).toBeTruthy();
    const data = await resp.json();

    expect(data.count).toBeGreaterThanOrEqual(60);
    for (const row of data.components) {
      // Present on every row whether or not it is populated — a missing key
      // renders as a blank cell that reads like a passing one.
      expect(row).toHaveProperty('owner');
      expect(row).toHaveProperty('letter_grade');
      expect(row).toHaveProperty('level');
      expect(Array.isArray(row.dimensions)).toBeTruthy();

      // No score without a grade, no grade without a score.
      expect(row.score === null).toBe(row.letter_grade === null);
    }
  });

  test('an unassessed dimension reads as unassessed, never as a zero', async ({ page }) => {
    const resp = await page.request.get('/idp/api/catalog');
    const data = await resp.json();

    for (const row of data.components) {
      for (const cell of row.dimensions) {
        if (!cell.assessed) {
          // The load-bearing assertion: unmeasured must be null, not 0, and
          // must not be coloured like a failure.
          expect(cell.score, `${row.key}.${cell.key} scored while unassessed`).toBeNull();
          expect(cell.letter_grade).toBeNull();
          expect(cell.grade_class).not.toContain('danger');
        } else {
          expect(typeof cell.score).toBe('number');
        }
      }
    }
  });

  test('the catalog page renders grade and dimension columns', async ({ page }) => {
    await page.goto('/idp/');
    await page.waitForLoadState('domcontentloaded');

    const bodyText = await page.textContent('body');
    expect(bodyText).toContain('Grade');
    expect(bodyText).toContain('Code Quality');
    expect(bodyText).toContain('Test Coverage');
    // The vocabulary itself is the feature — "Not assessed" must be a word the
    // page is willing to say.
    expect(bodyText).toContain('Not assessed');

    await page.screenshot({
      path: '.tmp/test_runs/screenshots/idp_portal_04_catalog_grades.png',
      fullPage: true,
    });
  });

  test('every dimension links to the evidence behind its score', async ({ page }) => {
    const resp = await page.request.get('/idp/api/component/idp');
    const detail = await resp.json();

    expect(detail.dimensions.length).toBeGreaterThan(0);
    let outcomes = 0;
    for (const dim of detail.dimensions) {
      for (const outcome of dim.outcomes) {
        expect(outcome.evidence_url).toContain('/idp/evidence?');
        expect(outcome.evidence.expression, 'evidence names no query').toBeTruthy();
        expect(outcome.evidence.adapter_module).toBeTruthy();
        outcomes += 1;
      }
    }
    expect(outcomes).toBeGreaterThan(0);
  });

  test('the evidence page re-derives one rule from source', async ({ page }) => {
    await page.goto('/idp/evidence?component=idp&rule=has-owner');
    await page.waitForLoadState('domcontentloaded');

    const bodyText = await page.textContent('body');
    expect(bodyText).toContain('The check');
    expect(bodyText).toContain('Observed facts');
    expect(bodyText).toContain('idp.components');

    // No absolute host path may leak into the rendered page.
    expect(bodyText).not.toMatch(/[A-Z]:\\/);

    await page.screenshot({
      path: '.tmp/test_runs/screenshots/idp_portal_05_evidence.png',
      fullPage: true,
    });
  });

  test('absent probe evidence is reported absent, not summarised as healthy', async ({ page }) => {
    const resp = await page.request.get('/idp/api/evidence?component=idp&rule=probes-healthy');
    expect(resp.ok()).toBeTruthy();
    const data = await resp.json();

    const probes = data.sources.find((s: any) => s.kind === 'probe_rows');
    expect(probes, 'probes-healthy must link to its probe rows').toBeTruthy();
    expect(probes.table).toBe('awareness_component_health');
    // measured must track the rows, so an empty set can never read as a pass.
    expect(probes.measured).toBe(probes.rows.length > 0);
  });

  test('the evidence endpoint rejects an unknown rule', async ({ page }) => {
    const resp = await page.request.get('/idp/api/evidence?component=idp&rule=no-such-rule');
    expect(resp.status()).toBe(404);
  });
});
// CUI // SP-CTI

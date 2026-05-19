// CUI // SP-CTI
// E2E Test: Studio Menu — all Studio subpages load and render correctly

import { test, expect } from '@playwright/test';

const SCREENSHOT_DIR = '.tmp/test_runs/screenshots';

const STUDIO_PAGES = [
  { label: 'App Builder',       path: '/studio/app-builder' },
  { label: 'Workflow Builder',  path: '/studio/workflows' },
  { label: 'Form Builder',      path: '/studio/forms' },
  { label: 'Case Management',   path: '/studio/cases' },
  { label: 'Automations',       path: '/studio/automations' },
  { label: 'Dashboard Builder', path: '/studio/dashboards' },
  { label: 'Marketplace',       path: '/studio/marketplace' },
];

function noServerError(body: string | null, path: string) {
  const b = body ?? '';
  expect(b.toLowerCase(), `${path} server error`).not.toContain('internal server error');
  expect(b, `${path} traceback`).not.toContain('Traceback');
}

test.describe('Studio Menu', () => {
  for (const pg of STUDIO_PAGES) {
    test(`${pg.label} (${pg.path}) — HTTP < 400`, async ({ request }) => {
      const resp = await request.get(pg.path);
      expect(resp.status(), `${pg.path} returned ${resp.status()}`).toBeLessThan(400);
    });

    test(`${pg.label} (${pg.path}) — renders without server error`, async ({ page }) => {
      await page.goto(pg.path);
      await page.waitForLoadState('domcontentloaded');

      await page.screenshot({
        path: `${SCREENSHOT_DIR}/studio_${pg.label.toLowerCase().replace(/[^a-z0-9]+/g, '_')}.png`,
        fullPage: false,
      });

      noServerError(await page.textContent('body'), pg.path);
    });
  }

  test('Studio pages share consistent CUI banner', async ({ page }) => {
    for (const pg of STUDIO_PAGES) {
      await page.goto(pg.path);
      await page.waitForLoadState('domcontentloaded');
      const body = (await page.textContent('body')) ?? '';
      expect(body, `${pg.path} missing CUI`).toContain('CUI');
    }
  });
});
// CUI // SP-CTI

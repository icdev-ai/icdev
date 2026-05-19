// CUI // SP-CTI
// E2E Smoke Test: GovCon sub-pages — requirements matrix and capabilities library

import { test, expect } from '@playwright/test';

const SCREENSHOT_DIR = '.tmp/test_runs/screenshots';
const CUI_BANNER = 'CUI // SP-CTI';

const GOVCON_PAGES = [
  { name: 'GovCon Hub',          path: '/govcon' },
  { name: 'Requirements Matrix', path: '/govcon/requirements' },
  { name: 'Capabilities Library', path: '/govcon/capabilities' },
];

test.describe('GovCon Sub-pages', () => {
  test.beforeEach(async ({ page }) => {
    await page.addInitScript(() => {
      localStorage.setItem('icdev_tour_completed', '1');
    });
  });

  for (const { name, path } of GOVCON_PAGES) {
    test(`${name} — HTTP < 400`, async ({ page }) => {
      const resp = await page.request.get(path);
      expect(resp.status(), `${path} returned HTTP ${resp.status()}`).toBeLessThan(400);
    });

    test(`${name} — no server error`, async ({ page }) => {
      await page.goto(path);
      await page.waitForLoadState('domcontentloaded');

      await page.screenshot({
        path: `${SCREENSHOT_DIR}/govcon_${name.toLowerCase().replace(/[^a-z0-9]+/g, '_')}.png`,
        fullPage: false,
      });

      const bodyText = (await page.textContent('body')) ?? '';
      expect(bodyText).not.toContain('Internal Server Error');
      expect(bodyText).not.toContain('Traceback');
      expect(bodyText).toContain(CUI_BANNER);
    });
  }
});
// CUI // SP-CTI

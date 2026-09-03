// CUI // SP-CTI
// E2E Test: Canvas Smoke — All 11 canvases load without errors; IQE widget present

import { test, expect } from '@playwright/test';

const SCREENSHOT_DIR = '.tmp/test_runs/screenshots';

const CANVASES = [
  { name: 'Supply Chain SCRM',  path: '/supply_chain' },
  { name: 'Digital Twin',       path: '/digital-twin' },
  { name: 'OSCAL',              path: '/oscal' },
  { name: 'FedRAMP 20x',        path: '/boundary/fedramp-20x' },
  { name: 'Strategos',          path: '/strategos' },
  { name: 'Innovation',         path: '/innovation' },
  { name: 'CPMP',               path: '/cpmp' },
  { name: 'Kanban',             path: '/kanban' },
  { name: 'Migration Canvas',   path: '/migration-canvas/network-migration/' },
  { name: 'AI Observatory',     path: '/ai-observatory' },
];

test.describe('Canvas Smoke Tests', () => {
  for (const canvas of CANVASES) {
    test(`${canvas.name} loads without server error`, async ({ page }) => {
      // HTTP-level check first
      const resp = await page.request.get(canvas.path);
      expect(
        resp.status(),
        `${canvas.path} returned HTTP ${resp.status()}`
      ).toBeLessThan(400);

      // Browser navigation check
      await page.goto(canvas.path);
      await page.waitForLoadState('domcontentloaded');

      await page.screenshot({
        path: `${SCREENSHOT_DIR}/canvas_${canvas.name.toLowerCase().replace(/[^a-z0-9]+/g, '_')}.png`,
        fullPage: false,
      });

      const bodyText = (await page.textContent('body')) ?? '';

      // Must not be a hard error page
      expect(bodyText.toLowerCase()).not.toContain('internal server error');
      expect(bodyText).not.toContain('werkzeug');
      expect(bodyText).not.toContain('Traceback');

      // CUI banner presence
      expect(bodyText).toContain('CUI');
    });

    test(`${canvas.name} has IQE widget attached`, async ({ page }) => {
      await page.goto(canvas.path);
      await page.waitForLoadState('domcontentloaded');

      const iqeWidget = page.locator(
        '.iqe-widget, #iqe-minibar, [data-api]'
      );
      const count = await iqeWidget.count();
      expect(
        count,
        `Expected IQE widget on ${canvas.path}, found 0 matching elements`
      ).toBeGreaterThan(0);
    });
  }
});
// CUI // SP-CTI

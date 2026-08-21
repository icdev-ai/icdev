// CUI // SP-CTI
// E2E Test: Platforms Menu — GeoSIGINT, Info Ops, GovCon, Training, More

import { test, expect } from '@playwright/test';

const SCREENSHOT_DIR = '.tmp/test_runs/screenshots';

const PLATFORMS_HARD = [
  { label: 'Genesis',               path: '/genesis' },
  { label: 'Oracle',                path: '/oracle' },
  { label: 'Proposals',             path: '/proposals' },
  { label: 'GovCon Intelligence',   path: '/govcon' },
];

// Feature-gated (may be disabled) — soft check (no 500)
const PLATFORMS_SOFT = [
  { label: 'GeoSIGINT Dashboard',        path: '/geosigint/' },
  { label: 'A2AD Zone Mapper',           path: '/geosigint/a2ad' },
  { label: 'Strait Crossing',            path: '/geosigint/strait-crossing' },
  { label: 'Island Chain Defense',       path: '/geosigint/island-chain' },
  { label: 'Amphibious Assessment',      path: '/geosigint/amphibious' },
  { label: 'Militia Classifier',         path: '/geosigint/militia' },
  { label: 'Semiconductor Chain',        path: '/geosigint/semiconductor' },
  { label: 'Info Ops Platform',          path: '/info-ops/' },
  { label: 'Info Ops Dashboard',         path: '/info-ops/dashboard' },
  { label: 'Info Ops Analysis',          path: '/info-ops/analysis' },
  { label: 'Info Ops OSINT',             path: '/info-ops/osint' },
  { label: 'GameDay',                    path: '/gameday' },
  { label: 'FORGE Academy',              path: '/academy' },
];

const MORE_MENU_PAGES = [
  { label: 'Profile & Settings', path: '/profile' },
  { label: 'Notifications',      path: '/notifications' },
  { label: 'Usage',              path: '/usage' },
  { label: 'Analytics',          path: '/analytics' },
  { label: 'Get Started',        path: '/wizard' },
  { label: 'Quick Paths',        path: '/quick-paths' },
];

function noServerError(body: string | null, path: string) {
  const b = body ?? '';
  expect(b.toLowerCase(), `${path} server error`).not.toContain('internal server error');
  expect(b, `${path} traceback`).not.toContain('Traceback');
}

test.describe('Platforms Menu — Core', () => {
  for (const pg of PLATFORMS_HARD) {
    test(`${pg.label} (${pg.path}) — HTTP < 400`, async ({ request }) => {
      const resp = await request.get(pg.path);
      expect(resp.status(), `${pg.path} returned ${resp.status()}`).toBeLessThan(400);
    });

    test(`${pg.label} (${pg.path}) — no server error`, async ({ page }) => {
      await page.goto(pg.path);
      await page.waitForLoadState('domcontentloaded');
      await page.screenshot({
        path: `${SCREENSHOT_DIR}/platform_${pg.label.toLowerCase().replace(/[^a-z0-9]+/g, '_')}.png`,
        fullPage: false,
      });
      noServerError(await page.textContent('body'), pg.path);
    });
  }
});

test.describe('Platforms Menu — Feature-gated (soft)', () => {
  for (const pg of PLATFORMS_SOFT) {
    test(`${pg.label} (${pg.path}) — not a 500`, async ({ request }) => {
      const resp = await request.get(pg.path);
      expect(resp.status(), `${pg.path} returned 500 (server crash)`).not.toBe(500);
    });

    test(`${pg.label} (${pg.path}) — no traceback`, async ({ page }) => {
      await page.goto(pg.path);
      await page.waitForLoadState('domcontentloaded');
      await page.screenshot({
        path: `${SCREENSHOT_DIR}/platform_soft_${pg.label.toLowerCase().replace(/[^a-z0-9]+/g, '_')}.png`,
        fullPage: false,
      });
      const body = (await page.textContent('body')) ?? '';
      expect(body, `${pg.path} traceback`).not.toContain('Traceback');
      expect(body.toLowerCase(), `${pg.path} werkzeug`).not.toContain('werkzeug debugger');
    });
  }
});

test.describe('More Menu', () => {
  for (const pg of MORE_MENU_PAGES) {
    test(`${pg.label} (${pg.path}) — HTTP < 400`, async ({ request }) => {
      const resp = await request.get(pg.path);
      expect(resp.status(), `${pg.path} returned ${resp.status()}`).toBeLessThan(400);
    });

    test(`${pg.label} (${pg.path}) — no server error`, async ({ page }) => {
      await page.goto(pg.path);
      await page.waitForLoadState('domcontentloaded');
      await page.screenshot({
        path: `${SCREENSHOT_DIR}/more_${pg.label.toLowerCase().replace(/[^a-z0-9]+/g, '_')}.png`,
        fullPage: false,
      });
      noServerError(await page.textContent('body'), pg.path);
    });
  }
});
// CUI // SP-CTI

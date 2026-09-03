// CUI // SP-CTI
// E2E Test: Key Pages Smoke — Quick-action target pages load without error

import { test, expect } from '@playwright/test';

const SCREENSHOT_DIR = '.tmp/test_runs/screenshots';

const KEY_PAGES = [
  { label: 'Kanban',              path: '/kanban' },
  { label: 'Digital Twin',        path: '/digital-twin' },
  { label: 'CPMP Portal',         path: '/cpmp' },
  { label: 'OSCAL',               path: '/oscal' },
  { label: 'WriteGuard',          path: '/writeguard' },
  { label: 'AI ML Modernize',     path: '/ai-ml/modernize' },
  { label: 'Proposals',           path: '/proposals' },
  { label: 'Supply Chain',        path: '/supply_chain' },
  { label: 'Strategos',           path: '/strategos' },
  // Network canvas is disabled in CI (ICDEV_NETWORK_ENABLED=false); /network/ask
  // returns 404 when the blueprint is not registered — omit from smoke list.
];

test.describe('Key Pages Smoke Tests', () => {
  for (const page_def of KEY_PAGES) {
    test(`${page_def.label} (${page_def.path}) returns HTTP < 400`, async ({ page }) => {
      const resp = await page.request.get(page_def.path);
      expect(
        resp.status(),
        `${page_def.path} returned ${resp.status()}`
      ).toBeLessThan(400);
    });

    test(`${page_def.label} renders without 500 error text`, async ({ page }) => {
      await page.goto(page_def.path);
      await page.waitForLoadState('domcontentloaded');

      await page.screenshot({
        path: `${SCREENSHOT_DIR}/key_pages_${page_def.label.toLowerCase().replace(/[^a-z0-9]+/g, '_')}.png`,
        fullPage: false,
      });

      const bodyText = (await page.textContent('body')) ?? '';
      expect(bodyText.toLowerCase()).not.toContain('internal server error');
      expect(bodyText).not.toContain('Traceback');
      expect(bodyText).not.toContain('werkzeug');
    });
  }

  test('prod-audit (Production Audit) page loads at its governed home', async ({ page }) => {
    // rmf-ui-14: the page lives on the Security canvas; the old URL is a 301 to it.
    const resp = await page.request.get('/security/prod-audit');
    expect(resp.status()).toBeLessThan(400);
  });

  test('old /prod-audit URL redirects to the governed home', async ({ page }) => {
    const resp = await page.request.get('/prod-audit', { maxRedirects: 0 });
    expect(resp.status()).toBe(301);
    expect(resp.headers()['location']).toContain('/security/prod-audit');
  });

  test('ai-transparency (AI Transparency) page loads at its governed home', async ({ page }) => {
    // rmf-ui-15: the page lives on the Security canvas; the old URL is a 301 to it.
    const resp = await page.request.get('/security/ai-transparency');
    expect(resp.status()).toBeLessThan(400);
  });

  test('old /ai-transparency URL redirects to the governed home', async ({ page }) => {
    const resp = await page.request.get('/ai-transparency', { maxRedirects: 0 });
    expect(resp.status()).toBe(301);
    expect(resp.headers()['location']).toContain('/security/ai-transparency');
  });

  test('ai-accountability (AI Accountability) page loads at its governed home', async ({ page }) => {
    // rmf-ui-16: the page lives on the Security canvas; the old URL is a 301 to it.
    const resp = await page.request.get('/security/ai-accountability');
    expect(resp.status()).toBeLessThan(400);
  });

  test('old /ai-accountability URL redirects to the governed home', async ({ page }) => {
    const resp = await page.request.get('/ai-accountability', { maxRedirects: 0 });
    expect(resp.status()).toBe(301);
    expect(resp.headers()['location']).toContain('/security/ai-accountability');
  });

  test('compliance page loads', async ({ page }) => {
    const resp = await page.request.get('/compliance');
    expect(resp.status()).toBeLessThan(400);
  });

  test('knowledge-search page loads', async ({ page }) => {
    const resp = await page.request.get('/knowledge-search');
    expect(resp.status()).toBeLessThan(400);
  });

  test('research page loads', async ({ page }) => {
    const resp = await page.request.get('/research');
    expect(resp.status()).toBeLessThan(400);
  });

  test('monitoring page loads', async ({ page }) => {
    const resp = await page.request.get('/monitoring');
    expect(resp.status()).toBeLessThan(400);
  });
});
// CUI // SP-CTI

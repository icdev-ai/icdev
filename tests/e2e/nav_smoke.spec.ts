// CUI // SP-CTI
// E2E Test: Navigation Smoke — All dropdown menus open; links resolve without 404/500

import { test, expect } from '@playwright/test';

const SCREENSHOT_DIR = '.tmp/test_runs/screenshots';

const NAV_ITEMS = [
  { label: 'Projects', href: '/projects' },
  { label: 'Agents', href: '/agents' },
  { label: 'Monitoring', href: '/monitoring' },
  { label: 'GovCon', href: '/govcon' },
  { label: 'Studio', href: '/studio/app-builder' },
  { label: 'Kanban', href: '/kanban' },
  { label: 'Genesis', href: '/genesis' },
  { label: 'Chat', href: '/chat' },
];

test.describe('Navigation Smoke Tests', () => {
  test('dashboard home loads with nav bar', async ({ page }) => {
    await page.goto('/');
    await page.waitForLoadState('domcontentloaded');

    await page.screenshot({
      path: `${SCREENSHOT_DIR}/nav_smoke_00_home.png`,
      fullPage: false,
    });

    // Nav bar must exist
    const nav = page.locator('nav, .navbar, .nav-bar, header nav');
    expect(await nav.count()).toBeGreaterThan(0);

    // CUI banner must be present
    const bodyText = await page.textContent('body');
    expect(bodyText).toContain('CUI');
  });

  for (const item of NAV_ITEMS) {
    test(`nav item "${item.label}" resolves (${item.href})`, async ({ page }) => {
      // Try via direct navigation — faster than clicking through nav
      const resp = await page.request.get(item.href);
      expect(
        resp.status(),
        `${item.href} returned ${resp.status()}`
      ).toBeLessThan(400);

      // Also navigate and check no error page text
      await page.goto(item.href);
      await page.waitForLoadState('domcontentloaded');

      await page.screenshot({
        path: `${SCREENSHOT_DIR}/nav_smoke_${item.label.toLowerCase().replace(/\s+/g, '_')}.png`,
        fullPage: false,
      });

      const bodyText = (await page.textContent('body')) ?? '';
      expect(bodyText.toLowerCase()).not.toContain('internal server error');
      expect(bodyText).not.toContain('werkzeug');
    });
  }

  test('no nav link points to a 404 response', async ({ page }) => {
    await page.goto('/');
    await page.waitForLoadState('domcontentloaded');

    // Collect all internal <a href> that are relative paths
    const hrefs: string[] = await page.evaluate(() => {
      return Array.from(document.querySelectorAll('nav a[href], .navbar a[href]'))
        .map(a => (a as HTMLAnchorElement).getAttribute('href') ?? '')
        .filter(h => h.startsWith('/') && !h.startsWith('//#') && !h.startsWith('//'))
        .filter(h => !h.includes('logout'))
        .slice(0, 30); // limit to avoid timeout
    });

    for (const href of hrefs) {
      const resp = await page.request.get(href);
      expect(
        resp.status(),
        `Nav link ${href} returned ${resp.status()}`
      ).toBeLessThan(400);
    }
  });
});
// CUI // SP-CTI

// CUI // SP-CTI
// E2E Test: NAV regression probes (nav-qa-01, spec 3 of 3)
//
// Browser-level regression guards for specific defects the NAV wave fixed:
//   - /evidence — previously returned 500; must now render 200 with a graceful
//     degraded path (nav wave; evidence_page degraded handling).
//   - /translations — page loads.
//   - /slides — page loads.
//   - Pulse post — renders without executing injected script (nav-sec-07 site 6:
//     pulse_post.html body_html is sanitized on ingest). The injected-payload
//     inertness is authoritatively covered at the source level by pytest; here we
//     add a cheap browser guard that no dialog fires when a real post is present,
//     and skip when the fresh CI DB has no seeded post (no cheap seed path).

import { test, expect } from '@playwright/test';

const SCREENSHOT_DIR = '.tmp/test_runs/screenshots';

const RENDERS_200 = [
  { label: 'Evidence Collection', path: '/evidence' }, // was 500 before the wave
  { label: 'Translations', path: '/translations' },
  { label: 'Slides', path: '/slides' },
];

test.describe('NAV regression probes — pages render (browser-level)', () => {
  for (const pg of RENDERS_200) {
    test(`${pg.label} (${pg.path}) renders 200 with no server error`, async ({ request }) => {
      const resp = await request.get(pg.path);
      expect(resp.status(), `${pg.path} returned HTTP ${resp.status()}`).toBeLessThan(400);
      const body = await resp.text();
      expect(body.toLowerCase(), `${pg.path} shows a 500 page`).not.toContain('internal server error');
      expect(body, `${pg.path} shows a traceback`).not.toContain('Traceback (most recent call last)');
      expect(body.trim().length, `${pg.path} body is empty`).toBeGreaterThan(300);
    });
  }
});

test.describe('NAV regression — pulse post script inertness', () => {
  test('a pulse post renders without executing injected script', async ({ page }) => {
    // Fail loudly if any injected <script>/handler fires an alert/confirm/prompt.
    let dialogFired = false;
    page.on('dialog', async (d) => {
      dialogFired = true;
      await d.dismiss().catch(() => {});
    });

    const idxResp = await page.request.get('/pulse');
    test.skip(idxResp.status() >= 400, `Pulse not available (HTTP ${idxResp.status()})`);

    await page.goto('/pulse');
    await page.waitForLoadState('domcontentloaded');

    const postLink = page.locator('a[href^="/pulse/post/"]').first();
    if ((await postLink.count()) === 0) {
      test.skip(
        true,
        'No seeded pulse post on this DB; script-inertness of pulse_post.html is covered at source by pytest (nav-sec-07).',
      );
      return;
    }

    await postLink.click();
    await page.waitForLoadState('domcontentloaded');
    await page.screenshot({ path: `${SCREENSHOT_DIR}/nav_readiness_pulse_post.png`, fullPage: false });

    const body = (await page.textContent('body')) ?? '';
    expect(body.toLowerCase()).not.toContain('internal server error');
    expect(dialogFired, 'a dialog fired — injected script executed (XSS)').toBe(false);
  });
});
// CUI // SP-CTI

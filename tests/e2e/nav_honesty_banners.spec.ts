// CUI // SP-CTI
// E2E Test: NAV honesty banners & degraded states (nav-qa-01, spec 2 of 3)
//
// Locks in the "no fabricated data" outcomes of the NAV wave:
//   - nav-plat-03: GeoSIGINT static-reference pages carry a persistent
//     "Reference data (static)" provenance badge (apps/geosigint/templates/
//     base.html renders it when a page sets static_reference=true).
//   - nav-strat-03: /strategos/oracle never shows a numeric composite when the
//     SIO assessment engine is unavailable — the API returns
//     {available:false} (HTTP 503) with NO composite_score, instead of the old
//     hardcoded 5.25 mock. The page still loads.
//   - Updates: the Updates page loads and renders.
//
// Feature-gated surfaces are probed and skipped (not failed) when the owning
// feature is disabled in this environment.

import { test, expect } from '@playwright/test';

const SCREENSHOT_DIR = '.tmp/test_runs/screenshots';

// GeoSIGINT pages that set static_reference=true (see apps/geosigint/templates/*).
const GEOSIGINT_STATIC_PAGES = [
  '/geosigint/',
  '/geosigint/a2ad',
  '/geosigint/amphibious',
  '/geosigint/strait-crossing',
  '/geosigint/island-chain',
  '/geosigint/militia',
  '/geosigint/semiconductor',
];

const STATIC_BADGE_TEXT = 'Reference data (static)';

test.describe('Honesty banner — GeoSIGINT static-data provenance', () => {
  test('GeoSIGINT static-reference pages load and render', async ({ request }) => {
    // Probe the index first; if GeoSIGINT is not mounted here, skip gracefully.
    const probe = await request.get(GEOSIGINT_STATIC_PAGES[0]);
    test.skip(probe.status() >= 400, `GeoSIGINT not available (HTTP ${probe.status()})`);

    for (const path of GEOSIGINT_STATIC_PAGES) {
      const resp = await request.get(path);
      expect.soft(resp.status(), `${path} returned HTTP ${resp.status()}`).toBeLessThan(400);
      if (resp.status() < 400) {
        const body = await resp.text();
        expect.soft(body.toLowerCase(), `${path} server error`).not.toContain('internal server error');
        expect.soft(body.trim().length, `${path} empty body`).toBeGreaterThan(300);
      }
    }
  });

  // FIXED (nav-plat-06). The badge used to never render: the geosigint
  // templates' `{% extends "base.html" %}` resolves to the MAIN dashboard
  // tools/dashboard/templates/base.html (the app template folder wins over the
  // blueprint folder in the Jinja loader search path), shadowing the
  // geosigint-local base.html that carried the badge. Fix: the badge markup was
  // moved into a shared partial (apps/geosigint/templates/
  // _geosigint_reference_badge.html) that the 7 static templates include at the
  // top of their content block, so it renders regardless of which base.html
  // wins — dashboard-mounted or standalone. Gating stays on static_reference,
  // so DB-backed pages never show it.
  test('GeoSIGINT pages render the "Reference data (static)" provenance badge', async ({ request }) => {
    const probe = await request.get(GEOSIGINT_STATIC_PAGES[0]);
    test.skip(probe.status() >= 400, `GeoSIGINT not available (HTTP ${probe.status()})`);

    for (const path of GEOSIGINT_STATIC_PAGES) {
      const resp = await request.get(path);
      if (resp.status() < 400) {
        const body = await resp.text();
        expect(body, `${path} missing static-reference provenance badge`).toContain(STATIC_BADGE_TEXT);
        expect(body, `${path} badge missing "Not a live feed" qualifier`).toContain('Not a live feed');
      }
    }
  });
});

test.describe('Honesty banner — Oracle degraded state (nav-strat-03)', () => {
  test('/strategos/oracle page loads', async ({ request, page }) => {
    const resp = await request.get('/strategos/oracle');
    test.skip(resp.status() >= 400, `Strategos/Oracle not available (HTTP ${resp.status()})`);

    await page.goto('/strategos/oracle');
    await page.waitForLoadState('domcontentloaded');
    await page.screenshot({ path: `${SCREENSHOT_DIR}/nav_readiness_oracle.png`, fullPage: false });

    const body = (await page.textContent('body')) ?? '';
    expect(body.toLowerCase()).not.toContain('internal server error');
    expect(body).not.toContain('Traceback (most recent call last)');
  });

  test('oracle assessment API never emits a numeric composite when unavailable', async ({ request }) => {
    const resp = await request.get('/api/strategos/oracle');
    // Route must exist (200 healthy or 503 degraded); a hard 404/500 is a wiring bug.
    test.skip(resp.status() === 404, 'Oracle assessment API not mounted');
    expect([200, 503], `unexpected status ${resp.status()}`).toContain(resp.status());

    const data = await resp.json();
    // The honesty invariant (nav-strat-03): a numeric composite may ONLY appear
    // when a real assessment is available. Key on the presence of a numeric
    // composite rather than the exact healthy-response shape, so the check is
    // robust regardless of which optional metadata the healthy path emits.
    const hasNumericComposite = typeof data.composite_score === 'number';
    if (resp.status() === 503 || data.available === false || !hasNumericComposite) {
      // Degraded/unavailable: MUST NOT expose fabricated lens/composite numbers.
      expect(data.available, 'degraded response must set available:false').toBe(false);
      expect(data.composite_score, 'degraded oracle must NOT expose a composite_score').toBeUndefined();
      expect(data.reason, 'degraded oracle should explain why').toBeTruthy();
    } else {
      // Healthy: a real assessment carries a numeric composite.
      expect(hasNumericComposite, 'healthy oracle must report a numeric composite').toBe(true);
    }
  });
});

test.describe('Updates page', () => {
  test('/updates loads and renders', async ({ request }) => {
    const resp = await request.get('/updates');
    expect(resp.status(), `/updates returned HTTP ${resp.status()}`).toBeLessThan(400);
    const body = await resp.text();
    expect(body.toLowerCase()).not.toContain('internal server error');
    expect(body.trim().length).toBeGreaterThan(300);
  });
});
// CUI // SP-CTI

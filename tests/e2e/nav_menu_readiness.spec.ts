// CUI // SP-CTI
// E2E Test: NAV menu-readiness end-of-wave sweep (nav-qa-01)
//
// Locks in the outcome of the NAV menu-readiness wave (49 PRs, #562-#611) that
// hardened the 8 top-level dashboard menus. This file is the *nav-link sweep*
// (spec 1 of 3):
//
//   1. nav_menu_readiness.spec.ts  — every rendered link in the 8 menus returns
//      < 400 and renders a non-empty template with no raw exception text.
//   2. nav_honesty_banners.spec.ts — GeoSIGINT static-data badge, Oracle degraded
//      panel (no fabricated composite), Updates page.
//   3. nav_regression_probes.spec.ts — /evidence (was 500), /translations, /slides,
//      pulse-post script inertness.
//
// DESIGN — links are extracted from the *rendered* nav DOM, not hardcoded. The
// server applies feature-gating (strategos_enabled, govcon_enabled, gameday/
// academy, airgap-hidden items) before rendering, so a gated-OFF link simply
// never appears in the DOM and is transparently excluded — this is how the task's
// "skip links gated behind disabled features gracefully" requirement is met
// without brittle skip lists. A link that IS in the nav but 404s is a real
// wiring defect and correctly fails. The enabled set in CI comes from the E2E
// job env in .github/workflows/icdev-ci.yml (govcon on; gameday/academy off;
// airgap off; strategos default-on).
//
// RBAC deny-cases are intentionally NOT duplicated here: ~200 pytest deny tests
// already cover them, and the E2E server runs with ICDEV_DASHBOARD_DEV_AUTOLOGIN
// (admin) so a browser session cannot exercise the deny path anyway.
//
// Reliability: no arbitrary sleeps. Status/render is checked with a single
// APIRequestContext GET per link (dev-autologin authenticates request context
// too), keeping the whole sweep well under the 5-minute budget. Soft assertions
// collect every failing link per menu instead of aborting on the first.

import { test, expect, type APIRequestContext } from '@playwright/test';

const SCREENSHOT_DIR = '.tmp/test_runs/screenshots';

// The 8 top-level menus this wave hardened. Updates is a plain top-level link;
// the rest are dropdowns. Strategos is default-on but treated as skippable in
// case a deployment disables it.
const DROPDOWN_MENUS = ['Build', 'Intelligence', 'Compliance', 'Strategos', 'Platforms', 'Studio', 'More'];
const SKIPPABLE_MENUS = new Set(['Strategos']); // gated by ICDEV_STRATEGOS_ENABLED

type MenuMap = Record<string, string[]>;

// Populated once from the rendered home nav; shared across the per-menu tests.
let MENUS: MenuMap = {};

async function extractMenus(page: import('@playwright/test').Page): Promise<MenuMap> {
  return page.evaluate(() => {
    const out: Record<string, string[]> = {};
    const clean = (hrefs: string[]) =>
      Array.from(
        new Set(
          hrefs
            .filter((h) => h.startsWith('/') && !h.startsWith('//'))
            .filter((h) => !/logout/i.test(h)),
        ),
      );

    // Dropdown menus: trigger text (minus the ▾) is the menu label.
    document.querySelectorAll('nav .nav-dropdown').forEach((dd) => {
      const trig = dd.querySelector('.nav-dropdown-trigger');
      if (!trig) return;
      const label = (trig.textContent || '').replace(/[▾\s]+$/, '').trim();
      if (!label) return;
      const links = Array.from(dd.querySelectorAll('.nav-dropdown-menu a[href]')).map(
        (a) => (a as HTMLAnchorElement).getAttribute('href') || '',
      );
      out[label] = clean(links);
    });

    // Updates is a plain (non-dropdown) top-level link.
    const updates = Array.from(document.querySelectorAll('nav .navbar-nav > li > a[href]')).find((a) =>
      (a.textContent || '').trim().startsWith('Updates'),
    ) as HTMLAnchorElement | undefined;
    if (updates) out['Updates'] = clean([updates.getAttribute('href') || '']);

    return out;
  });
}

function bodyIsErrorFree(body: string, href: string) {
  const lower = body.toLowerCase();
  expect.soft(lower, `${href} — Flask/werkzeug traceback`).not.toContain('traceback (most recent call last)');
  expect.soft(lower, `${href} — 500 error page`).not.toContain('internal server error');
  expect.soft(lower, `${href} — werkzeug debugger`).not.toContain('werkzeug');
  // Non-empty rendered template (a bare error/redirect stub would be tiny).
  expect.soft(body.trim().length, `${href} — empty/near-empty response body`).toBeGreaterThan(300);
}

async function assertLinkRenders(request: APIRequestContext, href: string) {
  const resp = await request.get(href);
  const status = resp.status();

  // 401/403 are access-gating decisions, not broken templates. RBAC deny-cases
  // are out of scope for this spec (covered by ~200 pytest deny tests; the E2E
  // server runs admin auto-login so these normally resolve to 200). Tolerate
  // them so the render sweep still fails loudly on real defects — 404 (broken
  // nav wiring) and 5xx (server error) — without duplicating RBAC coverage.
  if (status === 401 || status === 403) {
    return;
  }

  expect.soft(status, `${href} returned HTTP ${status}`).toBeLessThan(400);
  if (status < 400) {
    bodyIsErrorFree(await resp.text(), href);
  }
}

test.beforeAll(async ({ browser }) => {
  const page = await browser.newPage();
  await page.goto('/');
  await page.waitForLoadState('domcontentloaded');
  MENUS = await extractMenus(page);
  await page.close();
});

test.describe('NAV menu-readiness — nav-link sweep', () => {
  test('home renders with nav bar and CUI banner', async ({ page }) => {
    await page.goto('/');
    await page.waitForLoadState('domcontentloaded');
    await page.screenshot({ path: `${SCREENSHOT_DIR}/nav_readiness_home.png`, fullPage: false });

    const nav = page.locator('nav, .navbar');
    expect(await nav.count()).toBeGreaterThan(0);
    const bodyText = (await page.textContent('body')) ?? '';
    expect(bodyText).toContain('CUI');
  });

  test('all 8 top-level menus are present in the rendered nav', async () => {
    // Strategos may be gated off; assert only if it rendered.
    for (const label of DROPDOWN_MENUS) {
      if (SKIPPABLE_MENUS.has(label)) continue;
      expect(MENUS[label], `menu "${label}" missing from nav (keys: ${Object.keys(MENUS).join(', ')})`).toBeDefined();
    }
    expect(MENUS['Updates'], 'Updates top-level link missing from nav').toBeDefined();
  });

  for (const label of [...DROPDOWN_MENUS, 'Updates']) {
    test(`menu "${label}" — every rendered link renders (HTTP < 400, non-empty, no exception)`, async ({ request }) => {
      const links = MENUS[label];

      if (!links || links.length === 0) {
        if (SKIPPABLE_MENUS.has(label)) {
          test.skip(true, `Menu "${label}" not rendered (feature-gated off in this environment)`);
        }
        expect(links, `Menu "${label}" resolved to no links — extraction or gating error`).toBeTruthy();
        return;
      }

      // Sequential GETs keep it deterministic (config runs single-worker anyway);
      // soft assertions report every failing link in the menu at once.
      for (const href of links) {
        await assertLinkRenders(request, href);
      }
    });
  }
});
// CUI // SP-CTI

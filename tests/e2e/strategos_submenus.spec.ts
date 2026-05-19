// CUI // SP-CTI
// E2E Test: Strategos Menu — all 33 submenu pages load without server errors

import { test, expect } from '@playwright/test';

const SCREENSHOT_DIR = '.tmp/test_runs/screenshots';

const STRATEGOS_PAGES = [
  // Command
  { label: 'Overview',              path: '/strategos' },
  { label: 'Commander Dashboard',   path: '/strategos/commander' },
  // Intel
  { label: 'Predictive Intel Brief',path: '/strategos/intel-brief' },
  { label: 'Leadership Brief',      path: '/strategos/leadership-brief' },
  { label: 'Oracle Assessment',     path: '/strategos/oracle' },
  { label: 'PIR CCIR EEI',          path: '/strategos/pir' },
  { label: 'INTSUM Generator',      path: '/strategos/intsum' },
  { label: 'I&W Indicators',        path: '/strategos/indicators' },
  { label: 'Intel Briefs',          path: '/strategos/briefs' },
  { label: 'Knowledge Graph',       path: '/strategos/kg' },
  { label: 'Ghost Signals',         path: '/strategos/ghost' },
  // Ops
  { label: 'ORBAT Tracker',         path: '/strategos/orbat' },
  { label: 'Wargame',               path: '/strategos/wargame' },
  { label: 'IW Effects',            path: '/strategos/iw' },
  { label: 'F3EAD Targeting',       path: '/strategos/f3ead' },
  { label: 'BDA',                   path: '/strategos/bda' },
  { label: 'Red Cell',              path: '/strategos/red-cell' },
  { label: 'OPORD Generator',       path: '/strategos/opord' },
  { label: 'METT-TC',               path: '/strategos/mett-tc' },
  { label: 'Sync Matrix',           path: '/strategos/sync-matrix' },
  { label: 'ISR Planner',           path: '/strategos/isr-planner' },
  { label: 'IPB Canvas',            path: '/strategos/ipb' },
  // Domain
  { label: 'Source Registry',       path: '/strategos/sources' },
  { label: 'HITL Queue',            path: '/strategos/hitl' },
];

function noServerError(body: string | null, path: string) {
  const b = body ?? '';
  expect(b.toLowerCase(), `${path} server error`).not.toContain('internal server error');
  expect(b, `${path} traceback`).not.toContain('Traceback');
}

test.describe('Strategos Menu', () => {
  for (const pg of STRATEGOS_PAGES) {
    test(`${pg.label} (${pg.path}) — HTTP < 400`, async ({ request }) => {
      const resp = await request.get(pg.path);
      expect(resp.status(), `${pg.path} returned ${resp.status()}`).toBeLessThan(400);
    });

    test(`${pg.label} (${pg.path}) — renders without server error`, async ({ page }) => {
      await page.goto(pg.path);
      await page.waitForLoadState('domcontentloaded');

      await page.screenshot({
        path: `${SCREENSHOT_DIR}/strategos_${pg.label.toLowerCase().replace(/[^a-z0-9]+/g, '_')}.png`,
        fullPage: false,
      });

      noServerError(await page.textContent('body'), pg.path);

      // Strategos pages must carry CUI classification
      const bodyText = (await page.textContent('body')) ?? '';
      expect(bodyText, `${pg.path} missing CUI banner`).toContain('CUI');
    });
  }
});
// CUI // SP-CTI

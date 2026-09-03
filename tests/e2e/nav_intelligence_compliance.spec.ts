// CUI // SP-CTI
// E2E Test: Intelligence & Compliance Menus — all submenu pages load

import { test, expect } from '@playwright/test';

const SCREENSHOT_DIR = '.tmp/test_runs/screenshots';

const INTELLIGENCE_PAGES = [
  { label: 'System Graph',     path: '/system-graph' },
  { label: 'Component Map',    path: '/components-map' },
  { label: 'Ask ICDEV',        path: '/ask-icdev' },
  { label: 'AI Observatory',   path: '/ai-observatory' },
  { label: 'Research',         path: '/research' },
  { label: 'Autoresearch',     path: '/autoresearch' },
  { label: 'Knowledge Search', path: '/knowledge-search' },
  { label: 'Code Quality',     path: '/code-quality' },
  { label: 'WriteGuard',       path: '/writeguard' },
  { label: 'Pulse Blog',       path: '/pulse' },
  { label: 'SkillHub',   path: '/skillhub' },
];

const COMPLIANCE_PAGES = [
  // Risk & ATO
  // rmf-ui-07: migrated onto the Boundary canvas. The old /poam answers with a 301 to this path.
  { label: 'POA&M Findings',       path: '/boundary/poam' },
  { label: 'Compliance Hub',       path: '/compliance' },
  // rmf-ui-04: migrated onto the Boundary canvas (registry-registered, RBAC-guarded,
  // IQE-dispatchable). The old /oscal answers with a 301 to this path.
  { label: 'OSCAL',                path: '/boundary/oscal' },
  // rmf-ui-05: migrated onto the Boundary canvas beside rmf-ui-01. The old
  // /cato answers with a 301 to this path.
  { label: 'Continuous ATO',       path: '/boundary/cato-health' },
  { label: 'ATO Package Builder',  path: '/ato-package' },
  // Linked by rmf-inert-01 — both rendered a real template over a real API and
  // were reachable only by typing the URL, so nothing had ever loaded them.
  // rmf-ui-01: migrated onto the Boundary canvas (registry-registered, RBAC-guarded,
  // IQE-dispatchable). The old /ato-compliance answers with a 301 to this path.
  { label: 'ATO Compliance Dashboard', path: '/boundary/ato-compliance' },
  // rmf-ui-06: migrated onto the Boundary canvas like /ato-compliance above.
  // The old /fedramp-20x answers with a 301 to this path.
  { label: 'FedRAMP 20x KSIs',     path: '/boundary/fedramp-20x' },
  { label: 'MOSA',                 path: '/mosa' },
  // Security
  { label: 'Security Canvas',      path: '/security/' },
  { label: 'Security Scans',       path: '/security-scan' },
  // rmf-ui-12: migrated onto the Security canvas (registry-registered, RBAC-guarded,
  // IQE-dispatchable). The old /stig-manager answers with a 301 to this path.
  { label: 'STIG Manager',         path: '/security/stig-manager' },
  { label: 'PR Intelligence',      path: '/pr-intel' },
  { label: 'SRE Operations',       path: '/sre' },
  // AI & Policy
  // rmf-ui-13: migrated onto the Security canvas (registry-registered, RBAC-guarded,
  // IQE-dispatchable). The old /sbd answers with a 301 to this path.
  { label: 'Secure by Design',     path: '/security/sbd' },
  // rmf-ui-15: migrated onto the Security canvas (registry-registered, RBAC-guarded,
  // IQE-dispatchable). The old /ai-transparency answers with a 301 to this path.
  { label: 'AI Transparency',      path: '/security/ai-transparency' },
  { label: 'AI Accountability',    path: '/ai-accountability' },
  { label: 'Control Inheritance',  path: '/control-inheritance' },
  // rmf-ui-09: migrated onto the Boundary canvas (registry-registered, RBAC-guarded,
  // IQE-dispatchable). The old /compliance-debt answers with a 301 to this path.
  { label: 'Compliance Debt',      path: '/boundary/compliance-debt' },
  // rmf-ui-14: migrated onto the Security canvas (registry-registered, RBAC-guarded,
  // IQE-dispatchable). The old /prod-audit answers with a 301 to this path.
  { label: 'Production Audit',     path: '/security/prod-audit' },
  // Observability
  { label: 'Traces',               path: '/traces' },
  { label: 'Provenance',           path: '/provenance' },
  { label: 'XAI',                  path: '/xai' },
];

function noServerError(body: string | null, path: string) {
  const b = body ?? '';
  expect(b.toLowerCase(), `${path} server error`).not.toContain('internal server error');
  expect(b, `${path} traceback`).not.toContain('Traceback');
}

test.describe('Intelligence Menu', () => {
  for (const pg of INTELLIGENCE_PAGES) {
    test(`${pg.label} (${pg.path}) — HTTP < 400`, async ({ request }) => {
      const resp = await request.get(pg.path);
      expect(resp.status(), `${pg.path} returned ${resp.status()}`).toBeLessThan(400);
    });

    test(`${pg.label} (${pg.path}) — no server error`, async ({ page }) => {
      await page.goto(pg.path);
      await page.waitForLoadState('domcontentloaded');
      await page.screenshot({
        path: `${SCREENSHOT_DIR}/intel_${pg.label.toLowerCase().replace(/[^a-z0-9]+/g, '_')}.png`,
        fullPage: false,
      });
      noServerError(await page.textContent('body'), pg.path);
    });
  }
});

test.describe('Compliance Menu', () => {
  for (const pg of COMPLIANCE_PAGES) {
    test(`${pg.label} (${pg.path}) — HTTP < 400`, async ({ request }) => {
      const resp = await request.get(pg.path);
      expect(resp.status(), `${pg.path} returned ${resp.status()}`).toBeLessThan(400);
    });

    test(`${pg.label} (${pg.path}) — no server error`, async ({ page }) => {
      await page.goto(pg.path);
      await page.waitForLoadState('domcontentloaded');
      await page.screenshot({
        path: `${SCREENSHOT_DIR}/compliance_${pg.label.toLowerCase().replace(/[^a-z0-9]+/g, '_')}.png`,
        fullPage: false,
      });
      noServerError(await page.textContent('body'), pg.path);
    });
  }
});
// CUI // SP-CTI

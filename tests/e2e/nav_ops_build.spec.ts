// CUI // SP-CTI
// E2E Test: Ops & Build Menus — all submenu pages load without server errors

import { test, expect } from '@playwright/test';

const SCREENSHOT_DIR = '.tmp/test_runs/screenshots';

const OPS_PAGES = [
  { label: 'Projects',         path: '/projects' },
  { label: 'Agents',           path: '/agents' },
  { label: 'Task Board',       path: '/kanban' },
  { label: 'CI/CD',            path: '/cicd' },
  { label: 'Orchestration',    path: '/orchestration' },
  { label: 'Monitoring',       path: '/monitoring' },
  { label: 'Platform Health',  path: '/platform-health' },
  { label: 'Activity',         path: '/activity' },
  { label: 'Batch',            path: '/batch' },
  { label: 'Simulate Chat',    path: '/simulate/chat' },
];

const BUILD_PAGES = [
  { label: 'Chat',              path: '/chat' },
  { label: 'AI Strategy Wizard',path: '/ai-wizard' },
  { label: 'Dev Profiles',      path: '/dev-profiles' },
  { label: 'Phases',            path: '/phases' },
  { label: 'Fine-Tuning',       path: '/finetune' },
  { label: 'Diagrams',          path: '/diagrams' },
  { label: 'Connector Forge',   path: '/connector-forge' },
  { label: 'Translations',      path: '/translations' },
  { label: 'AI Pattern Library',path: '/ai-patterns' },
];

function noServerError(body: string | null, path: string) {
  const b = body ?? '';
  expect(b.toLowerCase(), `${path} contains server error`).not.toContain('internal server error');
  expect(b, `${path} contains traceback`).not.toContain('Traceback');
}

test.describe('Ops Menu', () => {
  for (const pg of OPS_PAGES) {
    test(`${pg.label} (${pg.path}) — HTTP < 400`, async ({ request }) => {
      const resp = await request.get(pg.path);
      expect(resp.status(), `${pg.path} returned ${resp.status()}`).toBeLessThan(400);
    });

    test(`${pg.label} (${pg.path}) — no server error`, async ({ page }) => {
      await page.goto(pg.path);
      await page.waitForLoadState('domcontentloaded');
      await page.screenshot({
        path: `${SCREENSHOT_DIR}/ops_${pg.label.toLowerCase().replace(/[^a-z0-9]+/g, '_')}.png`,
        fullPage: false,
      });
      noServerError(await page.textContent('body'), pg.path);
    });
  }
});

test.describe('Build Menu', () => {
  for (const pg of BUILD_PAGES) {
    test(`${pg.label} (${pg.path}) — HTTP < 400`, async ({ request }) => {
      const resp = await request.get(pg.path);
      expect(resp.status(), `${pg.path} returned ${resp.status()}`).toBeLessThan(400);
    });

    test(`${pg.label} (${pg.path}) — no server error`, async ({ page }) => {
      await page.goto(pg.path);
      await page.waitForLoadState('domcontentloaded');
      await page.screenshot({
        path: `${SCREENSHOT_DIR}/build_${pg.label.toLowerCase().replace(/[^a-z0-9]+/g, '_')}.png`,
        fullPage: false,
      });
      noServerError(await page.textContent('body'), pg.path);
    });
  }
});
// CUI // SP-CTI

// CUI // SP-CTI
// E2E Test: Canvases Menu — all feature-gated and unconditional canvas subpages
// Feature-gated pages may return 404 if disabled in .env — those are marked as soft checks.

import { test, expect } from '@playwright/test';

const SCREENSHOT_DIR = '.tmp/test_runs/screenshots';

// Hard pages: always expected to exist
const HARD_CANVAS_PAGES = [
  { label: 'Supply Chain SCRM',          path: '/supply_chain' },
  { label: 'Network Migration',          path: '/migration-canvas/network-migration/' },
  { label: 'Canvas Compliance Posture',  path: '/canvas-compliance' },
  { label: 'Migration Intelligence',     path: '/migration-intel/' },
  { label: 'AI Observatory',             path: '/ai-observatory' },
];

// Soft pages: feature-gated — pass even if 404 (disabled canvas), fail on 500
const SOFT_CANVAS_PAGES = [
  // Network canvas
  { label: 'Network Dashboard',          path: '/network/' },
  { label: 'Network New Topology',       path: '/network/canvas/new' },
  { label: 'Network Templates',          path: '/network/templates' },
  // DevOps canvas
  { label: 'DevOps Dashboard',           path: '/devops/' },
  { label: 'DevOps New Pipeline',        path: '/devops/canvas/new' },
  // Infrastructure canvas
  { label: 'Infra Dashboard',            path: '/infra/' },
  { label: 'Infra New Design',           path: '/infra/canvas/new' },
  { label: 'Infra Templates',            path: '/infra/templates' },
  // Data canvas
  { label: 'Data Dashboard',             path: '/data/' },
  { label: 'Data New Design',            path: '/data/canvas/new' },
  { label: 'Data Templates',             path: '/data/templates' },
  // Boundary canvas
  { label: 'Boundary Dashboard',         path: '/boundary/' },
  { label: 'Boundary New Design',        path: '/boundary/canvas/new' },
  { label: 'ISA Tracker',               path: '/boundary/isa-tracker' },
  // Migration canvas (additional)
  { label: 'Migration Design Canvas',    path: '/migration-canvas/' },
  { label: 'Server Migration',           path: '/migration-canvas/server-migration/' },
  { label: 'Cloud App Projects',         path: '/migration-canvas/projects' },
  // Observability canvas
  { label: 'Observability Dashboard',    path: '/observability/' },
  { label: 'Observability New Design',   path: '/observability/canvas/new' },
  // QA/QC canvas
  { label: 'Quality Dashboard',          path: '/quality/' },
  { label: 'Quality New Design',         path: '/quality/canvas/new' },
  { label: 'Quality Runbooks',           path: '/quality/runbooks' },
  // Agentic AI canvas
  { label: 'Agentic AI Dashboard',       path: '/agentic-ai/' },
  { label: 'Agentic AI New Design',      path: '/agentic-ai/canvas/new' },
  { label: 'Agentic AI Impact Graph',    path: '/agentic-ai/impact-graph' },
  // AI/ML canvas
  { label: 'AI ML Dashboard',            path: '/ai-ml/' },
  { label: 'AI ML New Design',           path: '/ai-ml/canvas/new' },
  { label: 'AI ML Model Catalog',        path: '/ai-ml/model-catalog' },
  // Ops Hub canvas
  { label: 'Ops Hub',                    path: '/ops' },
  { label: 'LLMOps',                     path: '/ops/llm' },
  { label: 'MLOps',                      path: '/ops/models' },
  { label: 'SLOs',                       path: '/ops/slos' },
  { label: 'Incidents',                  path: '/ops/incidents' },
  // GovLift canvas
  { label: 'GovLift Dashboard',          path: '/govlift' },
  { label: 'GovLift Workloads',          path: '/govlift/workloads' },
  { label: 'GovLift Waves',              path: '/govlift/waves' },
  { label: 'GovLift Executor',           path: '/govlift/executor' },
  { label: 'GovLift STIG',              path: '/govlift/stig' },
  { label: 'GovLift Audit',             path: '/govlift/audit' },
  // Mission Control canvas
  { label: 'Mission Control Dashboard',  path: '/mission-canvas/' },
  // AISG canvas — feature-gated via ICDEV_AISG_ENABLED; 404 is acceptable when disabled
  { label: 'AI Wizard',                  path: '/ai-wizard' },
  { label: 'AI Patterns',               path: '/ai-patterns' },
];

function noServerError(body: string | null, path: string) {
  const b = body ?? '';
  expect(b.toLowerCase(), `${path} server error`).not.toContain('internal server error');
  expect(b, `${path} traceback`).not.toContain('Traceback');
}

test.describe('Canvases Menu — Hard (always-on)', () => {
  for (const pg of HARD_CANVAS_PAGES) {
    test(`${pg.label} (${pg.path}) — HTTP < 400`, async ({ request }) => {
      const resp = await request.get(pg.path);
      expect(resp.status(), `${pg.path} returned ${resp.status()}`).toBeLessThan(400);
    });

    test(`${pg.label} (${pg.path}) — no server error`, async ({ page }) => {
      await page.goto(pg.path);
      await page.waitForLoadState('domcontentloaded');
      await page.screenshot({
        path: `${SCREENSHOT_DIR}/canvas_hard_${pg.label.toLowerCase().replace(/[^a-z0-9]+/g, '_')}.png`,
        fullPage: false,
      });
      noServerError(await page.textContent('body'), pg.path);
    });
  }
});

test.describe('Canvases Menu — Feature-gated (soft check)', () => {
  for (const pg of SOFT_CANVAS_PAGES) {
    test(`${pg.label} (${pg.path}) — not a 500 error`, async ({ request }) => {
      const resp = await request.get(pg.path);
      // 404 = feature disabled (ok); 500 = bug (fail)
      expect(
        resp.status(),
        `${pg.path} returned 500 (server error — canvas disabled pages should return 404 or 200)`
      ).not.toBe(500);
    });

    test(`${pg.label} (${pg.path}) — no traceback in body`, async ({ page }) => {
      await page.goto(pg.path);
      await page.waitForLoadState('domcontentloaded');
      await page.screenshot({
        path: `${SCREENSHOT_DIR}/canvas_soft_${pg.label.toLowerCase().replace(/[^a-z0-9]+/g, '_')}.png`,
        fullPage: false,
      });
      const body = (await page.textContent('body')) ?? '';
      expect(body, `${pg.path} traceback`).not.toContain('Traceback');
      expect(body.toLowerCase(), `${pg.path} werkzeug error`).not.toContain('werkzeug debugger');
    });
  }
});
// CUI // SP-CTI

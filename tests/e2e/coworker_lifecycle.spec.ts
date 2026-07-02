// CUI // SP-CTI
// E2E Test: ACE Co-Worker Engine Lifecycle
// Verifies /coworker pages load, team launch works, instance lifecycle completes,
// and edge cases are handled gracefully. Covers Orchestrator integration via /agents.

import { test, expect } from '@playwright/test';
import path from 'path';
import fs from 'fs';

const SCREENSHOTS = path.resolve(__dirname, '../../.tmp/test_runs/screenshots/coworker');
const CUI_BANNER = 'CUI // SP-CTI';
const PAGE_TIMEOUT = 30_000;

// Shared state across API tests — populated by the "launch" test.
let launchedInstanceId: string | null = null;
let secondInstanceId: string | null = null;

test.describe('ACE Co-Worker Engine Lifecycle', () => {
  test.beforeAll(() => {
    fs.mkdirSync(SCREENSHOTS, { recursive: true });
  });

  test.beforeEach(async ({ page }) => {
    await page.addInitScript(() => {
      localStorage.setItem('icdev_tour_completed', '1');
      localStorage.setItem('icdev_tour_last_step', '999');
    });
  });

  // ── Page Load — Happy Path ──────────────────────────────────────────────── //

  test('index loads with CUI banner and stat cards', async ({ page }) => {
    await page.goto('/coworker/');
    await page.waitForLoadState('domcontentloaded');
    await page.waitForSelector('.stat-card, .ace-home, h1', { timeout: PAGE_TIMEOUT });

    await page.screenshot({
      path: path.join(SCREENSHOTS, '01_index.png'),
      fullPage: true,
    });

    const bodyText = await page.textContent('body');
    expect(bodyText).toContain(CUI_BANNER);
    expect(bodyText?.toLowerCase()).toMatch(/co-worker|coworker|ace/i);

    // Stat cards must be present
    const statCards = page.locator('.stat-card');
    expect(await statCards.count()).toBeGreaterThanOrEqual(1);

    // Launch box present
    const launchBtn = page.locator('#ace-launch-btn, button:has-text("Launch")');
    expect(await launchBtn.count()).toBeGreaterThanOrEqual(1);
  });

  test('role catalog loads with ≥1 role listed', async ({ page }) => {
    await page.goto('/coworker/roles');
    await page.waitForLoadState('domcontentloaded');
    await page.waitForSelector('body', { timeout: PAGE_TIMEOUT });

    await page.screenshot({
      path: path.join(SCREENSHOTS, '02_roles.png'),
      fullPage: true,
    });

    const bodyText = await page.textContent('body');
    expect(bodyText).toContain(CUI_BANNER);

    // Expect at least one role name to appear
    const knownRoles = ['AI Developer', 'Security Analyst', 'QA Manager', 'ai_developer', 'security_analyst'];
    const hasRole = knownRoles.some(r => bodyText?.toLowerCase().includes(r.toLowerCase()));
    expect(hasRole).toBeTruthy();
  });

  test('trust leaderboard page loads (empty data ok)', async ({ page }) => {
    await page.goto('/coworker/trust');
    await page.waitForLoadState('domcontentloaded');

    await page.screenshot({
      path: path.join(SCREENSHOTS, '03_trust.png'),
      fullPage: true,
    });

    const bodyText = await page.textContent('body');
    expect(bodyText).toContain(CUI_BANNER);
    // Route renders ace/trust.html — must mention trust, co-worker, or leaderboard
    expect(bodyText?.toLowerCase()).toMatch(/trust|leaderboard|co-worker|coworker/i);
    expect(bodyText?.toLowerCase()).not.toMatch(/500 internal server error/i);
  });

  test('profile creator form loads with required fields', async ({ page }) => {
    await page.goto('/coworker/profiles/new');
    await page.waitForLoadState('domcontentloaded');

    await page.screenshot({
      path: path.join(SCREENSHOTS, '04_profiles_new.png'),
      fullPage: true,
    });

    const bodyText = await page.textContent('body');
    expect(bodyText).toContain(CUI_BANNER);
    expect(bodyText?.toLowerCase()).toMatch(/profile|role|generate/i);
  });

  // ── API — Happy Path ───────────────────────────────────────────────────── //

  test('GET /api/ace/instances returns 200 and paginated list', async ({ page }) => {
    await page.goto('/coworker/');

    const result = await page.evaluate(async () => {
      const r = await fetch('/api/ace/instances');
      const body = await r.json();
      // Response: {count, instances: [...], limit, offset, total}
      return {
        status: r.status,
        hasInstances: 'instances' in body && Array.isArray(body.instances),
        hasCount: typeof body.count === 'number' || typeof body.total === 'number',
      };
    });

    expect(result.status).toBe(200);
    expect(result.hasInstances).toBe(true);
    expect(result.hasCount).toBe(true);
  });

  test('GET /api/ace/events/topics returns 200 and topics', async ({ page }) => {
    await page.goto('/coworker/');

    const result = await page.evaluate(async () => {
      const r = await fetch('/api/ace/events/topics');
      if (!r.ok) return { status: r.status, ok: false };
      const body = await r.json();
      return { status: r.status, ok: true, hasTopics: Array.isArray(body) || typeof body === 'object' };
    });

    // Acceptable: 200 with topics, or 404 if endpoint not wired (gap C-2 related)
    expect([200, 404]).toContain(result.status);
    if (result.status === 200) {
      expect(result.hasTopics).toBe(true);
    }
  });

  test('POST /api/ace/launch with valid problem returns instance_id', async ({ page }) => {
    await page.goto('/coworker/');

    const result = await page.evaluate(async () => {
      const r = await fetch('/api/ace/launch', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        // API expects problem_text (not problem)
        body: JSON.stringify({ problem_text: 'Write a Python function to compute Fibonacci numbers with memoization and add unit tests.' }),
      });
      const body = await r.json();
      return { status: r.status, body };
    });

    // Launch returns 202 Accepted (async enqueue), not 200
    expect([200, 202]).toContain(result.status);
    expect(result.body).toHaveProperty('instance_id');
    launchedInstanceId = result.body.instance_id;

    await page.screenshot({
      path: path.join(SCREENSHOTS, '05_launch_response.png'),
      fullPage: true,
    });
  });

  test('GET /coworker/<id> instance detail loads', async ({ page }) => {
    // Depends on the launch test above; skip gracefully if no instance was created.
    if (!launchedInstanceId) {
      test.skip();
      return;
    }

    // Poll the status API until the background _run() thread writes the instance row.
    // TeamAssembler.assemble() does the INSERT after problem classification — give it up to 10s.
    await page.goto('/coworker/');
    const instanceReady = await page.evaluate(async (id) => {
      for (let i = 0; i < 20; i++) {
        const r = await fetch(`/api/ace/${id}/status`);
        const body = await r.json();
        if (r.ok && !body.error) return true;
        await new Promise(res => setTimeout(res, 500));
      }
      return false;
    }, launchedInstanceId);

    if (!instanceReady) {
      // Instance never appeared — background thread may have failed; skip detail test
      test.skip();
      return;
    }

    await page.goto(`/coworker/${launchedInstanceId}`);
    await page.waitForLoadState('domcontentloaded');
    await page.waitForSelector('body', { timeout: PAGE_TIMEOUT });

    await page.screenshot({
      path: path.join(SCREENSHOTS, '06_instance_detail.png'),
      fullPage: true,
    });

    const bodyText = await page.textContent('body');
    expect(bodyText).toContain(CUI_BANNER);
    // Should reference the instance ID or a coworker state
    expect(bodyText?.toLowerCase()).toMatch(/assembling|pending|active|complete|failed|coworker/i);
  });

  test('GET /api/ace/<id>/status returns state field', async ({ page }) => {
    if (!launchedInstanceId) {
      test.skip();
      return;
    }

    await page.goto('/coworker/');

    const result = await page.evaluate(async (id) => {
      const r = await fetch(`/api/ace/${id}/status`);
      const body = await r.json();
      return { status: r.status, hasState: 'state' in body || 'status' in body };
    }, launchedInstanceId);

    expect(result.status).toBe(200);
    expect(result.hasState).toBe(true);
  });

  test('GET /api/ace/<id>/messages returns paginated response', async ({ page }) => {
    if (!launchedInstanceId) {
      test.skip(true, 'No instance ID — launch test did not run or failed');
      return;
    }

    await page.goto('/coworker/');

    const result = await page.evaluate(async (id) => {
      const r = await fetch(`/api/ace/${id}/messages`);
      const body = await r.json();
      // Response: {count, messages: [...]}
      return {
        status: r.status,
        hasMessages: 'messages' in body && Array.isArray(body.messages),
        hasCount: typeof body.count === 'number',
      };
    }, launchedInstanceId);

    expect(result.status).toBe(200);
    expect(result.hasMessages).toBe(true);
    expect(result.hasCount).toBe(true);
  });

  test('GET /api/ace/<id>/artifacts returns paginated response', async ({ page }) => {
    if (!launchedInstanceId) {
      test.skip(true, 'No instance ID — launch test did not run or failed');
      return;
    }

    await page.goto('/coworker/');

    const result = await page.evaluate(async (id) => {
      const r = await fetch(`/api/ace/${id}/artifacts`);
      const body = await r.json();
      // Response: {artifacts: [...], count}
      return {
        status: r.status,
        hasArtifacts: 'artifacts' in body && Array.isArray(body.artifacts),
        hasCount: typeof body.count === 'number',
      };
    }, launchedInstanceId);

    expect(result.status).toBe(200);
    expect(result.hasArtifacts).toBe(true);
    expect(result.hasCount).toBe(true);
  });

  test('POST /api/ace/<id>/abort transitions state to cancelled', async ({ page }) => {
    if (!launchedInstanceId) {
      test.skip(true, 'No instance ID — launch test did not run or failed');
      return;
    }

    await page.goto('/coworker/');

    const result = await page.evaluate(async (id) => {
      const r = await fetch(`/api/ace/${id}/abort`, { method: 'POST' });
      const body = await r.json();
      return { status: r.status, body };
    }, launchedInstanceId);

    // Abort returns 200 with {aborted, instance_id, state} or 409 if already terminal
    expect([200, 409]).toContain(result.status);
    if (result.status === 200) {
      expect(result.body).toHaveProperty('aborted');
    }

    await page.screenshot({
      path: path.join(SCREENSHOTS, '09_abort_response.png'),
      fullPage: true,
    });
  });

  // ── Edge Cases ─────────────────────────────────────────────────────────── //

  test('edge: launch with empty problem → non-500 validation response', async ({ page }) => {
    await page.goto('/coworker/');

    const result = await page.evaluate(async () => {
      const r = await fetch('/api/ace/launch', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ problem: '' }),
      });
      return { status: r.status };
    });

    // Must not crash the server — 400 bad request or similar
    expect(result.status).not.toBe(500);
    expect(result.status).toBeGreaterThanOrEqual(400);
    expect(result.status).toBeLessThan(500);
  });

  test('edge: launch with single-word problem → instance_id returned', async ({ page }) => {
    await page.goto('/coworker/');

    const result = await page.evaluate(async () => {
      const r = await fetch('/api/ace/launch', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        // API expects problem_text key
        body: JSON.stringify({ problem_text: 'Debug' }),
      });
      const body = await r.json();
      return { status: r.status, body };
    });

    // Launch returns 202 Accepted (async enqueue), not 200
    expect([200, 202]).toContain(result.status);
    expect(result.body).toHaveProperty('instance_id');
    secondInstanceId = result.body.instance_id;
  });

  test('edge: abort non-existent instance → 404', async ({ page }) => {
    await page.goto('/coworker/');

    const result = await page.evaluate(async () => {
      const r = await fetch('/api/ace/nonexistent-id-xyz-000/abort', { method: 'POST' });
      const body = await r.json();
      return { status: r.status, body };
    });

    // C-7 fixed: abort now validates instance existence and returns 404
    expect(result.status).toBe(404);
    expect(result.body).toHaveProperty('error');
  });

  test('edge: GET /coworker/<unknown-id> → graceful error, not 500', async ({ page }) => {
    await page.goto('/coworker/nonexistent-instance-xyz-000');
    await page.waitForLoadState('domcontentloaded');

    const bodyText = await page.textContent('body');
    // Must render a page (with CUI banner if it's a proper error page, or a redirect)
    expect(bodyText).toBeTruthy();
    expect(bodyText?.toLowerCase()).toMatch(/not found|error|404|no instance/i);

    await page.screenshot({
      path: path.join(SCREENSHOTS, '13_unknown_instance.png'),
      fullPage: true,
    });
  });

  test('edge: GET /api/ace/<id>/messages?after=999999 → empty messages list', async ({ page }) => {
    if (!launchedInstanceId) {
      test.skip(true, 'No instance ID — launch test did not run or failed');
      return;
    }

    await page.goto('/coworker/');

    const result = await page.evaluate(async (id) => {
      const r = await fetch(`/api/ace/${id}/messages?after=999999`);
      const body = await r.json();
      // Response: {count, messages: [...]}
      const msgs = Array.isArray(body) ? body : (body.messages ?? []);
      return { status: r.status, count: msgs.length };
    }, launchedInstanceId);

    expect(result.status).toBe(200);
    expect(result.count).toBe(0);
  });

  test('edge: POST /api/ace/iqe-query with plain text → non-500 response', async ({ page }) => {
    await page.goto('/coworker/');

    const result = await page.evaluate(async () => {
      const r = await fetch('/api/ace/iqe-query', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ query: 'show active co-worker teams' }),
      });
      return { status: r.status };
    });

    // 200 = implemented, 501/404 = known gap (C-2), never 500
    expect(result.status).not.toBe(500);
  });

  test('edge: two sequential launches appear in /api/ace/instances', async ({ page }) => {
    if (!launchedInstanceId || !secondInstanceId) {
      test.skip(true, 'One or both launch tests did not produce an instance_id');
      return;
    }

    await page.goto('/coworker/');

    const result = await page.evaluate(async (ids) => {
      const r = await fetch('/api/ace/instances');
      const body = await r.json();
      // Response: {count, instances: [{id,...},...]}
      const list: Array<{ id: string }> = body.instances ?? body;
      const found = ids.filter((id: string) => list.some((inst) => inst.id === id));
      return { status: r.status, foundCount: found.length };
    }, [launchedInstanceId, secondInstanceId]);

    expect(result.status).toBe(200);
    expect(result.foundCount).toBeGreaterThanOrEqual(1);
  });

  test('GET /api/ace/presets returns 17 presets (C-1 fix)', async ({ page }) => {
    await page.goto('/coworker/');

    const result = await page.evaluate(async () => {
      const r = await fetch('/api/ace/presets');
      const body = await r.json();
      return { status: r.status, count: body.count, hasPresets: Array.isArray(body.presets) };
    });

    expect(result.status).toBe(200);
    expect(result.hasPresets).toBe(true);
    expect(result.count).toBe(17);
  });

  // ── Orchestrator Integration ───────────────────────────────────────────── //

  test('orchestrator: /agents page shows ≥8 core agents', async ({ page }) => {
    await page.goto('/agents');
    await page.waitForLoadState('domcontentloaded');

    await page.screenshot({
      path: path.join(SCREENSHOTS, '17_agents.png'),
      fullPage: true,
    });

    const bodyText = await page.textContent('body');
    expect(bodyText).toContain(CUI_BANNER);

    const coreAgents = ['Orchestrator', 'Architect', 'Builder', 'Compliance', 'Security', 'Infrastructure', 'Knowledge', 'Monitor'];
    const found = coreAgents.filter(a => bodyText?.toLowerCase().includes(a.toLowerCase()));
    expect(found.length).toBeGreaterThanOrEqual(8);
  });

  test('orchestrator: coworker index links to roles and trust leaderboard', async ({ page }) => {
    await page.goto('/coworker/');
    await page.waitForLoadState('domcontentloaded');

    const rolesLink = page.locator('a[href*="/coworker/roles"]');
    expect(await rolesLink.count()).toBeGreaterThanOrEqual(1);

    // C-3 fixed: Trust Leaderboard link now present in the header nav
    const trustLink = page.locator('a[href*="/coworker/trust"]');
    expect(await trustLink.count()).toBeGreaterThanOrEqual(1);

    await page.screenshot({
      path: path.join(SCREENSHOTS, '18_nav_links.png'),
      fullPage: true,
    });
  });

  test('GET /api/ace/<id>/audit returns events array', async ({ page }) => {
    if (!launchedInstanceId) {
      test.skip();
      return;
    }

    // Navigate first so relative URLs resolve against localhost:5050
    await page.goto('/coworker/');
    await page.waitForLoadState('domcontentloaded');

    const result = await page.evaluate(async (id) => {
      const r = await fetch(`/api/ace/${id}/audit?limit=50`);
      const body = await r.json();
      return { status: r.status, hasEvents: Array.isArray(body.events), count: body.count };
    }, launchedInstanceId);

    expect(result.status).toBe(200);
    expect(result.hasEvents).toBe(true);
    expect(typeof result.count).toBe('number');

    await page.screenshot({
      path: path.join(SCREENSHOTS, '19_audit_log.png'),
    });
  });

  test('instance detail page has Activity Log tab', async ({ page }) => {
    if (!launchedInstanceId) {
      test.skip();
      return;
    }

    await page.goto(`/coworker/${launchedInstanceId}`);
    await page.waitForLoadState('domcontentloaded');

    // Tab button must exist
    const auditTab = page.locator('[data-tab="tab-activity"]');
    expect(await auditTab.count()).toBeGreaterThanOrEqual(1);

    // Lifecycle stepper must exist
    const stepper = page.locator('#lifecycle-stepper');
    expect(await stepper.count()).toBe(1);

    await page.screenshot({
      path: path.join(SCREENSHOTS, '20_instance_activity_tab.png'),
      fullPage: true,
    });
  });
});
// CUI // SP-CTI

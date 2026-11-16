// CUI // SP-CTI
// E2E: every write the feature-conformance audit credits to a page is SENT BY THAT
// PAGE, as a person would send it (icx-cnf-08).
//
// The audit (tools/conformance/feature_audit.py) credits a write to a page only when
// the page names its endpoint id (icdevRoute / icdevFetch / url_for). Naming is not
// firing: a dead button names its route too. So CI's E2E job runs the audit's
// --e2e-gate over what tests/e2e/fixtures/observed_calls.ts recorded, and fails on any
// credited write no page sent. A test's own fetch (page.evaluate) does not count, so
// every test here presses the real control with page.click/fill and waits for the
// request. A refusal is a fine answer (no model in CI, a step the person may not
// hold): what is proved is that the page sends it.
//
// What the controls need on screen is seeded by tests/e2e/fixtures/conformance_seed.py
// (a Cortex finding, an agent-loop session that ran out of turns, NOVA trust for the
// e2e-only role cnf08_e2e_auto_role). Everything is named cnf08-e2e-*.

import { test, expect } from './fixtures/observed_calls';
import type { Page, Request } from '@playwright/test';
import { spawnSync } from 'child_process';
import path from 'path';
import { PYTHON, icdevSubprocessEnv } from './fixtures/subprocess_env';

const ROOT = path.resolve(__dirname, '../..');
const SEED = path.join(ROOT, 'tests/e2e/fixtures/conformance_seed.py');
const T = 30_000;

function seed(args: string[] = []): unknown {
  // The seeder writes to the database the RUN asked for, never an ambient one.
  const res = spawnSync(PYTHON, [SEED, ...args], {
    cwd: ROOT, encoding: 'utf8', env: icdevSubprocessEnv({ PYTHONIOENCODING: 'utf-8' }),
  });
  if (res.status !== 0) throw new Error(`conformance_seed.py failed: ${res.stderr || res.stdout}`);
  return JSON.parse(res.stdout.trim().split('\n').pop() || '{}');
}

/** Wait for the page to send `method` to a path matching `re`, while `act` runs. */
async function sends(page: Page, method: string, re: RegExp, act: () => Promise<unknown>): Promise<Request> {
  const req = page.waitForRequest(r => r.method() === method && re.test(new URL(r.url()).pathname), { timeout: T });
  await act();
  return req;
}


test.describe('Every credited write is sent by its page (icx-cnf-08)', () => {
  let session = '';
  test.beforeAll(() => {
    session = (seed() as { session: { session_id: string } }).session.session_id;
  });

  test.beforeEach(async ({ page }) => {
    await page.addInitScript(() => {
      localStorage.setItem('icdev_tour_completed', '1');
      localStorage.setItem('icdev_tour_last_step', '999');
    });
    page.on('dialog', d => { void d.accept(d.type() === 'prompt' ? 'cnf08 e2e: stop for the test' : undefined); });
  });

  // ── ACE: launch, HITL, abort ──────────────────────────────────────────────
  test('ACE: launch from /coworker, approve its checkpoint, abort it', async ({ page }) => {
    await page.goto('/coworker/');
    // Says it is a fixture: where a team has a working model (a workstation), it runs.
    await page.fill('#ace-problem',
      'E2E FIXTURE TEXT (icx-cnf-08), not a task: do not act on it or change any file.');
    await sends(page, 'POST', /^\/api\/ace\/launch$/, () => page.click('#ace-launch-btn'));
    // The page goes to the new instance once it is launched.
    await page.waitForURL(/\/coworker\/ace-[^/]+$/, { timeout: T });
    const id = decodeURIComponent(new URL(page.url()).pathname.split('/').pop() as string);

    seed(['--hitl', id]);
    await page.goto(`/coworker/${encodeURIComponent(id)}`);
    await sends(page, 'POST', new RegExp(`^/api/ace/${id}/hitl$`),
      () => page.locator('.btn-hitl-approve').first().click());

    await page.goto(`/coworker/${encodeURIComponent(id)}`);
    await page.locator('button', { hasText: 'Abort Instance' }).first().click();
    await sends(page, 'POST', new RegExp(`^/api/ace/${id}/abort$`), () => page.click('#abort-confirm-btn'));
  });

  // ── ACE: auto-dispatch stop, resume, grant, revoke ────────────────────────
  test('ACE: stop all, resume, turn auto on and off from the Co-Worker page', async ({ page }) => {
    await page.goto('/coworker/');
    await expect(page.locator('#ace-auto-stopstate')).not.toHaveText('', { timeout: T });
    if (await page.locator('#ace-auto-stop-btn').isDisabled()) {
      await page.fill('#ace-auto-stop-reason', 'cnf08 e2e: resume');
      await sends(page, 'POST', /^\/api\/ace\/auto-dispatch\/resume-all$/, () => page.click('#ace-auto-resume-btn'));
      await page.reload();
      await expect(page.locator('#ace-auto-stopstate')).not.toHaveText('', { timeout: T });
    }
    await page.fill('#ace-auto-stop-reason', 'cnf08 e2e: stop');
    await sends(page, 'POST', /^\/api\/ace\/auto-dispatch\/stop-all$/, () => page.click('#ace-auto-stop-btn'));
    await expect(page.locator('#ace-auto-resume-btn')).toBeEnabled({ timeout: T });
    await page.fill('#ace-auto-stop-reason', 'cnf08 e2e: resume');
    await sends(page, 'POST', /^\/api\/ace\/auto-dispatch\/resume-all$/, () => page.click('#ace-auto-resume-btn'));

    // The e2e-only role (tests/e2e/fixtures/ace_roles) is the one auto-eligible pair.
    await page.reload();
    await expect(page.locator('#ace-auto-grant-form')).toBeVisible({ timeout: T });
    await page.selectOption('#ace-auto-pair', { label: 'cnf08_e2e_auto_role / e2e.cnf08.auto' });
    await page.fill('#ace-auto-reason', 'cnf08 e2e: grant for the test');
    await sends(page, 'POST', /^\/api\/ace\/auto-dispatch\/grant$/, () => page.click('#ace-auto-grant-btn'));
    const off = page.locator('#ace-auto-grants button', { hasText: 'Turn off' }).first();
    await expect(off).toBeVisible({ timeout: T });
    await sends(page, 'POST', /^\/api\/ace\/auto-dispatch\/[^/]+\/revoke$/, () => off.click());
  });

  // ── ACE: profiles ─────────────────────────────────────────────────────────
  test('ACE: suggest a name, preview, create, then delete the profile', async ({ page }) => {
    const name = `CNF08 E2E Profile ${Date.now()}`;
    await page.goto('/coworker/profiles/new');
    await page.fill('#pdesc', 'cnf08 e2e: reviews test fixtures for the conformance audit');
    await sends(page, 'POST', /^\/api\/ace\/profiles\/suggest-name$/,
      () => page.locator('.suggest-trigger').click());
    await page.fill('#pname', name);
    await sends(page, 'POST', /^\/api\/ace\/profiles\/preview$/, () => page.click('#btn-assist'));
    await expect(page.locator('#btn-save')).toBeVisible({ timeout: T });
    const made = await sends(page, 'POST', /^\/api\/ace\/profiles\/generate$/, () => page.click('#btn-save'));
    const out = await (await made.response())?.json().catch(() => ({})) as { role_id?: string };
    expect(out.role_id, 'the profile was created').toBeTruthy();

    await page.goto('/coworker/roles');
    const del = page.locator(`[data-delete-profile="${out.role_id}"]`);
    await expect(del).toBeVisible({ timeout: T });
    await sends(page, 'DELETE', new RegExp(`^/api/ace/profiles/${out.role_id}$`), () => del.click());
  });

  // ── ACE: chat, session eval and re-run, IQE ───────────────────────────────
  test('ACE: chat, run an eval and re-run a session, ask IQE', async ({ page }) => {
    await page.goto('/coworker/chat');
    await page.fill('#ace-chat-input', 'cnf08 e2e: what does this team do?');
    await sends(page, 'POST', /^\/api\/ace\/chat$/, () => page.click('#ace-chat-send'));

    await page.goto(`/coworker/sessions/${session}`);
    const runEval = page.locator('button', { hasText: 'Run Eval' });
    await expect(runEval).toBeVisible({ timeout: T });
    await sends(page, 'POST', new RegExp(`^/api/ace/sessions/${session}/eval$`), () => runEval.click());
    await page.goto(`/coworker/sessions/${session}`);
    await expect(page.locator('#rerun-btn')).toBeVisible({ timeout: T });
    await sends(page, 'POST', new RegExp(`^/api/ace/sessions/${session}/rerun$`), () => page.click('#rerun-btn'));

    await iqe(page, '/coworker/', /^\/api\/ace\/iqe-query$/);
  });

  // ── ACE: the suggested team, confirmed and declined from chat ─────────────
  test('ACE: a suggested team is confirmed and declined from its chat card', async ({ page }) => {
    for (const [label, re] of [['No thanks', /^\/api\/ace\/suggestions\/[^/]+\/decline$/],
                               ['Spin up the team', /^\/api\/ace\/suggestions\/[^/]+\/confirm$/]] as const) {
      await page.goto('/chat');
      await page.click('#btn-new-context');
      await page.fill('#new-ctx-title', `cnf08 e2e ${label}`);
      await page.click('#btn-create-context');
      const input = page.locator('#message-input');
      await expect(input).toBeEnabled({ timeout: T });
      // 200+ characters with four or more requirement signals read as a multi-step goal,
      // so the chat proposes a co-worker team (tools/ace/chat_trigger.py) as a card. The
      // text says it is a fixture: where the chat has a working model (a workstation, not
      // CI) its agent also answers the turn, and it must not take it as work to do.
      await input.fill('E2E FIXTURE TEXT (icx-cnf-08), not a request: do not act on it. It only has '
        + 'to read as a goal. Requirements: the system must track each record. Monitor the queue '
        + 'and alert on a backlog. Analyze the totals weekly. Deploy with acceptance criteria.');
      await page.click('#btn-send');
      const btn = page.locator('[data-sme-actions] button', { hasText: label }).last();
      await expect(btn).toBeVisible({ timeout: 60_000 });
      await sends(page, 'POST', re, () => btn.click());
    }
  });

  // ── Cortex ────────────────────────────────────────────────────────────────
  test('Cortex: chat, govern, every verb, intake, and IQE', async ({ page }) => {
    await page.goto('/cortex/');
    await page.fill('#cortex-question', 'cnf08 e2e: what is TLS 1.1?');
    await sends(page, 'POST', /^\/cortex\/api\/chat$/, () => page.click('#cortex-send'));

    await page.goto('/cortex/govern');
    await sends(page, 'POST', /^\/cortex\/api\/v1\/govern$/,
      () => page.locator('button', { hasText: /run|govern/i }).first().click());

    await page.goto('/cortex/verbs');
    for (const verb of ['resolve', 'complete', 'reason', 'classify', 'extract']) {
      await sends(page, 'POST', new RegExp(`^/cortex/api/v1/${verb}$`),
        () => page.click(`button[data-verb="${verb}"]`));
    }

    await page.goto('/cortex/intake');
    await page.fill('#intake-customer', 'cnf08 e2e customer');
    await page.fill('#intake-org', 'cnf08 e2e org');
    await page.fill('#intake-ask', 'cnf08 e2e: we need a records system');
    await sends(page, 'POST', /^\/cortex\/api\/v1\/intake\/session$/,
      () => page.locator('button[onclick="intakeCreate()"]').click());
    if (!(await page.inputValue('#intake-session'))) await page.fill('#intake-session', 'cnf08-e2e-intake');
    await page.fill('#intake-message', 'cnf08 e2e: about 200 users');
    await sends(page, 'POST', /^\/cortex\/api\/v1\/intake\/turn$/,
      () => page.locator('button[onclick="intakeTurn()"]').click());

    await iqe(page, '/cortex/', /^\/cortex\/api\/iqe-query$/);
  });

  test('Cortex: every PMO verb, and an award proposed for another person to decide', async ({ page }) => {
    await page.goto('/cortex/pmo');
    for (const verb of ['slides', 'win_themes', 'staffing_matrix', 'cost_volume', 'dashboard']) {
      await sends(page, 'POST', new RegExp(`^/cortex/api/v1/${verb}$`),
        () => page.locator(`#pmo-${verb} button`, { hasText: 'Send' }).click());
    }
    // BOM reconcile reads the files a person picks; one small quote is enough.
    await page.setInputFiles('#pmo-bom-files', { name: 'cnf08-e2e-quote.csv', mimeType: 'text/csv',
      buffer: Buffer.from('part,qty,unit_price\ncnf08-e2e-switch,2,1000\n') });
    await sends(page, 'POST', /^\/cortex\/api\/v1\/bom$/, () => page.click('button[onclick="pmoBom()"]'));
    await page.fill('#pmo-award-opp', 'cnf08-e2e-opportunity');
    await sends(page, 'POST', /^\/cortex\/api\/v1\/award$/,
      () => page.locator('button', { hasText: 'Propose award' }).click());
  });

  // ── Cortex findings, adjudicated in the Explorer ──────────────────────────
  test('Cortex findings: adjudicate an open finding with a reason', async ({ page }) => {
    await page.goto('/document-intelligence/explorer');
    const box = page.locator('.cortex-adjudication', { has: page.locator('[data-adj="accept"]') }).first();
    await expect(box).toBeVisible({ timeout: T });
    await box.locator('[data-adj-reason]').fill('cnf08 e2e: the catalog is authoritative');
    await sends(page, 'POST', /^\/document-intelligence\/api\/explorer\/cortex-findings\/[^/]+\/adjudicate$/,
      () => box.locator('[data-adj="accept"]').click());
  });
});

test.describe('Route ids in the console (icx-cnf-08)', () => {
  test('the route helpers fill a rule and refuse a missing, empty or unknown parameter', async ({ page }) => {
    await page.goto('/coworker/');
    const out = await page.evaluate(() => {
      const w = window as unknown as {
        icdevRoute: (e: string, p?: object, q?: object) => string;
        icdevFetch: (e: string, p?: object, i?: object) => Promise<Response>;
      };
      const err = (f: () => unknown) => { try { f(); return ''; } catch (e) { return String(e); } };
      return {
        filled: w.icdevRoute('ace.instance_detail', { instance_id: 'a b/c' }),
        query: w.icdevRoute('ace_api.api_evals_list', {}, { limit: 5, skip: null }),
        missing: err(() => w.icdevRoute('ace.instance_detail')),
        empty: err(() => w.icdevRoute('ace.instance_detail', { instance_id: '' })),
        extra: err(() => w.icdevRoute('ace_api.api_launch', { instance_id: 'x' })),
        unknown: err(() => w.icdevRoute('no_such.endpoint')),
      };
    });
    expect(out.filled).toBe('/coworker/a%20b%2Fc');
    expect(out.query).toBe('/api/ace/evals?limit=5');
    expect(out.missing).toContain('ace.instance_detail takes {instance_id}');
    expect(out.empty).toContain('needs instance_id');
    expect(out.extra).toContain('ace_api.api_launch takes {}');
    expect(out.unknown).toContain('unknown endpoint no_such.endpoint');
    const wrongMethod = await page.evaluate(() =>
      (window as unknown as { icdevFetch: (e: string, p?: object, i?: object) => Promise<Response> })
        .icdevFetch('ace_api.api_launch', {}, { method: 'GET' }).then(() => 'sent', e => String(e)));
    expect(wrongMethod).toContain('serves POST, not GET');
  });

  test('the conformance page shows each route named by id and seen in e2e', async ({ page }) => {
    await page.goto('/twin-observatory/conformance');
    await expect(page.locator('#conformance-routes-summary')).toContainText('named by a page by endpoint id');
    const row = page.locator('table.conformance-routes tr[data-endpoint="ace_api.api_launch"]').first();
    await expect(row).toContainText('coworker/index.html');
    await expect(row).toContainText('seen');
  });
});

/** Open a page's IQE widget, ask it something, and see the page send the query. */
async function iqe(page: Page, url: string, re: RegExp) {
  await page.goto(url);
  const widget = page.locator('.iqe-widget').first();
  await widget.locator('.iqe-widget__header').click();
  await widget.locator('textarea.iqe-ta').fill('show all open findings');
  await sends(page, 'POST', re, () => widget.locator('.iqe-run-btn').click());
}

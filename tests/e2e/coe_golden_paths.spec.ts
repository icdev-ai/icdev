// CUI // SP-CTI
// E2E: golden paths, so that a new service is conformant by construction (icx-coe-04).
//
// Three fresh, card-named people (tests/e2e/fixtures/coe04_seed.py, synthetic): coe04-author, coe04-publisher and
// coe04-steward, each in its own browser context, each signed in by its own dashboard API key. All hold the role
// that publishes, so what stops the author publishing their own version on the page is separation of duties.
// The journey, every write pressed on the page except the one mid-journey seed:
//
//   1. the author opens the golden paths page;
//   2. the `service` path meets every standard in force; its drift check (run on the page: scaffold into a temp
//      tree, then the IDP scorecard and feature_audit) passes, a scorecard-rule standard passes by machine and a
//      manual one needs evidence;
//   3. a NEW standard that applies to services is adopted (seeded through the product's own functions: proposed by
//      the author, reviewed and adopted by the steward), and the page shows both paths behind it;
//   4. the author proposes a new `service` version that declares it, and its drift check runs;
//   5. the author is refused publishing their own version (separation);
//   6. the publisher, another person, publishes it, and the path meets its standards again;
//   7. the twin observes the paths and a finding's thread (model-service, still behind) opens on its ledger row;
//      the steward takes the retire step on the version in use and keeps it.
//
// Then the page in both themes (dark, light) with axe (the vendored axe-core) over its content.

import { test, expect } from './fixtures/observed_calls';
import type { Browser, BrowserContext, Page, Request } from '@playwright/test';
import { spawnSync } from 'child_process';
import path from 'path';
import { PYTHON, icdevSubprocessEnv } from './fixtures/subprocess_env';

const ROOT = path.resolve(__dirname, '../..');
const SEED = path.join(ROOT, 'tests/e2e/fixtures/coe04_seed.py');
const AXE = path.join(ROOT, 'tools/testing/vendor/axe/axe.min.js');
const T = 90_000;

type Person = { id: string; name: string; role: string; key: string };
type Seeded = { run: string; author: Person; publisher: Person; steward: Person; standard: string; version: string };

function py(args: string[]): string {
  // The seeder writes to the database the RUN asked for, never an ambient one.
  const res = spawnSync(PYTHON, [SEED, ...args], {
    cwd: ROOT, encoding: 'utf8', env: icdevSubprocessEnv({ PYTHONIOENCODING: 'utf-8' }),
  });
  if (res.status !== 0) throw new Error(`coe04_seed.py ${args.join(' ')} failed: ${res.stderr || res.stdout}`);
  return res.stdout.trim().split('\n').pop() || '{}';
}

/** A person's own browser context: their own session, signed in by their own key. */
async function as(browser: Browser, who: Person, where: string, theme = 'dark'): Promise<{ ctx: BrowserContext; page: Page }> {
  const ctx = await browser.newContext({ extraHTTPHeaders: { Authorization: `Bearer ${who.key}` } });
  await ctx.addInitScript((t: string) => {
    localStorage.setItem('icdev_tour_completed', '1');
    localStorage.setItem('icdev_tour_last_step', '999');
    localStorage.setItem('icdev-theme', t);
  }, theme);
  const page = await ctx.newPage();
  await page.goto(where);
  return { ctx, page };
}

/** Wait for the page to send `method` to a path matching `re`, while `act` runs. */
async function sends(page: Page, method: string, re: RegExp, act: () => Promise<unknown>): Promise<Request> {
  const req = page.waitForRequest(r => r.method() === method && re.test(new URL(r.url()).pathname), { timeout: T });
  await act();
  return req;
}

async function open(page: Page, id: string) {
  await sends(page, 'GET', new RegExp(`^/idp/api/golden-paths/${id}$`), () => page.locator(`[data-open="${id}"]`).click());
  await expect(page.locator('#gp-detail-title')).toContainText(`Golden path ${id}`, { timeout: T });
  await expect(page.locator(`[data-open="${id}"]`)).toHaveAttribute('aria-pressed', 'true');
}

test.describe('Golden paths: a new service conformant by construction (icx-coe-04)', () => {
  test.describe.configure({ mode: 'serial' });
  let s: Seeded;

  test.beforeAll(() => { s = JSON.parse(py([])); });
  test.afterAll(() => {
    // Deprecate the standard this run adopted (a declared step) and refuse anything a failed run left open.
    if (s) py(['--cleanup', s.run, '--by', s.steward.id]);
  });

  test('see a path meet its standards, fall behind a new one, propose, refuse the author, publish, open the twin thread', async ({ browser }) => {
    test.setTimeout(420_000);
    // 1. the author opens the golden paths page
    const author = await as(browser, s.author, '/idp/golden-paths');
    const page = author.page;
    await expect(page.locator('#gp-help')).toContainText('conformant by construction');
    await expect(page.locator('tr[data-path="service"]')).toHaveAttribute('data-behind', 'false');

    // 2. `service` meets every standard in force; its drift check passes on the page
    await open(page, 'service');
    await expect(page.locator('#gp-behind')).toContainText('meets every standard in force');
    await sends(page, 'POST', /^\/idp\/api\/golden-paths\/service\/drift$/, () => page.click('#gp-run-drift'));
    await expect(page.locator('#gp-drift-msg')).toContainText('Drift check of service', { timeout: T });
    await expect(page.locator('#gp-drift-result')).toHaveAttribute('data-result', 'pass', { timeout: T });
    await expect(page.locator('#gp-drift-result')).toContainText('red first: yes');
    await expect(page.locator('tr[data-std="ENG-E2E-SPEC"]')).toHaveAttribute('data-status', 'pass');
    await expect(page.locator('tr[data-std="ENG-PAGE-COMPLETENESS"]')).toContainText('by machine');
    await expect(page.locator('tr[data-std="ENG-RED-FIRST"]')).toHaveAttribute('data-status', 'needs_evidence');
    await expect(page.locator('tr[data-std="ENG-RED-FIRST"]')).toContainText('needs evidence');

    // 3. a new standard that applies to services is adopted (the product's own functions); both paths fall behind
    py(['--adopt', s.run, '--author', s.author.id, '--by', s.steward.id]);
    await page.reload();
    await expect(page.locator('tr[data-path="service"]')).toHaveAttribute('data-behind', 'true');
    await expect(page.locator('tr[data-path="model-service"]')).toHaveAttribute('data-behind', 'true');
    await expect(page.locator('tr[data-path="service"]')).toContainText(s.standard);
    await open(page, 'service');
    await expect(page.locator('#gp-behind')).toContainText('behind the standards in force');
    await expect(page.locator(`tr[data-std="${s.standard}"]`)).toHaveAttribute('data-state', 'missing');

    // 4. the author proposes a new version that declares it; its drift check runs
    await expect(page.locator('#gp-satisfies')).toHaveValue(new RegExp(`${s.standard}@1\\.0`));
    await page.fill('#gp-version', s.version);
    await page.fill('#gp-note', 'coe04 e2e: declares the new standard (synthetic)');
    await sends(page, 'POST', /^\/idp\/api\/golden-paths\/propose$/, () => page.click('#gp-propose'));
    await expect(page.locator('#gp-propose-msg')).toContainText(`Proposed service ${s.version}`, { timeout: T });
    await expect(page.locator('#gp-drift-msg')).toContainText(`service ${s.version}: pass`, { timeout: T });
    await expect(page.locator(`tr[data-version="${s.version}"]`)).toHaveAttribute('data-state', 'proposed', { timeout: T });
    await expect(page.locator(`tr[data-version="${s.version}"]`)).toContainText('pass');
    await expect(page.locator('#gp-step')).toContainText('The gate holds.');

    // 5. the author is refused publishing their own version
    await page.fill('#gp-reason', 'coe04 e2e: my own (must be refused)');
    await sends(page, 'POST', /^\/idp\/api\/golden-paths\/service\/publish$/,
      () => page.locator('button[data-step="publish"][data-decision="publish"]').click());
    await expect(page.locator('#gp-step-msg')).toContainText('Refused (separation)', { timeout: T });

    // 6. the publisher, another person, publishes it; the path meets its standards again
    const publisher = await as(browser, s.publisher, '/idp/golden-paths');
    const pp = publisher.page;
    await open(pp, 'service');
    await expect(pp.locator('#gp-step')).toContainText(s.author.id);
    await pp.fill('#gp-reason', 'coe04 e2e: read the version and its drift check, publish it (synthetic)');
    await sends(pp, 'POST', /^\/idp\/api\/golden-paths\/service\/publish$/,
      () => pp.locator('button[data-step="publish"][data-decision="publish"]').click());
    await expect(pp.locator('#gp-step-msg')).toContainText(`service ${s.version} published by ${s.publisher.id}`, { timeout: T });
    await expect(pp.locator(`tr[data-version="${s.version}"]`)).toHaveAttribute('data-state', 'published', { timeout: T });
    await expect(pp.locator('#gp-behind')).toContainText('meets every standard in force');
    await expect(pp.locator('#gp-detail-title')).toContainText(`(${s.version} in use)`);

    // 7. the twin observes the paths; a finding's thread opens on its ledger row
    await sends(pp, 'GET', /^\/idp\/api\/standards-twin$/, () => pp.click('#twin-observe'));
    await expect(pp.locator('#gp-twin-verdict')).toBeVisible({ timeout: T });
    const finding = pp.locator('li[data-rule="coe.golden_path.behind"]').filter({ hasText: 'model-service' }).first();
    await expect(finding).toContainText(s.standard);
    await expect(pp.locator('li[data-rule="coe.golden_path.behind"]').filter({ hasText: /^high: service / })).toHaveCount(0);
    await finding.locator('button[data-thread]').click();
    await expect(pp.locator('#gp-thread-resolved')).toContainText('coe_golden_path_events', { timeout: T });
    await expect(pp.locator('#gp-thread-resolved')).toContainText('model-service');

    // and the retire step is a person's too: the steward reads the version in use and keeps it (decided, recorded)
    const steward = await as(browser, s.steward, '/idp/golden-paths');
    const sp = steward.page;
    await open(sp, 'service');
    await expect(sp.locator('#gp-step')).toContainText(`Retire service ${s.version}`);
    await sp.fill('#gp-reason', 'coe04 e2e: still the path to use (synthetic)');
    await sends(sp, 'POST', /^\/idp\/api\/golden-paths\/service\/retire$/,
      () => sp.locator('button[data-step="retire"][data-decision="keep"]').click());
    await expect(sp.locator('#gp-step-msg')).toContainText(`service ${s.version} kept by ${s.steward.id}`, { timeout: T });

    for (const p of [author, publisher, steward]) await p.ctx.close();
  });

  for (const theme of ['dark', 'light']) {
    test(`the golden paths page in the ${theme} theme has no axe violations`, async ({ browser }) => {
      const { ctx, page } = await as(browser, s.publisher, '/idp/golden-paths', theme);
      await expect(page.locator('html')).toHaveAttribute('data-theme', theme);
      await expect(page.locator('#gp-detail-title')).toContainText('Golden path', { timeout: T });
      await page.click('#twin-observe');
      await expect(page.locator('#gp-twin-verdict')).toBeVisible({ timeout: T });
      await page.addScriptTag({ path: AXE });
      const result = await page.evaluate(async () => {
        // @ts-expect-error axe is injected above
        const r = await window.axe.run(document.querySelector('.std-wrap'),
          { runOnly: { type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'] } });
        return r.violations.map((v: { id: string; nodes: { target: string[]; failureSummary?: string }[] }) =>
          `${v.id}: ${v.nodes.slice(0, 3).map(n => `${n.target.join(' ')} [${(n.failureSummary || '').split('\n').pop()}]`).join('; ')}`);
      });
      expect(result, `axe violations on /idp/golden-paths in the ${theme} theme`).toEqual([]);
      await ctx.close();
    });
  }
});

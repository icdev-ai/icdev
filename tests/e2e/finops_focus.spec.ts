// CUI // SP-CTI
// E2E: a FOCUS cost bundle prepared from a synthetic export and published by a person (osx-cop-05).
//
// Two fresh, card-named people (tests/e2e/fixtures/cop05_seed.py, synthetic): cop05i-preparer and cop05i-publisher,
// each in its own browser context (its own session), each signed in by its own dashboard API key. Both hold the
// role that publishes, so what stops the preparer publishing their own bundle on the page is separation of duties.
// The seed copies the synthetic FOCUS 1.4 export (made-up provider "Example Cloud (synthetic)", one billing month,
// the four declared workloads and two untagged rows) into the directory the server reads exports from
// (ICDEV_FINOPS_FOCUS_DIR). The journey, every write pressed on the page:
//
//   1. the preparer opens FOCUS cost bundles and reads the export: each workload's total, the unattributed rows;
//   2. the preparer prepares the bundle; it waits for a person; publishing it themselves is refused (separation);
//   3. the publisher, someone else, publishes it with a reason;
//   4. the published bundle shows with its digest, and its route returns it with its publication;
//   5. the twin observes it: the bundle and every workload verified, and a finding's thread opens on its ledger row.
//
// Then the page in both themes (dark, light) with axe (the vendored axe-core) over its content.

import { test, expect } from './fixtures/observed_calls';
import type { Browser, BrowserContext, Page, Request } from '@playwright/test';
import { spawnSync } from 'child_process';
import path from 'path';
import { PYTHON, icdevSubprocessEnv } from './fixtures/subprocess_env';

const ROOT = path.resolve(__dirname, '../..');
const SEED = path.join(ROOT, 'tests/e2e/fixtures/cop05_seed.py');
const AXE = path.join(ROOT, 'tools/testing/vendor/axe/axe.min.js');
const T = 60_000;

type Person = { id: string; name: string; role: string; key: string };
type Total = { key?: string; rows: number; effective_cost: string; billed_cost: string };
type Seeded = { run: string; preparer: Person; publisher: Person;
  export: { name: string; period: string; rows: number; digest: string; workloads: Record<string, Total>; unattributed: Total } };

function seed(): Seeded {
  // The seeder writes to the database the RUN asked for, never an ambient one.
  const res = spawnSync(PYTHON, [SEED], {
    cwd: ROOT, encoding: 'utf8', env: icdevSubprocessEnv({ PYTHONIOENCODING: 'utf-8' }),
  });
  if (res.status !== 0) throw new Error(`cop05_seed.py failed: ${res.stderr || res.stdout}`);
  return JSON.parse(res.stdout.trim().split('\n').pop() || '{}');
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

test.describe('FOCUS cost bundles: prepared from an export, published by a person (osx-cop-05)', () => {
  test.describe.configure({ mode: 'serial' });
  let s: Seeded;

  test.beforeAll(() => { s = seed(); });

  test('read the export, prepare, refuse the preparer, publish, see the bundle, open the twin thread', async ({ browser }) => {
    test.setTimeout(300_000);
    // 1. the preparer reads the synthetic export
    const preparer = await as(browser, s.preparer, '/idp/finops/focus');
    const page = preparer.page;
    await expect(page.locator('#ff-help')).toContainText('refuses the whole export');
    await expect(page.locator(`[data-export="${s.export.name}"]`)).toHaveAttribute('aria-pressed', 'true', { timeout: T });
    await expect(page.locator('#ff-digest')).toHaveText(s.export.digest, { timeout: T });
    await expect(page.locator('#ff-summary')).toContainText(`${s.export.rows} FOCUS 1.4 rows`);
    for (const [key, w] of Object.entries(s.export.workloads)) {
      const row = page.locator(`tr[data-workload="${key}"]`);
      await expect(row).toHaveAttribute('data-rows', String(w.rows));
      await expect(row.locator('[data-effective]')).toHaveAttribute('data-effective', w.effective_cost);
    }
    await expect(page.locator('tr[data-workload="icos-api"]')).toContainText('96.457550 USD');
    const un = page.locator('tr[data-unattributed]');
    await expect(un).toHaveAttribute('data-rows', String(s.export.unattributed.rows));
    await expect(un).toContainText(`${s.export.unattributed.effective_cost} USD`);

    // 2. the preparer prepares it; it waits for a person; publishing it themselves is refused
    await sends(page, 'POST', /^\/idp\/api\/finops\/focus\/prepare$/, () => page.click('#ff-prepare'));
    await expect(page.locator('#ff-prepare-msg')).toContainText(`Prepared the ${s.export.period} bundle ${s.export.digest}`, { timeout: T });
    const waiting = page.locator('[data-waiting]').filter({ hasText: s.preparer.id });
    await expect(waiting).toContainText('waiting for a person', { timeout: T });
    const prepared = await waiting.getAttribute('data-waiting');
    expect(prepared).toMatch(/^fofe-[0-9a-f]{16}$/);
    await waiting.locator('input.ff-reason').fill('cop05 e2e: my own (must be refused)');
    await sends(page, 'POST', new RegExp(`^/idp/api/finops/focus/${prepared}/publish$`),
      () => waiting.locator('button[data-publish="publish"]').click());
    await expect(page.locator('#ff-publish-msg')).toContainText('Refused (separation)', { timeout: T });

    // 3. the publisher, another person, publishes it
    const publisher = await as(browser, s.publisher, '/idp/finops/focus');
    const pp = publisher.page;
    const box = pp.locator(`[data-waiting="${prepared}"]`);
    await expect(box).toContainText(s.preparer.id, { timeout: T });
    await expect(box).toContainText(s.export.digest);
    await box.locator('input.ff-reason').fill('cop05 e2e: read the bundle, publish it (synthetic)');
    await sends(pp, 'POST', new RegExp(`^/idp/api/finops/focus/${prepared}/publish$`),
      () => box.locator('button[data-publish="publish"]').click());
    await expect(pp.locator('#ff-publish-msg')).toContainText(`bundle published by ${s.publisher.id}`, { timeout: T });

    // 4. the bundle, with its digest; the route returns it with its publication
    await expect(pp.locator('#ff-published-digest')).toHaveText(s.export.digest, { timeout: T });
    await expect(pp.locator('#ff-published-bundle')).toContainText('icos-focus-cost/v1');
    await expect(pp.locator('#ff-published-bundle')).toContainText(`prepared by ${s.preparer.id}`);
    const href = await pp.locator('#ff-bundle-link').getAttribute('href');
    expect(href).toMatch(/^\/idp\/api\/finops\/focus\/fofe-[0-9a-f]{16}\/bundle$/);
    const published = (href || '').split('/')[5];
    await expect(pp.locator(`tr[data-event="${published}"]`)).toHaveAttribute('data-kind', 'published', { timeout: T });
    const resp = await pp.request.get(href || '');
    expect(resp.ok()).toBeTruthy();
    const bundle = await resp.json();
    expect(bundle.schema).toBe('icos-focus-cost/v1');
    expect(bundle.content_digest).toBe(s.export.digest);
    expect(bundle.publication.signed_by).toBe(s.publisher.id);
    expect(bundle.publication.prepared_by).toBe(s.preparer.id);
    expect(bundle.publication.template).toBe('decl-wft-finops-focus-publish');
    expect(bundle.publication.event_id).toBe(published);
    expect(bundle.workloads.map((w: Total) => w.key)).toEqual(['icos-api', 'icos-mesh', 'icos-data-plane', 'icos-twin']);

    // 5. the twin observes it; a finding's thread opens on its ledger row
    await sends(pp, 'GET', /^\/idp\/api\/standards-twin$/, () => pp.click('#twin-observe'));
    await expect(pp.locator('#ff-twin-verdict')).toBeVisible({ timeout: T });
    await expect(pp.locator('li[data-check="finops.focus.published"]')).toHaveAttribute('data-status', 'verified');
    for (const key of Object.keys(s.export.workloads)) {
      await expect(pp.locator(`li[data-check="finops.focus.${key}"]`)).toHaveAttribute('data-status', 'verified');
    }
    const finding = pp.locator('li[data-rule="finops.focus.unattributed"]').first();
    await expect(finding).toContainText(`${s.export.unattributed.rows} row(s)`);
    await expect(finding.locator('button[data-thread]')).toHaveText(`finops_focus:${published}`);
    await finding.locator('button[data-thread]').click();
    await expect(pp.locator('#ff-thread-resolved')).toContainText('finops_focus_events', { timeout: T });
    await expect(pp.locator('#ff-thread-resolved')).toContainText(`published by ${s.publisher.id}`);
    await expect(pp.locator('#ff-thread-resolved')).toContainText(s.export.digest.slice(0, 16));

    for (const p of [preparer, publisher]) await p.ctx.close();
  });

  for (const theme of ['dark', 'light']) {
    test(`the FOCUS cost bundles page in the ${theme} theme has no axe violations`, async ({ browser }) => {
      const { ctx, page } = await as(browser, s.publisher, '/idp/finops/focus', theme);
      await expect(page.locator('html')).toHaveAttribute('data-theme', theme);
      await expect(page.locator('#ff-digest')).toBeVisible({ timeout: T });
      await page.click('#twin-observe');
      await expect(page.locator('#ff-twin-verdict')).toBeVisible({ timeout: T });
      await page.addScriptTag({ path: AXE });
      const result = await page.evaluate(async () => {
        // @ts-expect-error axe is injected above
        const r = await window.axe.run(document.querySelector('.ff-wrap'),
          { runOnly: { type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'] } });
        return r.violations.map((v: { id: string; nodes: { target: string[]; failureSummary?: string }[] }) =>
          `${v.id}: ${v.nodes.slice(0, 3).map(n => `${n.target.join(' ')} [${(n.failureSummary || '').split('\n').pop()}]`).join('; ')}`);
      });
      expect(result, `axe violations on /idp/finops/focus in the ${theme} theme`).toEqual([]);
      await ctx.close();
    });
  }
});

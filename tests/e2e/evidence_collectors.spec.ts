// CUI // SP-CTI
// E2E: evidence collectors for customer environments, a report published to ICDEV[OS] by a person (icx-ent-02).
//
// Three fresh, card-named people (tests/e2e/fixtures/ent02_seed.py, synthetic), each in its own browser context
// signed in by its own dashboard API key: ent02-preparer and ent02-publisher of the synthetic system's organisation,
// and ent02-outsider of another. The seed prepared syn-dib-01's report as the preparer: the real read-only collectors
// read local stub sources (synthetic) where one device is unencrypted and the SIEM review search is absent.
// The journey, every write pressed on the page:
//
//   1. the preparer opens the page: the system, its four collectors (credentials by reference), the prepared report
//      waiting for a person, an objective observed other than satisfied, one left unverified, and the observations;
//   2. the preparer presses Collect and prepare: the configured sources are documentation placeholders with no
//      credential, so it is refused as nothing determinable (no report from nothing);
//   3. the preparer is refused publishing their own report (separation of duties);
//   4. the publisher, another person of the org, publishes it with a reason;
//   5. the published report shows; the bundle route returns it with its publication embedded, as ICDEV[OS] imports it;
//   6. its twin thread resolves to the ledger row;
//   7. the outsider sees no system (one held back) and the system's route is "no such system".
//
// Then the page in both themes (dark, light) with axe (the vendored axe-core) over its content.

import { test, expect } from './fixtures/observed_calls';
import type { Browser, BrowserContext, Page, Request } from '@playwright/test';
import { spawnSync } from 'child_process';
import path from 'path';
import { PYTHON, icdevSubprocessEnv } from './fixtures/subprocess_env';

const ROOT = path.resolve(__dirname, '../..');
const SEED = path.join(ROOT, 'tests/e2e/fixtures/ent02_seed.py');
const AXE = path.join(ROOT, 'tools/testing/vendor/axe/axe.min.js');
const T = 60_000;
const PAGE = '/evidence-collectors/?system=syn-dib-01';

type Person = { id: string; name: string; role: string; key: string; org: string };
type Seeded = { run: string; preparer: Person; publisher: Person; outsider: Person;
  prepared: { content_digest: string; event_id: string; entries: number; unverified: number } };

function seed(): Seeded {
  // The seeder writes to the database the RUN asked for, never an ambient one.
  const res = spawnSync(PYTHON, [SEED], {
    cwd: ROOT, encoding: 'utf8', env: icdevSubprocessEnv({ PYTHONIOENCODING: 'utf-8' }),
  });
  if (res.status !== 0) throw new Error(`ent02_seed.py failed: ${res.stderr || res.stdout}`);
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

test.describe('Evidence collectors: a customer system\'s objective evidence, published by another person (icx-ent-02)', () => {
  test.describe.configure({ mode: 'serial' });
  let s: Seeded;

  test.beforeAll(() => { s = seed(); });

  test('see the observations, refuse nothing-determinable and the preparer, publish, see the report, open the thread, hide it from another org', async ({ browser }) => {
    test.setTimeout(300_000);
    // 1. the preparer opens the page
    const preparer = await as(browser, s.preparer, PAGE);
    const page = preparer.page;
    await expect(page.locator('#ec-help')).toContainText('unverified');
    await expect(page.locator('#ec-system')).toHaveAttribute('data-system', 'syn-dib-01');
    await expect(page.locator('#ec-collectors')).toContainText('AWS Config');
    await expect(page.locator('#ec-collectors')).toContainText('SCIM');
    await expect(page.locator('#ec-collectors')).toContainText('Microsoft Graph');
    await expect(page.locator('#ec-collectors')).toContainText('Splunk');
    await expect(page.locator('#ec-collectors')).toContainText('by reference (keyring)');
    await expect(page.locator('#ec-prepared-digest')).toHaveAttribute('data-digest', s.prepared.content_digest);
    await expect(page.locator('#ec-prepared')).toContainText('waiting for a person');
    await expect(page.locator('tr[data-objective="sp800-171r2/3.1.19[b]"]')).toHaveAttribute('data-result', 'other_than_satisfied');
    await expect(page.locator('tr[data-objective="sp800-171r2/3.1.1[a]"]')).toHaveAttribute('data-result', 'satisfied');
    await expect(page.locator('tr[data-objective="sp800-53r5/au-6_obj.a"]')).toHaveAttribute('data-result', 'unverified');
    await expect(page.locator('tr[data-fact="intune:all_encrypted"]')).toContainText('false');
    await expect(page.locator('tr[data-fact="splunk:review_searches_scheduled"]')).toContainText('unverified');

    // 2. collecting from the configured placeholders is refused: nothing could be judged
    await page.fill('#ec-note', 'ent02 e2e: collect from the configured sources (synthetic)');
    await sends(page, 'POST', /^\/evidence-collectors\/api\/systems\/syn-dib-01\/prepare$/, () => page.click('#ec-prepare'));
    await expect(page.locator('#ec-prepare-msg')).toContainText('nothing_determinable', { timeout: T });

    // 3. the preparer is refused publishing their own report
    await page.fill('#ec-reason', 'ent02 e2e: my own (must be refused)');
    await sends(page, 'POST', /^\/evidence-collectors\/api\/systems\/syn-dib-01\/publish$/,
      () => page.locator('button[data-publish="publish"]').click());
    await expect(page.locator('#ec-publish-msg')).toContainText('Refused (separation)', { timeout: T });

    // 4. the publisher, another person of the org, publishes it
    const publisher = await as(browser, s.publisher, PAGE);
    const pp = publisher.page;
    await expect(pp.locator('#ec-builders')).toContainText(s.preparer.id);
    await pp.fill('#ec-reason', 'ent02 e2e: read the observations and the mapping, publish (synthetic)');
    await sends(pp, 'POST', /^\/evidence-collectors\/api\/systems\/syn-dib-01\/publish$/,
      () => pp.locator('button[data-publish="publish"]').click());

    // 5. the published report; the route returns it with its publication embedded
    await expect(pp.locator('#ec-published')).toContainText(`published by ${s.publisher.id}`, { timeout: T });
    await expect(pp.locator('#ec-decided')).toContainText('published');
    const href = await pp.locator('#ec-bundle-link').getAttribute('href');
    expect(href).toBe('/evidence-collectors/api/systems/syn-dib-01/bundle');
    const resp = await pp.request.get(href || '');
    expect(resp.ok()).toBeTruthy();
    const report = await resp.json();
    expect(report.schema).toBe('icos-objective-evidence/v1');
    expect(report.system).toBe('syn-dib-01');
    expect(report.content_digest).toBe(s.prepared.content_digest);
    expect(Object.keys(report.publication).sort()).toEqual(['content_digest', 'decided_at', 'prepared_by', 'signed_by', 'step_id']);
    expect(report.publication.signed_by).toBe(s.publisher.id);
    expect(report.publication.prepared_by).toBe(s.preparer.id);
    expect(report.publication.content_digest).toBe(s.prepared.content_digest);

    // 6. the twin thread resolves to the ledger row
    await sends(pp, 'GET', /^\/evidence-collectors\/api\/twin$/, () => pp.click('#ec-thread'));
    await expect(pp.locator('#ec-thread-msg')).toContainText(`published by ${s.publisher.id}`, { timeout: T });
    await expect(pp.locator('#ec-thread-msg')).toContainText(s.prepared.content_digest.slice(0, 16));

    // 7. another organisation sees no system, and the system's route says only "no such system"
    const outsider = await as(browser, s.outsider, '/evidence-collectors/');
    await expect(outsider.page.locator('#ec-held')).toContainText('not shown', { timeout: T });
    await expect(outsider.page.locator('tr[data-system="syn-dib-01"]')).toHaveCount(0);
    const hidden = await outsider.page.request.get('/evidence-collectors/api/systems/syn-dib-01');
    expect(hidden.status()).toBe(404);
    expect((await hidden.json()).error).toBe('no such system');

    for (const p of [preparer, publisher, outsider]) await p.ctx.close();
  });

  for (const theme of ['dark', 'light']) {
    test(`the evidence collectors page in the ${theme} theme has no axe violations`, async ({ browser }) => {
      const { ctx, page } = await as(browser, s.publisher, PAGE, theme);
      await expect(page.locator('html')).toHaveAttribute('data-theme', theme);
      await expect(page.locator('#ec-system')).toBeVisible({ timeout: T });
      await page.addScriptTag({ path: AXE });
      const result = await page.evaluate(async () => {
        // @ts-expect-error axe is injected above
        const r = await window.axe.run(document.querySelector('.ec-wrap'),
          { runOnly: { type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'] } });
        return r.violations.map((v: { id: string; nodes: { target: string[]; failureSummary?: string }[] }) =>
          `${v.id}: ${v.nodes.slice(0, 3).map(n => `${n.target.join(' ')} [${(n.failureSummary || '').split('\n').pop()}]`).join('; ')}`);
      });
      expect(result, `axe violations on /evidence-collectors/ in the ${theme} theme`).toEqual([]);
      await ctx.close();
    });
  }
});

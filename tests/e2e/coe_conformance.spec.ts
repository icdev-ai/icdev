// CUI // SP-CTI
// E2E: standards scorecards with delivery evidence, and a conformance bundle published by a person (icx-coe-03).
//
// Two fresh, card-named people (tests/e2e/fixtures/coe03_seed.py, synthetic): coe03-preparer and coe03-publisher,
// each in its own browser context (its own session), each signed in by its own dashboard API key. Both hold the
// role that publishes, so what stops the preparer publishing their own bundle on the page is separation of duties.
// The seed also leaves ONE exemption on e2e-spec that was granted 40 days ago for 30 days (an injected clock in the
// fixture, never a bypass in the product), and synthetic delivery sources dated from today: ICDEV[IT]'s git, CI and
// board, ICDEV[OS]'s git only, nothing for ICDEV[FT]. The server reads them (ICDEV_COE_SOURCES_DIR, no live git).
// The journey, every write pressed on the page:
//
//   1. the preparer opens the scorecards (ICDEV[IT] first);
//   2. a standard passes by machine, a manual one shows "needs evidence", the expired exemption fails its standard;
//   3. delivery evidence with sample sizes; ICDEV[OS]'s change failure rate is unverified (no CI snapshot), and every
//      ICDEV[FT] metric is unverified (no source);
//   4. the preparer prepares ICDEV[IT]'s bundle and is refused publishing it; the publisher publishes it;
//   5. the published bundle shows with its digest, and the bundle route returns it with its publication;
//   6. the twin observes the bundles and a finding's thread opens on its ledger row;
//   7. the publisher revokes the lapsed exemption on the exemptions page (a stop, with a reason), so the scorecard's
//      exemption gate is back to pass for the specs that run after this one.
//
// Then the page in both themes (dark, light) with axe (the vendored axe-core) over its content.

import { test, expect } from './fixtures/observed_calls';
import type { Browser, BrowserContext, Page, Request } from '@playwright/test';
import { spawnSync } from 'child_process';
import path from 'path';
import { PYTHON, icdevSubprocessEnv } from './fixtures/subprocess_env';

const ROOT = path.resolve(__dirname, '../..');
const SEED = path.join(ROOT, 'tests/e2e/fixtures/coe03_seed.py');
const AXE = path.join(ROOT, 'tools/testing/vendor/axe/axe.min.js');
const T = 60_000;

type Person = { id: string; name: string; role: string; key: string };
type Seeded = { run: string; preparer: Person; publisher: Person;
  expired: { exemption_key: string; ref: string; component: string; expires_on: string };
  sources: { it: { deployments: number; observed: number; rework_sample: number }; os: { deployments: number } } };

function seed(): Seeded {
  // The seeder writes to the database the RUN asked for, never an ambient one.
  const res = spawnSync(PYTHON, [SEED], {
    cwd: ROOT, encoding: 'utf8', env: icdevSubprocessEnv({ PYTHONIOENCODING: 'utf-8' }),
  });
  if (res.status !== 0) throw new Error(`coe03_seed.py failed: ${res.stderr || res.stdout}`);
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

async function repo(page: Page, key: string) {
  await sends(page, 'GET', new RegExp(`^/idp/api/conformance/${key}$`),
    () => page.locator(`[data-repo="${key}"]`).click());
  await expect(page.locator('#cf-digest')).toBeVisible({ timeout: T });
  await expect(page.locator(`[data-repo="${key}"]`)).toHaveAttribute('aria-pressed', 'true');
}

test.describe('Standards scorecards, delivery evidence and a published conformance bundle (icx-coe-03)', () => {
  test.describe.configure({ mode: 'serial' });
  let s: Seeded;

  test.beforeAll(() => { s = seed(); });
  test.afterAll(() => {
    // The same stop the journey takes, in case it did not get there: never leave a lapsed exemption failing the gate.
    if (!s) return;
    spawnSync(PYTHON, [SEED, '--revoke', s.expired.exemption_key, '--by', s.publisher.id], {
      cwd: ROOT, encoding: 'utf8', env: icdevSubprocessEnv({ PYTHONIOENCODING: 'utf-8' }),
    });
  });

  test('score, see evidence and unverified, prepare, refuse the preparer, publish, see the bundle, open the twin thread', async ({ browser }) => {
    test.setTimeout(300_000);
    // 1. the preparer opens the scorecards
    const preparer = await as(browser, s.preparer, '/idp/conformance');
    await expect(preparer.page.locator('#cf-help')).toContainText('needs evidence');
    await expect(preparer.page.locator('#cf-digest')).toBeVisible({ timeout: T });
    await expect(preparer.page.locator('[data-repo="it"]')).toHaveAttribute('aria-pressed', 'true');

    // 2. a standard passes by machine, a manual one needs evidence, the expired exemption fails its standard
    const page = preparer.page;
    await expect(page.locator('tr[data-std="ENG-PAGE-COMPLETENESS"]')).toHaveAttribute('data-status', 'pass');
    await expect(page.locator('tr[data-std="ENG-PAGE-COMPLETENESS"]')).toContainText('by machine');
    await expect(page.locator('tr[data-std="ENG-RED-FIRST"]')).toHaveAttribute('data-status', 'needs_evidence');
    await expect(page.locator('tr[data-std="ENG-RED-FIRST"]')).toContainText('needs evidence');
    const e2e = page.locator('tr[data-std="ENG-E2E-SPEC"]');
    await expect(e2e).toHaveAttribute('data-status', 'fail');
    await expect(e2e).toContainText('an exemption expired');
    await expect(e2e).toContainText(s.expired.exemption_key);
    await expect(e2e).toContainText('expired exemption');
    await expect(page.locator('tr[data-std="ICOS-CONTROLS"]')).toHaveAttribute('data-status', 'not_scored');
    await expect(page.locator(`tr[data-ex="${s.expired.exemption_key}"]`)).toHaveAttribute('data-state', 'expired');
    await expect(page.locator('tr[data-std="ENG-PAGE-COMPLETENESS"] a')).toHaveAttribute('href', '/idp/standards#ENG-PAGE-COMPLETENESS');

    // 3. delivery evidence, with sample sizes; unverified is marked
    await expect(page.locator('tr[data-metric="deployment_frequency"]')).toHaveAttribute('data-status', 'measured');
    await expect(page.locator('tr[data-metric="deployment_frequency"] [data-sample]')).toHaveAttribute('data-sample', String(s.sources.it.deployments));
    await expect(page.locator('tr[data-metric="change_failure_rate"] [data-sample]')).toHaveAttribute('data-sample', String(s.sources.it.observed));
    await expect(page.locator('tr[data-metric="change_failure_rate"]')).toContainText('40.0%');
    await expect(page.locator('tr[data-metric="lead_time"]')).toContainText('2 h');
    await expect(page.locator('tr[data-metric="ai_rework_rate"] [data-sample]')).toHaveAttribute('data-sample', String(s.sources.it.rework_sample));
    await expect(page.locator('#cf-sources [data-source="ci"]')).not.toContainText('missing');
    await repo(page, 'os');
    await expect(page.locator('tr[data-metric="deployment_frequency"] [data-sample]')).toHaveAttribute('data-sample', String(s.sources.os.deployments));
    const cfr = page.locator('tr[data-metric="change_failure_rate"]');
    await expect(cfr).toHaveAttribute('data-status', 'unverified');
    await expect(cfr).toContainText('UNVERIFIED');
    await expect(cfr).toContainText('no CI run records');
    await expect(page.locator('tr[data-std="ICOS-INTERFACES"]')).toHaveAttribute('data-status', 'not_scored');
    await repo(page, 'ft');
    await expect(page.locator('tr[data-metric][data-status="unverified"]')).toHaveCount(5);
    await expect(page.locator('tr[data-metric="lead_time"]')).toContainText('no git history');
    await repo(page, 'it');

    // 4. the preparer prepares ICDEV[IT]'s bundle; they are refused publishing it
    await sends(page, 'POST', /^\/idp\/api\/conformance\/it\/prepare$/, () => page.click('#cf-prepare'));
    await expect(page.locator('#cf-prepare-msg')).toContainText('Prepared it bundle', { timeout: T });
    await expect(page.locator('#cf-ledger')).toContainText('waiting for a person', { timeout: T });
    const prepared = (await page.locator('#cf-digest').textContent() || '').trim();
    expect(prepared).toMatch(/^[0-9a-f]{64}$/);
    await page.fill('#cf-reason', 'coe03 e2e: my own (must be refused)');
    await sends(page, 'POST', /^\/idp\/api\/conformance\/it\/publish$/, () => page.locator('button[data-publish="publish"]').click());
    await expect(page.locator('#cf-publish-msg')).toContainText('Refused (separation)', { timeout: T });

    //    the publisher, another person, publishes it
    const publisher = await as(browser, s.publisher, '/idp/conformance');
    const pp = publisher.page;
    await expect(pp.locator('#cf-ledger')).toContainText('waiting for a person', { timeout: T });
    await expect(pp.locator('#cf-publish')).toContainText(s.preparer.id);
    await pp.fill('#cf-reason', 'coe03 e2e: read the bundle, publish it (synthetic)');
    await sends(pp, 'POST', /^\/idp\/api\/conformance\/it\/publish$/, () => pp.locator('button[data-publish="publish"]').click());
    await expect(pp.locator('#cf-publish-msg')).toContainText(`it bundle published by ${s.publisher.id}`, { timeout: T });

    // 5. the bundle produced, with its digest; the route returns it with its publication
    await expect(pp.locator('#cf-published-digest')).toHaveText(prepared, { timeout: T });
    await expect(pp.locator('#cf-published-bundle')).toContainText('icos-conformance/v1');
    await expect(pp.locator('#cf-ledger')).toContainText('Newest published', { timeout: T });
    const href = await pp.locator('#cf-bundle-link').getAttribute('href');
    expect(href).toBe('/idp/api/conformance/it/bundle');
    const resp = await pp.request.get(href || '');
    expect(resp.ok()).toBeTruthy();
    const bundle = await resp.json();
    expect(bundle.schema).toBe('icos-conformance/v1');
    expect(bundle.content_digest).toBe(prepared);
    expect(bundle.publication.signed_by).toBe(s.publisher.id);
    expect(bundle.publication.prepared_by).toBe(s.preparer.id);
    expect(bundle.publication.template).toBe('decl-wft-coe-conformance-publish');

    // 6. the twin observes the bundles; a finding's thread opens on its ledger row
    await sends(pp, 'GET', /^\/idp\/api\/standards-twin$/, () => pp.click('#twin-observe'));
    await expect(pp.locator('#cf-twin-verdict')).toBeVisible({ timeout: T });
    const finding = pp.locator('li[data-rule="coe.conformance.standard_failing"]').first();
    await expect(finding).toContainText('it: ENG-E2E-SPEC fails');
    await finding.locator('button[data-thread]').click();
    await expect(pp.locator('#cf-thread-resolved')).toContainText('coe_conformance_events', { timeout: T });
    await expect(pp.locator('#cf-thread-resolved')).toContainText(`published by ${s.publisher.id}`);
    await expect(pp.locator('#cf-thread-resolved')).toContainText(prepared.slice(0, 16));

    // 7. the lapsed exemption is revoked on the exemptions page: a stop, with a reason; the gate is back to pass
    await pp.goto('/idp/exemptions');
    await pp.locator(`tr[data-key="${s.expired.exemption_key}"] button[data-open]`).click();
    await expect(pp.locator('#ex-detail-title')).toContainText(s.expired.exemption_key, { timeout: T });
    await pp.fill('#ex-reason', 'coe03 e2e: lapsed, withdrawn (synthetic)');
    await sends(pp, 'POST', /^\/idp\/api\/exemptions\/[0-9a-f]{16}\/revoke$/,
      () => pp.locator('button[data-step="revoke"][data-decision="revoke"]').click());
    await expect(pp.locator('#ex-step-msg')).toContainText('revoked', { timeout: T });
    await pp.goto('/idp/conformance');
    await expect(pp.locator('tr[data-std="ENG-E2E-SPEC"]')).not.toContainText('an exemption expired', { timeout: T });

    for (const p of [preparer, publisher]) await p.ctx.close();
  });

  for (const theme of ['dark', 'light']) {
    test(`the standards scorecards page in the ${theme} theme has no axe violations`, async ({ browser }) => {
      const { ctx, page } = await as(browser, s.publisher, '/idp/conformance', theme);
      await expect(page.locator('html')).toHaveAttribute('data-theme', theme);
      await expect(page.locator('#cf-digest')).toBeVisible({ timeout: T });
      await page.click('#twin-observe');
      await expect(page.locator('#cf-twin-verdict')).toBeVisible({ timeout: T });
      await page.addScriptTag({ path: AXE });
      const result = await page.evaluate(async () => {
        // @ts-expect-error axe is injected above
        const r = await window.axe.run(document.querySelector('.std-wrap'),
          { runOnly: { type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'] } });
        return r.violations.map((v: { id: string; nodes: { target: string[]; failureSummary?: string }[] }) =>
          `${v.id}: ${v.nodes.slice(0, 3).map(n => `${n.target.join(' ')} [${(n.failureSummary || '').split('\n').pop()}]`).join('; ')}`);
      });
      expect(result, `axe violations on /idp/conformance in the ${theme} theme`).toEqual([]);
      await ctx.close();
    });
  }
});

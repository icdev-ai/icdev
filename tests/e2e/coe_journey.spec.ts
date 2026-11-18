// CUI // SP-CTI
// E2E: the Engineering CoE block, end to end through the console (osx-coe-02, the CoE journey card).
//
// ONE scenario across the four CoE pages, every decision by a fresh, card-named test person
// (tests/e2e/fixtures/coe_journey_seed.py, synthetic), each in its own browser context signed in by its own key:
// coe02j-author, coe02j-steward, coe02j-requester and coe02j-approver. All hold the role that takes every step, so
// what stops a person deciding their own proposal, request or bundle on the page is separation of duties.
//
//   1. /idp/standards: the author proposes a standard checked by machine (the IDP scorecard rule
//      component-readiness/iqe-adapter) and is refused reviewing it; the steward reviews it and signs it into force.
//   2. /idp/golden-paths: the `service` path is behind it; the author proposes a version that declares it, its drift
//      check runs on the page and passes (the new standard by machine); the author is refused publishing it; the
//      steward publishes it, and the path meets every standard in force again.
//   3. /idp/exemptions: the requester asks to waive the standard for one component for 30 days and is refused
//      granting it; the approver grants it; the look-ahead past its expiry shows it expired and the gate failed.
//      A browser cannot let time pass, so a second exemption of the standard, granted 40 days ago for 30 by the same
//      two people, is seeded through the product's own functions under an injected clock: it has lapsed TODAY.
//   4. /idp/conformance: the standard is scored for ICDEV[IT] and fails, naming the lapsed exemption; it is listed
//      not applicable for ICDEV[OS] and ICDEV[FT]. For each repo the author prepares the bundle (refused publishing
//      it, once) and the approver publishes it; the published bundle carries its publication.
//
// Then each page in both themes (dark, light) with axe (the vendored axe-core) over its content. ICDEV[OS]'s side of
// the journey (import, the twin, the coe-finding step) is ICDEV[OS]'s e2e (ui/e2e, osx-coe-02).

import { test, expect } from './fixtures/observed_calls';
import type { Browser, BrowserContext, Page, Request } from '@playwright/test';
import { spawnSync } from 'child_process';
import path from 'path';
import { PYTHON, icdevSubprocessEnv } from './fixtures/subprocess_env';

const ROOT = path.resolve(__dirname, '../..');
const SEED = path.join(ROOT, 'tests/e2e/fixtures/coe_journey_seed.py');
const AXE = path.join(ROOT, 'tools/testing/vendor/axe/axe.min.js');
const T = 90_000;

type Person = { id: string; name: string; role: string; key: string };
type Seeded = { run: string; author: Person; steward: Person; requester: Person; approver: Person;
  standard: string; scorecard: string; rule: string; component: string; version: string };

function py(args: string[]): string {
  // The seeder writes to the database the RUN asked for, never an ambient one.
  const res = spawnSync(PYTHON, [SEED, ...args], {
    cwd: ROOT, encoding: 'utf8', env: icdevSubprocessEnv({ PYTHONIOENCODING: 'utf-8' }),
  });
  if (res.status !== 0) throw new Error(`coe_journey_seed.py ${args.join(' ')} failed: ${res.stderr || res.stdout}`);
  return res.stdout.trim().split('\n').pop() || '{}';
}

function day(offset: number): string {
  const d = new Date();
  d.setUTCDate(d.getUTCDate() + offset);
  return d.toISOString().slice(0, 10);
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

async function stdStep(page: Page, id: string, kind: string, decision: string, reason: string) {
  await page.fill('#std-reason', reason);
  return sends(page, 'POST', new RegExp(`^/idp/api/standards/${id}/${kind}$`),
    () => page.locator(`button[data-step="${kind}"][data-decision="${decision}"]`).click());
}

async function openStd(page: Page, id: string) {
  await page.locator(`tr[data-std="${id}"] button[data-open]`).click();
  await expect(page.locator('#std-detail-title')).toContainText(id, { timeout: T });
}

async function openPath(page: Page, id: string) {
  await sends(page, 'GET', new RegExp(`^/idp/api/golden-paths/${id}$`), () => page.locator(`[data-open="${id}"]`).click());
  await expect(page.locator('#gp-detail-title')).toContainText(`Golden path ${id}`, { timeout: T });
}

async function exStep(page: Page, kind: string, decision: string, reason: string) {
  await page.fill('#ex-reason', reason);
  return sends(page, 'POST', new RegExp(`^/idp/api/exemptions/[0-9a-f]{16}/${kind}$`),
    () => page.locator(`button[data-step="${kind}"][data-decision="${decision}"]`).click());
}

async function repo(page: Page, key: string) {
  await sends(page, 'GET', new RegExp(`^/idp/api/conformance/${key}$`), () => page.locator(`[data-repo="${key}"]`).click());
  await expect(page.locator('#cf-digest')).toBeVisible({ timeout: T });
  await expect(page.locator(`[data-repo="${key}"]`)).toHaveAttribute('aria-pressed', 'true');
}

async function axeClean(page: Page, label: string) {
  await page.addScriptTag({ path: AXE });
  const result = await page.evaluate(async () => {
    // @ts-expect-error axe is injected above
    const r = await window.axe.run(document.querySelector('.std-wrap'),
      { runOnly: { type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'] } });
    return r.violations.map((v: { id: string; nodes: { target: string[]; failureSummary?: string }[] }) =>
      `${v.id}: ${v.nodes.slice(0, 3).map(n => `${n.target.join(' ')} [${(n.failureSummary || '').split('\n').pop()}]`).join('; ')}`);
  });
  expect(result, `axe violations on ${label}`).toEqual([]);
}

test.describe('The Engineering CoE, end to end: standard, golden path, exemption, bundles (osx-coe-02)', () => {
  test.describe.configure({ mode: 'serial' });
  let s: Seeded;

  test.beforeAll(() => { s = JSON.parse(py([])); });
  test.afterAll(() => {
    // Revoke the run's exemptions, deprecate its standard and refuse anything a failed run left open.
    if (s) py(['--cleanup', s.run, '--by', s.steward.id]);
  });

  test('propose and adopt a standard, bring a golden path up to it, waive it until it lapses, publish the bundles', async ({ browser }) => {
    test.setTimeout(600_000);
    const STD = s.standard;

    // 1. the author proposes the standard, with a machine check; the author may not review it; the steward adopts it
    const author = await as(browser, s.author, '/idp/standards');
    const ap = author.page;
    await expect(ap.locator('#std-help')).toContainText('A standard without a check is a wish');
    await ap.fill('#p-id', STD);
    await ap.fill('#p-version', '1.0');
    await ap.fill('#p-title', "Every service's data is queryable through an IQE adapter (coe02j e2e)");
    await ap.selectOption('#p-owner', 'developer');
    await ap.fill('#p-scope', 'Every ICDEV[IT] service (synthetic: the osx-coe-02 browser journey only).');
    await ap.fill('#p-sources', 'args/scorecards/component-readiness.yaml');
    await ap.selectOption('#p-check', 'scorecard_rule');
    await ap.fill('#p-scorecard', s.scorecard);
    await ap.fill('#p-rule', s.rule);
    await ap.fill('#p-note', 'coe02j e2e: please review (synthetic)');
    await sends(ap, 'POST', /^\/idp\/api\/standards\/propose$/, () => ap.click('#p-submit'));
    await expect(ap.locator('#std-propose-msg')).toContainText(`Proposed ${STD} 1.0 as a draft`, { timeout: T });
    await expect(ap.locator('#std-detail-title')).toContainText(STD, { timeout: T });
    await stdStep(ap, STD, 'review', 'endorse', 'coe02j e2e: my own draft (must be refused)');
    await expect(ap.locator('#std-step-msg')).toContainText('Refused (separation)', { timeout: T });

    const steward = await as(browser, s.steward, '/idp/standards');
    const sp = steward.page;
    await openStd(sp, STD);
    await stdStep(sp, STD, 'review', 'endorse', 'coe02j e2e: the check resolves to an existing scorecard rule');
    await expect(sp.locator('#std-step-msg')).toContainText(`${STD} 1.0 is now reviewed`, { timeout: T });
    await stdStep(sp, STD, 'adopt', 'sign', 'coe02j e2e: signed into force at its digest');
    await expect(sp.locator('#std-step-msg')).toContainText(`${STD} 1.0 is now adopted`, { timeout: T });
    await expect(sp.locator('#std-detail-body')).toContainText('in force: 1.0', { timeout: T });
    await expect(sp.locator('#std-detail-body')).toContainText('adopted by ' + s.steward.id);

    // 2. the `service` golden path falls behind it; a new version declares it, passes its drift check, is published
    await ap.goto('/idp/golden-paths');
    await expect(ap.locator('tr[data-path="service"]')).toHaveAttribute('data-behind', 'true', { timeout: T });
    await expect(ap.locator('tr[data-path="service"]')).toContainText(STD);
    await openPath(ap, 'service');
    await expect(ap.locator('#gp-behind')).toContainText('behind the standards in force');
    await expect(ap.locator('#gp-satisfies')).toHaveValue(new RegExp(`${STD}@1\\.0`));
    await ap.fill('#gp-version', s.version);
    await ap.fill('#gp-note', 'coe02j e2e: declares the new standard (synthetic)');
    await sends(ap, 'POST', /^\/idp\/api\/golden-paths\/propose$/, () => ap.click('#gp-propose'));
    await expect(ap.locator('#gp-propose-msg')).toContainText(`Proposed service ${s.version}`, { timeout: T });
    await expect(ap.locator('#gp-drift-msg')).toContainText(`service ${s.version}: pass`, { timeout: T });
    await expect(ap.locator(`tr[data-version="${s.version}"]`)).toHaveAttribute('data-state', 'proposed', { timeout: T });
    await expect(ap.locator(`tr[data-version="${s.version}"]`)).toContainText('pass');
    await ap.fill('#gp-reason', 'coe02j e2e: my own version (must be refused)');
    await sends(ap, 'POST', /^\/idp\/api\/golden-paths\/service\/publish$/,
      () => ap.locator('button[data-step="publish"][data-decision="publish"]').click());
    await expect(ap.locator('#gp-step-msg')).toContainText('Refused (separation)', { timeout: T });

    await sp.goto('/idp/golden-paths');
    await openPath(sp, 'service');
    await sp.fill('#gp-reason', 'coe02j e2e: read the version and its passing drift check; publish it');
    await sends(sp, 'POST', /^\/idp\/api\/golden-paths\/service\/publish$/,
      () => sp.locator('button[data-step="publish"][data-decision="publish"]').click());
    await expect(sp.locator('#gp-step-msg')).toContainText(`service ${s.version} published by ${s.steward.id}`, { timeout: T });
    await expect(sp.locator('#gp-behind')).toContainText('meets every standard in force', { timeout: T });
    await expect(sp.locator(`tr[data-std="${STD}"]`)).toHaveAttribute('data-status', 'pass', { timeout: T });
    await expect(sp.locator(`tr[data-std="${STD}"]`)).toContainText('by machine');

    // 3. an exemption of the standard: requested, refused to its requester, granted, and expired in the look-ahead
    const requester = await as(browser, s.requester, '/idp/exemptions');
    const rp = requester.page;
    await expect(rp.locator('#ex-help')).toContainText('Every exemption expires');
    const key = `${s.scorecard}|${s.rule}|${s.component}`;
    await rp.selectOption('#r-scorecard', s.scorecard);
    await rp.selectOption('#r-scope', 'standard');
    await rp.fill('#r-scope-ref', STD);
    await rp.selectOption('#r-rule', s.rule);
    await rp.fill('#r-component', s.component);
    await rp.fill('#r-reason', 'coe02j e2e: no IQE adapter yet; 30 days while one is written (synthetic)');
    await rp.fill('#r-expires', day(30));
    await sends(rp, 'POST', /^\/idp\/api\/exemptions\/request$/, () => rp.click('#r-submit'));
    await expect(rp.locator('#ex-request-msg')).toContainText(`Requested ${key}`, { timeout: T });
    await expect(rp.locator('#ex-standards')).toContainText(STD);
    await exStep(rp, 'decide', 'grant', 'coe02j e2e: my own request (must be refused)');
    await expect(rp.locator('#ex-step-msg')).toContainText('Refused (separation)', { timeout: T });

    const approver = await as(browser, s.approver, '/idp/exemptions');
    const xp = approver.page;
    await xp.locator(`tr[data-key="${key}"] button[data-open]`).click();
    await expect(xp.locator('#ex-detail-title')).toContainText(key, { timeout: T });
    await exStep(xp, 'decide', 'grant', 'coe02j e2e: read at its digest; 30 days, then it lapses');
    await expect(xp.locator('#ex-step-msg')).toContainText('approved, now approved', { timeout: T });
    await expect(xp.locator(`tr[data-key="${key}"]`)).toHaveAttribute('data-state', 'active', { timeout: T });
    await xp.fill('#ex-asof', day(31));
    await xp.click('#ex-asof-go');
    await expect(xp.locator(`tr[data-key="${key}"]`)).toHaveAttribute('data-state', 'expired', { timeout: T });
    await expect(xp.locator('#ex-gate')).toHaveAttribute('data-gate', 'fail');

    // the one a browser cannot make: granted 40 days ago for 30 (the product's functions, an injected clock)
    const lapsed = JSON.parse(py(['--lapse', s.run, '--requester', s.requester.id, '--approver', s.approver.id]));
    await xp.click('#ex-asof-today');
    await expect(xp.locator(`tr[data-key="${key}"]`)).toHaveAttribute('data-state', 'active', { timeout: T });
    await expect(xp.locator(`tr[data-key="${lapsed.exemption_key}"]`)).toHaveAttribute('data-state', 'expired', { timeout: T });
    await expect(xp.locator('#ex-gate')).toHaveAttribute('data-gate', 'fail');

    // 4. the standard scored across the repos; each repo's bundle prepared by one person and published by another
    await ap.goto('/idp/conformance');
    await expect(ap.locator('#cf-digest')).toBeVisible({ timeout: T });
    const it = ap.locator(`tr[data-std="${STD}"]`);
    await expect(it).toHaveAttribute('data-status', 'fail', { timeout: T });
    await expect(it).toContainText('an exemption expired');
    await expect(it).toContainText(lapsed.exemption_key);
    await expect(ap.locator(`tr[data-ex="${lapsed.exemption_key}"]`)).toHaveAttribute('data-state', 'expired');
    await expect(ap.locator(`tr[data-ex="${key}"]`)).toHaveAttribute('data-state', 'active');
    for (const r of ['os', 'ft']) {
      await repo(ap, r);
      await expect(ap.locator(`tr[data-std="${STD}"]`)).toHaveAttribute('data-status', 'not_applicable');
    }
    await xp.goto('/idp/conformance');
    for (const r of ['it', 'os', 'ft']) {
      await repo(ap, r);
      await sends(ap, 'POST', new RegExp(`^/idp/api/conformance/${r}/prepare$`), () => ap.click('#cf-prepare'));
      await expect(ap.locator('#cf-prepare-msg')).toContainText(`Prepared ${r} bundle`, { timeout: T });
      const prepared = (await ap.locator('#cf-digest').textContent() || '').trim();
      expect(prepared).toMatch(/^[0-9a-f]{64}$/);
      if (r === 'it') {
        await ap.fill('#cf-reason', 'coe02j e2e: my own bundle (must be refused)');
        await sends(ap, 'POST', /^\/idp\/api\/conformance\/it\/publish$/, () => ap.locator('button[data-publish="publish"]').click());
        await expect(ap.locator('#cf-publish-msg')).toContainText('Refused (separation)', { timeout: T });
      }
      await repo(xp, r);
      await expect(xp.locator('#cf-publish')).toContainText(s.author.id, { timeout: T });
      await xp.fill('#cf-reason', `coe02j e2e: read the ${r} bundle at its digest; publish it to ICDEV[OS]`);
      await sends(xp, 'POST', new RegExp(`^/idp/api/conformance/${r}/publish$`), () => xp.locator('button[data-publish="publish"]').click());
      await expect(xp.locator('#cf-publish-msg')).toContainText(`${r} bundle published by ${s.approver.id}`, { timeout: T });
      await expect(xp.locator('#cf-published-digest')).toHaveText(prepared, { timeout: T });
      const resp = await xp.request.get(`/idp/api/conformance/${r}/bundle`);
      expect(resp.ok()).toBeTruthy();
      const bundle = await resp.json();
      expect(bundle.schema).toBe('icos-conformance/v1');
      expect(bundle.content_digest).toBe(prepared);
      expect(bundle.publication.signed_by).toBe(s.approver.id);
      expect(bundle.publication.prepared_by).toBe(s.author.id);
      expect(bundle.standards.find((x: { id: string }) => x.id === STD).status).toBe(r === 'it' ? 'fail' : 'not_applicable');
      if (r === 'it') {
        expect(bundle.exemptions.map((x: { exemption_key: string }) => x.exemption_key)).toContain(lapsed.exemption_key);
      }
    }

    for (const p of [author, steward, requester, approver]) await p.ctx.close();
  });

  for (const theme of ['dark', 'light']) {
    test(`the four CoE pages in the ${theme} theme have no axe violations`, async ({ browser }) => {
      const { ctx, page } = await as(browser, s.steward, '/idp/standards', theme);
      await expect(page.locator('html')).toHaveAttribute('data-theme', theme);
      await openStd(page, s.standard);
      await axeClean(page, `/idp/standards (${theme})`);
      await page.goto('/idp/golden-paths');
      await expect(page.locator('#gp-detail-title')).toContainText('Golden path', { timeout: T });
      await axeClean(page, `/idp/golden-paths (${theme})`);
      await page.goto('/idp/exemptions');
      await expect(page.locator('#ex-gate')).toBeVisible({ timeout: T });
      await axeClean(page, `/idp/exemptions (${theme})`);
      await page.goto('/idp/conformance');
      await expect(page.locator('#cf-digest')).toBeVisible({ timeout: T });
      await axeClean(page, `/idp/conformance (${theme})`);
      await ctx.close();
    });
  }
});

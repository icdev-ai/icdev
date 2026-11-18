// CUI // SP-CTI
// E2E: exemptions that expire, and ADRs as records, end to end through the console (icx-coe-02).
//
// Three fresh, card-named people (tests/e2e/fixtures/coe02_seed.py, synthetic): coe02-requester,
// coe02-approver and coe02-architect, each in its own browser context (its own session), each
// signed in by its own dashboard API key. All three hold the role that grants and accepts, so what
// stops the requester and the architect deciding their own on the page is separation of duties.
// The seed also leaves ONE exemption that was granted 40 days ago for 30 days (an injected clock in
// the fixture, never a bypass in the product), so a real lapse is on the page. The journey, every
// write pressed on the page:
//
//   1. the requester requests an exemption with an expiry (rule e2e-spec, which ENG-E2E-SPEC checks);
//   2. the requester is refused when trying to grant their own request;
//   3. the approver grants it;
//   4. it shows as active;
//   5. past its expiry: the page's look-ahead judges it as of the day after it lapses (expired, the
//      gate fails), and the seeded exemption has really lapsed today;
//   6. the page and the scorecard show the failure; then the requester asks for a renewal of the
//      active one and the approver renews it, and the lapsed one is revoked (a stop, with a reason);
//   7. the architect proposes an ADR linked to a standard and a requirement;
//   8. the architect is refused accepting their own ADR, and the approver accepts it;
//   9. the architect proposes its successor, the approver accepts it and supersedes the first.
//
// Then the twin observes the exemption log and a finding's thread resolves to its row, and both
// pages in both themes (dark, light) with axe (the vendored axe-core) over their content.

import { test, expect } from './fixtures/observed_calls';
import type { Browser, BrowserContext, Page, Request } from '@playwright/test';
import { spawnSync } from 'child_process';
import path from 'path';
import { PYTHON, icdevSubprocessEnv } from './fixtures/subprocess_env';

const ROOT = path.resolve(__dirname, '../..');
const SEED = path.join(ROOT, 'tests/e2e/fixtures/coe02_seed.py');
const AXE = path.join(ROOT, 'tools/testing/vendor/axe/axe.min.js');
const T = 30_000;

type Person = { id: string; name: string; role: string; key: string };
type Seeded = { run: string; requester: Person; approver: Person; architect: Person;
  expired: { exemption_key: string; ref: string; expires_on: string } };

function seed(): Seeded {
  // The seeder writes to the database the RUN asked for, never an ambient one.
  const res = spawnSync(PYTHON, [SEED], {
    cwd: ROOT, encoding: 'utf8', env: icdevSubprocessEnv({ PYTHONIOENCODING: 'utf-8' }),
  });
  if (res.status !== 0) throw new Error(`coe02_seed.py failed: ${res.stderr || res.stdout}`);
  return JSON.parse(res.stdout.trim().split('\n').pop() || '{}');
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

async function openEx(page: Page, key: string) {
  await page.locator(`tr[data-key="${key}"] button[data-open]`).click();
  await expect(page.locator('#ex-detail-title')).toContainText(key, { timeout: T });
}

async function exStep(page: Page, kind: string, decision: string, reason: string) {
  await page.fill('#ex-reason', reason);
  return sends(page, 'POST', new RegExp(`^/idp/api/exemptions/[0-9a-f]{16}/${kind}$`),
    () => page.locator(`button[data-step="${kind}"][data-decision="${decision}"]`).click());
}

async function openAdr(page: Page, id: string) {
  await page.fill('#adr-q', id);
  await page.locator(`tr[data-adr="${id}"] button[data-open]`).click();
  await expect(page.locator('#adr-detail-title')).toContainText(id, { timeout: T });
}

async function adrStep(page: Page, id: string, kind: string, decision: string, reason: string) {
  await page.fill('#adr-reason', reason);
  return sends(page, 'POST', new RegExp(`^/idp/api/adrs/${id}/${kind}$`),
    () => page.locator(`button[data-step="${kind}"][data-decision="${decision}"]`).click());
}

async function proposeAdr(page: Page, id: string, title: string, supersedes = '') {
  await page.fill('#a-id', id);
  await page.fill('#a-version', '1');
  await page.fill('#a-title', title);
  await page.fill('#a-section', 'icx-coe-02 e2e (synthetic)');
  await page.fill('#a-context', 'Synthetic: the icx-coe-02 browser journey only.');
  await page.fill('#a-decision', `Synthetic decision ${id}.`);
  await page.fill('#a-consequences', 'None outside this journey.');
  await page.fill('#a-supersedes', supersedes);
  await page.fill('#a-requirements', 'PM-COE-08');
  await page.fill('#a-standards', 'ENG-E2E-SPEC');
  await page.fill('#a-note', 'coe02 e2e: please review (synthetic)');
  await sends(page, 'POST', /^\/idp\/api\/adrs\/propose$/, () => page.click('#a-submit'));
  await expect(page.locator('#adr-propose-msg')).toContainText(`Proposed ${id} version 1 as a draft`, { timeout: T });
}

test.describe('Exemptions that expire and ADRs as records, end to end (icx-coe-02)', () => {
  test.describe.configure({ mode: 'serial' });
  let s: Seeded;

  test.beforeAll(() => { s = seed(); });

  test('request, refuse the requester, grant, expire, fail, renew; propose, accept, supersede an ADR', async ({ browser }) => {
    test.setTimeout(240_000);
    const component = `coe02-e2e-${s.run}`;
    const key = `component-readiness|e2e-spec|${component}`;
    const expires = day(60);
    const requester = await as(browser, s.requester, '/idp/exemptions');
    await expect(requester.page.locator('#ex-help')).toContainText('Every exemption expires');

    // 1. the requester requests an exemption with an expiry
    await requester.page.selectOption('#r-scorecard', 'component-readiness');
    await requester.page.selectOption('#r-scope', 'rule');
    await requester.page.selectOption('#r-rule', 'e2e-spec');
    await requester.page.fill('#r-component', component);
    await requester.page.fill('#r-reason', 'coe02 e2e: headless for the journey (synthetic)');
    await requester.page.fill('#r-expires', '');
    await sends(requester.page, 'POST', /^\/idp\/api\/exemptions\/request$/, () => requester.page.click('#r-submit'));
    await expect(requester.page.locator('#ex-request-msg')).toContainText('no_expiry', { timeout: T });
    await requester.page.fill('#r-expires', expires);
    await sends(requester.page, 'POST', /^\/idp\/api\/exemptions\/request$/, () => requester.page.click('#r-submit'));
    await expect(requester.page.locator('#ex-request-msg')).toContainText(`Requested ${key}`, { timeout: T });
    await expect(requester.page.locator('#ex-detail-title')).toContainText(key, { timeout: T });
    await expect(requester.page.locator('#ex-standards')).toContainText('ENG-E2E-SPEC');

    // 2. the requester is refused when trying to grant their own request
    await exStep(requester.page, 'decide', 'grant', 'coe02 e2e: my own (must be refused)');
    await expect(requester.page.locator('#ex-step-msg')).toContainText('Refused (separation)', { timeout: T });

    // 3. the approver grants it
    const approver = await as(browser, s.approver, '/idp/exemptions');
    await openEx(approver.page, key);
    await exStep(approver.page, 'decide', 'grant', 'coe02 e2e: confirmed headless (synthetic)');
    await expect(approver.page.locator('#ex-step-msg')).toContainText('approved, now approved', { timeout: T });
    await expect(approver.page.locator('#ex-history tr[data-event="approved"]')).toContainText('step-');

    // 4. it shows as active
    await expect(approver.page.locator(`tr[data-key="${key}"]`)).toHaveAttribute('data-state', 'active', { timeout: T });

    // 5. past its expiry: the look-ahead (as of the day after) and the seeded lapse (today)
    await approver.page.fill('#ex-asof', day(61));
    await approver.page.click('#ex-asof-go');
    await expect(approver.page.locator('#ex-lookahead')).toContainText(day(61), { timeout: T });
    await expect(approver.page.locator(`tr[data-key="${key}"]`)).toHaveAttribute('data-state', 'expired', { timeout: T });
    await expect(approver.page.locator('#ex-gate')).toHaveAttribute('data-gate', 'fail');
    await approver.page.click('#ex-asof-today');
    await expect(approver.page.locator(`tr[data-key="${key}"]`)).toHaveAttribute('data-state', 'active', { timeout: T });
    await expect(approver.page.locator(`tr[data-key="${s.expired.exemption_key}"]`)).toHaveAttribute('data-state', 'expired');

    // 6. the page and the scorecard show it failed
    await expect(approver.page.locator('#ex-gate')).toHaveAttribute('data-gate', 'fail');
    await expect(approver.page.locator('#ex-gate')).toContainText('FAILED');
    const card = await approver.ctx.newPage();
    await card.goto('/idp/scorecards');
    await expect(card.locator('#idp-exemption-gate')).toHaveAttribute('data-gate', 'fail', { timeout: T });
    await expect(card.locator('#idp-exemption-failed')).toContainText('expired');
    await expect(card.locator('#idp-exemption-gate')).toContainText(`coe02-lapsed-${s.run}`);
    await card.close();
    //    the requester asks for a renewal of the active one; the approver renews it
    await requester.page.reload();
    await openEx(requester.page, key);
    await requester.page.fill('#ex-renew-expires', day(90));
    await exStep(requester.page, 'renewal', 'ask', 'coe02 e2e: still headless (synthetic)');
    await expect(requester.page.locator('#ex-step-msg')).toContainText('renewal_requested', { timeout: T });
    await approver.page.reload();
    await openEx(approver.page, key);
    await expect(approver.page.locator(`tr[data-key="${key}"]`)).toHaveAttribute('data-state', 'renewal_pending');
    await exStep(approver.page, 'renew', 'renew', 'coe02 e2e: renewed (synthetic)');
    await expect(approver.page.locator('#ex-step-msg')).toContainText(`(expires ${day(90)})`, { timeout: T });
    //    the lapsed one is revoked: a stop, with a reason
    await openEx(approver.page, s.expired.exemption_key);
    await exStep(approver.page, 'revoke', 'revoke', 'coe02 e2e: lapsed, withdrawn (synthetic)');
    await expect(approver.page.locator('#ex-step-msg')).toContainText('revoked', { timeout: T });
    await expect(approver.page.locator('#ex-gate')).toHaveAttribute('data-gate', 'pass', { timeout: T });

    // the twin observes the log; a finding's thread resolves to its row
    await sends(approver.page, 'GET', /^\/idp\/api\/standards-twin$/, () => approver.page.click('#twin-observe'));
    await expect(approver.page.locator('#ex-twin-verdict')).toBeVisible({ timeout: T });

    // 7. the architect proposes an ADR linked to a standard and a requirement
    const first = `D-COE02E2E-${s.run.toUpperCase()}-1`;
    const second = `D-COE02E2E-${s.run.toUpperCase()}-2`;
    const architect = await as(browser, s.architect, '/idp/adrs');
    await expect(architect.page.locator('#adr-help')).toContainText('is generated');
    await expect(architect.page.locator('#adr-doc')).toContainText('generated from the records');
    await proposeAdr(architect.page, first, 'coe02 e2e decision (synthetic)');
    await expect(architect.page.locator('#adr-links')).toContainText('PM-COE-08');
    await expect(architect.page.locator('#adr-links')).toContainText('ENG-E2E-SPEC');

    // 8. a person other than the author accepts it (the author is refused)
    await adrStep(architect.page, first, 'accept', 'accept', 'coe02 e2e: my own (must be refused)');
    await expect(architect.page.locator('#adr-step-msg')).toContainText('Refused (separation)', { timeout: T });
    const signer = await as(browser, s.approver, '/idp/adrs');
    await openAdr(signer.page, first);
    await adrStep(signer.page, first, 'accept', 'accept', 'coe02 e2e: agreed (synthetic)');
    await expect(signer.page.locator('#adr-step-msg')).toContainText(`${first} version 1 is now accepted`, { timeout: T });

    // 9. supersede it: the successor is proposed and accepted, then the first is superseded by it
    await proposeAdr(architect.page, second, 'coe02 e2e successor (synthetic)', first);
    await signer.page.reload();
    await openAdr(signer.page, second);
    await adrStep(signer.page, second, 'accept', 'accept', 'coe02 e2e: successor agreed (synthetic)');
    await expect(signer.page.locator('#adr-step-msg')).toContainText(`${second} version 1 is now accepted`, { timeout: T });
    await openAdr(signer.page, first);
    await signer.page.fill('#adr-successor', second);
    await adrStep(signer.page, first, 'supersede', 'supersede', 'coe02 e2e: replaced (synthetic)');
    await expect(signer.page.locator('#adr-step-msg')).toContainText(`${first} version 1 is now superseded`, { timeout: T });
    for (const ev of ['proposed', 'accepted', 'superseded']) {
      await expect(signer.page.locator(`#adr-history tr[data-event="${ev}"]`)).toHaveCount(1);
    }
    await expect(signer.page.locator('#adr-no-step')).toBeVisible();

    for (const p of [requester, approver, architect, signer]) await p.ctx.close();
  });

  for (const theme of ['dark', 'light']) {
    test(`both pages in the ${theme} theme have no axe violations`, async ({ browser }) => {
      for (const where of ['/idp/exemptions', '/idp/adrs']) {
        const { ctx, page } = await as(browser, s.approver, where, theme);
        await expect(page.locator('html')).toHaveAttribute('data-theme', theme);
        if (where === '/idp/exemptions') {
          await openEx(page, s.expired.exemption_key);
        } else {
          await openAdr(page, 'D-COE-2');
        }
        await page.addScriptTag({ path: AXE });
        const result = await page.evaluate(async () => {
          // @ts-expect-error axe is injected above
          const r = await window.axe.run(document.querySelector('.std-wrap'),
            { runOnly: { type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'] } });
          return r.violations.map((v: { id: string; nodes: { target: string[]; failureSummary?: string }[] }) =>
            `${v.id}: ${v.nodes.slice(0, 3).map(n => `${n.target.join(' ')} [${(n.failureSummary || '').split('\n').pop()}]`).join('; ')}`);
        });
        expect(result, `axe violations on ${where} in the ${theme} theme`).toEqual([]);
        await ctx.close();
      }
    });
  }
});

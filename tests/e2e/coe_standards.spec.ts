// CUI // SP-CTI
// E2E: the engineering standards register, end to end through the console (icx-coe-01).
//
// Two fresh, card-named people (tests/e2e/fixtures/coe01_seed.py, synthetic): coe01-author and
// coe01-steward, each in its own browser context (its own session), each signed in by its own
// dashboard API key. Both hold the steward role, so what stops the author on the page is
// separation of duties, not a missing role. The journey, every write pressed on the page:
//
//   1. the author proposes a standard with a check (an IDP scorecard rule), and is refused when
//      they try to review their own draft;
//   2. the steward reviews it (endorse);
//   3. the steward signs it into force (adopt);
//   4. the steward sees it adopted, in force, with its history (proposed, reviewed, adopted);
//   5. the author proposes a check-less standard; the steward endorses it and is REFUSED at
//      adoption: a standard without a check is a wish;
//   6. the steward deprecates the adopted standard, and the history shows it;
//   7. the twin observes the register and a finding's thread resolves to its ledger row.
//
// Then the page in both themes (dark, light) with axe (the vendored axe-core) over its content.
//
// CI's E2E job runs feature_audit.py --e2e-gate over what tests/e2e/fixtures/observed_calls.ts
// recorded: every write the audit credits to /idp/standards must be SENT by the page here.

import { test, expect } from './fixtures/observed_calls';
import type { Browser, BrowserContext, Page, Request } from '@playwright/test';
import { spawnSync } from 'child_process';
import path from 'path';
import { PYTHON, icdevSubprocessEnv } from './fixtures/subprocess_env';

const ROOT = path.resolve(__dirname, '../..');
const SEED = path.join(ROOT, 'tests/e2e/fixtures/coe01_seed.py');
const AXE = path.join(ROOT, 'tools/testing/vendor/axe/axe.min.js');
const T = 30_000;

type Person = { id: string; name: string; role: string; key: string };
type Seeded = { run: string; author: Person; steward: Person };

function seed(): Seeded {
  // The seeder writes to the database the RUN asked for, never an ambient one.
  const res = spawnSync(PYTHON, [SEED], {
    cwd: ROOT, encoding: 'utf8', env: icdevSubprocessEnv({ PYTHONIOENCODING: 'utf-8' }),
  });
  if (res.status !== 0) throw new Error(`coe01_seed.py failed: ${res.stderr || res.stdout}`);
  return JSON.parse(res.stdout.trim().split('\n').pop() || '{}');
}

/** A person's own browser context: their own session, signed in by their own key. */
async function as(browser: Browser, who: Person, theme = 'dark'): Promise<{ ctx: BrowserContext; page: Page }> {
  const ctx = await browser.newContext({ extraHTTPHeaders: { Authorization: `Bearer ${who.key}` } });
  await ctx.addInitScript((t: string) => {
    localStorage.setItem('icdev_tour_completed', '1');
    localStorage.setItem('icdev_tour_last_step', '999');
    localStorage.setItem('icdev-theme', t);
  }, theme);
  const page = await ctx.newPage();
  await page.goto('/idp/standards');
  await expect(page.locator('#std-help')).toContainText('A standard without a check is a wish');
  return { ctx, page };
}

/** Wait for the page to send `method` to a path matching `re`, while `act` runs. */
async function sends(page: Page, method: string, re: RegExp, act: () => Promise<unknown>): Promise<Request> {
  const req = page.waitForRequest(r => r.method() === method && re.test(new URL(r.url()).pathname), { timeout: T });
  await act();
  return req;
}

async function open(page: Page, id: string) {
  await page.locator(`tr[data-std="${id}"] button[data-open]`).click();
  await expect(page.locator('#std-detail-title')).toContainText(id, { timeout: T });
}

async function step(page: Page, id: string, kind: string, decision: string, reason: string) {
  await page.fill('#std-reason', reason);
  return sends(page, 'POST', new RegExp(`^/idp/api/standards/${id}/${kind}$`),
    () => page.locator(`button[data-step="${kind}"][data-decision="${decision}"]`).click());
}

async function propose(page: Page, id: string, title: string, withCheck: boolean) {
  await page.fill('#p-id', id);
  await page.fill('#p-version', '1.0');
  await page.fill('#p-title', title);
  await page.selectOption('#p-owner', 'developer');
  await page.fill('#p-scope', 'Synthetic: the icx-coe-01 browser journey only.');
  await page.fill('#p-sources', 'args/scorecards/component-readiness.yaml\ncatalog:OWASP_TOP_10');
  await page.selectOption('#p-check', withCheck ? 'scorecard_rule' : '');
  if (withCheck) {
    await page.fill('#p-scorecard', 'component-readiness');
    await page.fill('#p-rule', 'e2e-spec');
  }
  await page.fill('#p-note', 'coe01 e2e: please review (synthetic)');
  await sends(page, 'POST', /^\/idp\/api\/standards\/propose$/, () => page.click('#p-submit'));
  await expect(page.locator('#std-propose-msg')).toContainText(`Proposed ${id} 1.0 as a draft`, { timeout: T });
}

test.describe('Engineering standards register, end to end (icx-coe-01)', () => {
  let s: Seeded;
  test.beforeAll(() => { s = seed(); });

  test('propose, review, adopt, see it in force with its history, refuse a wish, deprecate', async ({ browser }) => {
    test.setTimeout(180_000);
    const STD = `ENG-COE01-E2E-${s.run.toUpperCase()}`;
    const WISH = `ENG-COE01-WISH-${s.run.toUpperCase()}`;
    const author = await as(browser, s.author);
    const steward = await as(browser, s.steward);

    // 1. the author proposes a standard with a check, and may not review their own draft
    await propose(author.page, STD, 'Every component has a browser journey (coe01 e2e)', true);
    await expect(author.page.locator('#std-detail-title')).toContainText(STD, { timeout: T });
    await expect(author.page.locator('#std-detail-body')).toContainText('May not take it: ' + s.author.id);
    await step(author.page, STD, 'review', 'endorse', 'coe01 e2e: my own draft');
    await expect(author.page.locator('#std-step-msg')).toContainText('Refused (separation)', { timeout: T });

    // 2. the steward reviews it
    await steward.page.reload();
    await open(steward.page, STD);
    await expect(steward.page.locator('#std-detail-body')).toContainText('proposed');
    await step(steward.page, STD, 'review', 'endorse', 'coe01 e2e: scope and check are sound');
    await expect(steward.page.locator('#std-step-msg')).toContainText(`${STD} 1.0 is now reviewed`, { timeout: T });

    // 3. the steward signs it into force
    await expect(steward.page.locator('button[data-step="adopt"][data-decision="sign"]')).toBeVisible({ timeout: T });
    await step(steward.page, STD, 'adopt', 'sign', 'coe01 e2e: adopted for the journey');
    await expect(steward.page.locator('#std-step-msg')).toContainText(`${STD} 1.0 is now adopted`, { timeout: T });

    // 4. it is adopted, in force, with its history
    await expect(steward.page.locator('#std-detail-body')).toContainText('in force: 1.0', { timeout: T });
    await expect(steward.page.locator('#std-detail-body')).toContainText('adopted by ' + s.steward.id);
    const history = steward.page.locator('#std-history tbody tr');
    await expect(history).toHaveCount(3);
    for (const ev of ['proposed', 'reviewed', 'adopted']) {
      await expect(steward.page.locator(`#std-history tr[data-event="${ev}"]`)).toHaveCount(1);
    }
    await expect(steward.page.locator('#std-history tr[data-event="adopted"]')).toContainText('step-');
    await expect(steward.page.locator(`tr[data-std="${STD}"]`)).toContainText('adopted');

    // 5. a check-less proposal is refused at adoption
    await propose(author.page, WISH, 'A wish with no check (coe01 e2e)', false);
    await expect(author.page.locator('#std-propose-msg')).toContainText('cannot be adopted');
    await steward.page.reload();
    await open(steward.page, WISH);
    await expect(steward.page.locator('#std-detail-body [data-wish]').first()).toBeVisible();
    await step(steward.page, WISH, 'review', 'endorse', 'coe01 e2e: endorse, to show adoption refuses it');
    await expect(steward.page.locator('#std-step-msg')).toContainText(`${WISH} 1.0 is now reviewed`, { timeout: T });
    await step(steward.page, WISH, 'adopt', 'sign', 'coe01 e2e: try to adopt a wish');
    await expect(steward.page.locator('#std-step-msg')).toContainText('Refused (no_check)', { timeout: T });
    await expect(steward.page.locator('#std-step-msg')).toContainText('a standard without a check is a wish');
    await open(steward.page, WISH);
    await expect(steward.page.locator('#std-detail-body')).toContainText('not in force');

    // 6. the steward deprecates the adopted standard
    await open(steward.page, STD);
    await steward.page.fill('#std-superseded', 'ENG-E2E-SPEC');
    await step(steward.page, STD, 'deprecate', 'deprecate', 'coe01 e2e: retired at the end of the journey');
    await expect(steward.page.locator('#std-step-msg')).toContainText(`${STD} 1.0 is now deprecated`, { timeout: T });
    await expect(steward.page.locator('#std-history tr[data-event="deprecated"]')).toHaveCount(1, { timeout: T });
    await expect(steward.page.locator('#std-history tbody tr')).toHaveCount(4);
    await expect(steward.page.locator('#std-no-step')).toBeVisible();

    // 7. the twin observes the register; a finding's thread resolves to its ledger row
    await sends(steward.page, 'GET', /^\/idp\/api\/standards-twin$/, () => steward.page.click('#twin-observe'));
    await expect(steward.page.locator('#std-twin-verdict')).toBeVisible({ timeout: T });
    await steward.page.locator('#std-twin-body button[data-thread^="coe_standard:"]').first().click();
    await expect(steward.page.locator('#std-thread')).toContainText('rests on coe_standard_events', { timeout: T });

    await author.ctx.close();
    await steward.ctx.close();
  });

  for (const theme of ['dark', 'light']) {
    test(`the page in the ${theme} theme has no axe violations`, async ({ browser }) => {
      const { ctx, page } = await as(browser, s.steward, theme);
      await expect(page.locator('html')).toHaveAttribute('data-theme', theme);
      await open(page, 'ENG-RED-FIRST');
      await page.addScriptTag({ path: AXE });
      const result = await page.evaluate(async () => {
        // @ts-expect-error axe is injected above
        const r = await window.axe.run(document.querySelector('.std-wrap'),
          { runOnly: { type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'] } });
        return r.violations.map((v: { id: string; nodes: { target: string[]; failureSummary?: string }[] }) =>
          `${v.id}: ${v.nodes.slice(0, 3).map(n => `${n.target.join(' ')} [${(n.failureSummary || '').split('\n').pop()}]`).join('; ')}`);
      });
      expect(result, `axe violations in the ${theme} theme`).toEqual([]);
      await ctx.close();
    });
  }
});

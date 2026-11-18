// CUI // SP-CTI
// E2E: FORGE Academy tracks per Community of Practice, xAPI statements, and a batch published by a person (icx-cop-01).
//
// Three fresh, card-named people (tests/e2e/fixtures/cop01a_seed.py, synthetic): cop01a-learner, cop01a-preparer and
// cop01a-publisher, each in its own browser context (its own session), each signed in by its own dashboard API key.
// The preparer and the publisher both hold the role that publishes, so what stops the preparer publishing the batch
// they prepared is separation of duties. The learner's answers come from the declared config, as a learner who read
// the cited files would give them. The journey, every write pressed on the page:
//
//   1. the learner opens a mission of the engineering path, answers one question wrong: not passed, recorded, no
//      statement; answers again, right: passed, and a `progressed` statement appears (completion false);
//   2. the learner passes every mission of the cloud path: a `completed` statement appears for their pseudonymous
//      account, on the path's activity id, and the path shows completed;
//   3. the twin observes the statements no published batch carries, and the finding's thread opens on the ledger row;
//   4. the preparer prepares the batch and is refused publishing it; the publisher, another person, publishes it;
//   5. the published export verifies on the page, and the route returns the batch, exactly what ICDEV[OS] imports,
//      carrying the learner's statements, with its publication beside it;
//   6. the twin now reads a published batch.
//
// Then the page in both themes (dark, light) with axe (the vendored axe-core) over its content, a mission open.

import { test, expect } from './fixtures/observed_calls';
import type { Browser, BrowserContext, Page, Request } from '@playwright/test';
import { spawnSync } from 'child_process';
import path from 'path';
import { PYTHON, icdevSubprocessEnv } from './fixtures/subprocess_env';

const ROOT = path.resolve(__dirname, '../..');
const SEED = path.join(ROOT, 'tests/e2e/fixtures/cop01a_seed.py');
const AXE = path.join(ROOT, 'tools/testing/vendor/axe/axe.min.js');
const T = 60_000;

type Person = { id: string; name: string; role: string; key: string };
type Answers = { cloud_path: string; cloud: string[]; engineering_path: string; engineering: string;
  wrong: { mission: string; question: string; option: number }; key: Record<string, Record<string, number>>;
  path_prefix: string };
type Seeded = { run: string; learner: Person; preparer: Person; publisher: Person; answers: Answers };

function seed(): Seeded {
  // The seeder writes to the database the RUN asked for, never an ambient one.
  const res = spawnSync(PYTHON, [SEED], {
    cwd: ROOT, encoding: 'utf8', env: icdevSubprocessEnv({ PYTHONIOENCODING: 'utf-8' }),
  });
  if (res.status !== 0) throw new Error(`cop01a_seed.py failed: ${res.stderr || res.stdout}`);
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

/** Open a mission, choose an option for each question, submit; returns the result message. */
async function answer(page: Page, mission: string, choices: Record<string, number>): Promise<string> {
  await page.locator(`button[data-mission="${mission}"]`).click();
  await expect(page.locator('#cp-mission-title')).toBeVisible({ timeout: T });
  for (const [q, i] of Object.entries(choices)) await page.locator(`#cp-${mission}-${q}-${i}`).check();
  await sends(page, 'POST', new RegExp(`^/idp/api/cop-tracks/missions/${mission}/attempt$`),
    () => page.locator('#cp-submit').click());
  await expect(page.locator('#cp-attempt-msg')).toContainText('Recorded as', { timeout: T });
  return (await page.locator('#cp-attempt-msg').textContent()) || '';
}

test.describe('CoP learning paths: a mission, its xAPI statement, a batch a person publishes (icx-cop-01)', () => {
  test.describe.configure({ mode: 'serial' });
  let s: Seeded;

  test.beforeAll(() => { s = seed(); });

  test('complete missions, see the statements, prepare, publish by another person, verify the export', async ({ browser }) => {
    test.setTimeout(240_000);
    const a = s.answers;

    // 1. a wrong answer is recorded and counts for nothing; the right ones make a progressed statement
    const learner = await as(browser, s.learner, '/idp/cop-tracks');
    const lp = learner.page;
    await expect(lp.locator(`section[data-track="${a.cloud_path}"]`)).toBeVisible({ timeout: T });
    const wrong = { ...a.key[a.wrong.mission], [a.wrong.question]: a.wrong.option };
    const failed = await answer(lp, a.wrong.mission, wrong);
    expect(failed).toContain('Not passed 2 of 3');
    expect(failed).toContain('Recorded as attempted');
    const account = (failed.match(/account (lrn-[0-9a-f]{20})/) || [])[1];
    expect(account, 'a pseudonymous account, never the person').toBeTruthy();
    expect(failed).not.toContain(s.learner.id);
    const mine = lp.locator('tr[data-mine="true"]');
    await expect(mine).toHaveCount(0);
    const passed = await answer(lp, a.engineering, a.key[a.engineering]);
    expect(passed).toContain('Passed 3 of 3');
    expect(passed).toContain('Your statement on this path: progressed');
    const eng = lp.locator(`tr[data-mine="true"][data-object="${a.path_prefix}${a.engineering_path}"]`);
    await expect(eng).toHaveAttribute('data-verb', 'progressed', { timeout: T });
    await expect(eng).toContainText('completion false');
    await expect(eng).toContainText('http://adlnet.gov/expapi/verbs/progressed');

    // 2. every mission of the cloud path: a completed statement on the path's activity id
    for (const m of a.cloud) {
      const msg = await answer(lp, m, a.key[m]);
      expect(msg).toContain('Passed 3 of 3');
    }
    const cloud = lp.locator(`tr[data-mine="true"][data-object="${a.path_prefix}${a.cloud_path}"]`);
    await expect(cloud).toHaveAttribute('data-verb', 'completed', { timeout: T });
    await expect(cloud).toContainText('completion true, success true');
    await expect(cloud).toContainText(account);
    await expect(lp.locator(`section[data-track="${a.cloud_path}"] [data-status="completed"]`)).toBeVisible();

    // 3. the twin: statements no published batch carries, threaded to the ledger row
    await sends(lp, 'GET', /^\/idp\/api\/standards-twin$/, () => lp.click('#cp-twin-observe'));
    await expect(lp.locator('#cp-twin-verdict')).toBeVisible({ timeout: T });
    const finding = lp.locator('li[data-rule="cop.academy.unreported"]');
    await expect(finding).toContainText('the newest published batch does not carry');
    await finding.locator('button[data-thread]').click();
    await expect(lp.locator('#cp-thread-resolved')).toContainText('cop_academy_events', { timeout: T });
    await expect(lp.locator('#cp-thread-resolved')).toContainText('completed');

    // 4. the preparer prepares the batch and is refused publishing it
    const preparer = await as(browser, s.preparer, '/idp/cop-tracks');
    const pr = preparer.page;
    await sends(pr, 'POST', /^\/idp\/api\/cop-tracks\/xapi\/prepare$/, () => pr.click('#cp-prepare'));
    await expect(pr.locator('#cp-prepare-msg')).toContainText('Prepared the batch', { timeout: T });
    await expect(pr.locator('#cp-ledger')).toContainText('waiting for a person', { timeout: T });
    const prepared = ((await pr.locator('#cp-prepared-digest').textContent()) || '').trim();
    expect(prepared).toMatch(/^[0-9a-f]{64}$/);
    await pr.fill('#cp-reason', 'cop01a e2e: my own (must be refused)');
    await sends(pr, 'POST', /^\/idp\/api\/cop-tracks\/xapi\/publish$/, () => pr.locator('button[data-publish="publish"]').click());
    await expect(pr.locator('#cp-publish-msg')).toContainText('Refused (separation)', { timeout: T });

    //    the publisher, another person, publishes it
    const publisher = await as(browser, s.publisher, '/idp/cop-tracks');
    const pp = publisher.page;
    await expect(pp.locator('#cp-ledger')).toContainText('waiting for a person', { timeout: T });
    await expect(pp.locator('#cp-builders')).toContainText(s.preparer.id);
    await expect(pp.locator('#cp-prepared-digest')).toHaveText(prepared);
    await pp.fill('#cp-reason', 'cop01a e2e: read the batch, publish it (synthetic)');
    await sends(pp, 'POST', /^\/idp\/api\/cop-tracks\/xapi\/publish$/, () => pp.locator('button[data-publish="publish"]').click());
    await expect(pp.locator('#cp-publish-msg')).toContainText(`the batch published by ${s.publisher.id}`, { timeout: T });
    await expect(pp.locator('#cp-published-digest')).toHaveText(prepared, { timeout: T });
    await expect(pp.locator('#cp-signed-by')).toHaveText(s.publisher.id);
    await expect(pp.locator('#cp-published-batch')).toContainText('icos-cop-academy/v1');

    // 5. the export verifies, on the page and from the route
    await sends(pp, 'GET', /^\/idp\/api\/cop-tracks\/xapi\/published$/, () => pp.click('#cp-verify'));
    await expect(pp.locator('#cp-verify-ok')).toHaveText('The export verifies', { timeout: T });
    await expect(pp.locator('#cp-verify-result')).toContainText(prepared);
    const href = await pp.locator('#cp-published-link').getAttribute('href');
    expect(href).toBe('/idp/api/cop-tracks/xapi/published');
    const resp = await pp.request.get(href || '');
    expect(resp.ok()).toBeTruthy();
    const got = await resp.json();
    expect(got.verify.ok).toBe(true);
    expect(got.batch.schema).toBe('icos-cop-academy/v1');
    expect(got.batch.content_digest).toBe(prepared);
    expect(Object.keys(got.batch).sort()).toEqual(
      ['classification', 'content_digest', 'paths', 'produced_at', 'producer', 'schema', 'statements']);
    expect(got.publication.signed_by).toBe(s.publisher.id);
    expect(got.publication.prepared_by).toBe(s.preparer.id);
    expect(got.publication.template).toBe('decl-wft-cop-academy-xapi-publish');
    type Stmt = { actor: { account: { name: string } }; verb: { id: string }; object: { id: string };
      result: { completion: boolean; success?: boolean } };
    const ours = (got.batch.statements as Stmt[]).filter(x => x.actor.account.name === account);
    expect(ours.map(x => [x.object.id, x.verb.id]).sort()).toEqual([
      [`${a.path_prefix}${a.cloud_path}`, 'http://adlnet.gov/expapi/verbs/completed'],
      [`${a.path_prefix}${a.engineering_path}`, 'http://adlnet.gov/expapi/verbs/progressed'],
    ].sort());
    expect(JSON.stringify(got.batch)).not.toContain(s.learner.id);

    // 6. the twin now reads a published batch
    await sends(pp, 'GET', /^\/idp\/api\/standards-twin$/, () => pp.click('#cp-twin-observe'));
    await expect(pp.locator('li[data-check="cop.academy.published"] [data-status="verified"]')).toBeVisible({ timeout: T });

    for (const p of [learner, preparer, publisher]) await p.ctx.close();
  });

  for (const theme of ['dark', 'light']) {
    test(`the CoP learning paths page in the ${theme} theme has no axe violations`, async ({ browser }) => {
      const { ctx, page } = await as(browser, s.learner, '/idp/cop-tracks', theme);
      await expect(page.locator('html')).toHaveAttribute('data-theme', theme);
      await expect(page.locator(`section[data-track="${s.answers.cloud_path}"]`)).toBeVisible({ timeout: T });
      await page.locator(`button[data-mission="${s.answers.engineering}"]`).click();
      await expect(page.locator('#cp-mission-title')).toBeVisible({ timeout: T });
      await page.click('#cp-twin-observe');
      await expect(page.locator('#cp-twin-verdict')).toBeVisible({ timeout: T });
      await page.addScriptTag({ path: AXE });
      const result = await page.evaluate(async () => {
        // @ts-expect-error axe is injected above
        const r = await window.axe.run(document.querySelector('.std-wrap'),
          { runOnly: { type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'] } });
        return r.violations.map((v: { id: string; nodes: { target: string[]; failureSummary?: string }[] }) =>
          `${v.id}: ${v.nodes.slice(0, 3).map(n => `${n.target.join(' ')} [${(n.failureSummary || '').split('\n').pop()}]`).join('; ')}`);
      });
      expect(result, `axe violations on /idp/cop-tracks in the ${theme} theme`).toEqual([]);
      await ctx.close();
    });
  }
});

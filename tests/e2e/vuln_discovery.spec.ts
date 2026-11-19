// CUI // SP-CTI
// E2E: AI-assisted vulnerability discovery, proven or dropped (icx-ent-01).
//
// Two fresh, card-named people (tests/e2e/fixtures/ent01_seed.py, synthetic): ent01-registrant and ent01-reviewer,
// each in its own browser context, each signed in by its own dashboard API key. Both hold every step, so what stops
// the registrant deciding their own target, patch and bundle is separation of duties. The target is the SYNTHETIC
// benchmark of planted code; its code runs only in the isolated sandbox (no network). The journey, every write
// pressed on the page:
//
//   1. the registrant registers the benchmark; they are refused authorising it; the reviewer authorises it;
//   2. the registrant runs discovery: the planted parser crash is a finding with its proof (PoV fired, control did
//      not), the safe parser is not, dropped candidates are counted, no model is asked;
//   3. the registrant proposes a patch for that finding: its proof fails on the patched copy and the tests pass;
//      they are refused reviewing it; the reviewer accepts it (nothing is applied to the target);
//   4. the registrant prepares the findings bundle and is refused publishing it; the reviewer publishes it; the
//      bundle route returns it with its publication beside it (prepared by one, signed by the other);
//   5. the twin observes: a finding's thread opens on its vr_events row;
//   6. the reviewer revokes the target (a stop, with a reason).
//
// Then the page in both themes (dark, light) with axe (the vendored axe-core) over its content.

import { test, expect } from './fixtures/observed_calls';
import type { Browser, BrowserContext, Page, Request } from '@playwright/test';
import { spawnSync } from 'child_process';
import path from 'path';
import { PYTHON, icdevSubprocessEnv } from './fixtures/subprocess_env';

const ROOT = path.resolve(__dirname, '../..');
const SEED = path.join(ROOT, 'tests/e2e/fixtures/ent01_seed.py');
const AXE = path.join(ROOT, 'tools/testing/vendor/axe/axe.min.js');
const T = 60_000;
const RUN = 180_000;   // a discovery run and a patch validation start containers

type Person = { id: string; name: string; role: string; key: string };
type Seeded = { run: string; registrant: Person; reviewer: Person; patch: string;
  benchmark: { root: string; src_root: string; test_dir: string; fuzz: string } };

function seed(): Seeded {
  const res = spawnSync(PYTHON, [SEED], {
    cwd: ROOT, encoding: 'utf8', env: icdevSubprocessEnv({ PYTHONIOENCODING: 'utf-8' }),
  });
  if (res.status !== 0) throw new Error(`ent01_seed.py failed: ${res.stderr || res.stdout}`);
  return JSON.parse(res.stdout.trim().split('\n').pop() || '{}');
}

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

async function sends(page: Page, method: string, re: RegExp, act: () => Promise<unknown>, timeout = T): Promise<Request> {
  const req = page.waitForRequest(r => r.method() === method && re.test(new URL(r.url()).pathname), { timeout });
  await act();
  return req;
}

async function pick(page: Page, tid: string) {
  await sends(page, 'GET', new RegExp(`^/vulnresearch/api/targets/${tid}$`), () => page.locator(`[data-target="${tid}"]`).click());
  await expect(page.locator(`[data-target="${tid}"]`)).toHaveAttribute('aria-pressed', 'true');
}

test.describe('Vulnerability discovery: authorised target, PoV-proven finding, reviewed patch, published bundle (icx-ent-01)', () => {
  test.describe.configure({ mode: 'serial' });
  let s: Seeded;
  let tid = '';

  test.beforeAll(() => { s = seed(); });

  test('register, authorise, discover, prove, patch, review, prepare, publish, open the twin thread', async ({ browser }) => {
    test.setTimeout(900_000);
    const reg = await as(browser, s.registrant, '/vulnresearch/');
    const page = reg.page;
    await expect(page.locator('#vr-help')).toContainText('finding only with a proof of');
    await expect(page.locator('#vr-isolation')).toContainText('network none');

    // 1. register the benchmark; the registrant is refused authorising it; the reviewer authorises it
    await page.fill('#vr-name', `ent01 benchmark ${s.run} (synthetic)`);
    await page.fill('#vr-root', s.benchmark.root);
    await page.fill('#vr-src', s.benchmark.src_root);
    await page.fill('#vr-tests', s.benchmark.test_dir);
    await page.fill('#vr-fuzz', s.benchmark.fuzz);
    await page.fill('#vr-why', 'ent01 e2e: the synthetic planted benchmark, owned by ICDEV (synthetic)');
    await sends(page, 'POST', /^\/vulnresearch\/api\/targets$/, () => page.click('#vr-register-btn'));
    await expect(page.locator('#vr-register-msg')).toContainText('Registered vrt-', { timeout: T });
    tid = ((await page.locator('#vr-register-msg').textContent()) || '').match(/vrt-[0-9a-f]{10}/)?.[0] || '';
    expect(tid).toMatch(/^vrt-[0-9a-f]{10}$/);
    await expect(page.locator('#vr-target-summary')).toContainText('awaiting authorisation', { timeout: T });
    await sends(page, 'POST', /\/runs$/, () => page.click('#vr-run'));
    await expect(page.locator('#vr-run-msg')).toContainText('Refused (out_of_scope)', { timeout: T });
    await page.fill('#vr-reason', 'ent01 e2e: my own target (must be refused)');
    await sends(page, 'POST', /\/authorise$/, () => page.locator('button[data-authorise="authorise"]').click());
    await expect(page.locator('#vr-authorise-msg')).toContainText('Refused (separation)', { timeout: T });

    const rev = await as(browser, s.reviewer, '/vulnresearch/');
    const rp = rev.page;
    await pick(rp, tid);
    await expect(rp.locator('#vr-authorise')).toContainText(s.registrant.id);
    await rp.fill('#vr-reason', 'ent01 e2e: ICDEV owns this synthetic tree; authorised for testing');
    await sends(rp, 'POST', /\/authorise$/, () => rp.locator('button[data-authorise="authorise"]').click());
    await expect(rp.locator('#vr-authorise-msg')).toContainText(`authorised by ${s.reviewer.id}`, { timeout: T });

    // 2. discovery: the planted parser crash is proven; the safe parser is not; drops are counted
    await page.reload();
    await pick(page, tid);
    await expect(page.locator('#vr-target-summary')).toContainText('authorised', { timeout: T });
    await page.uncheck('#vr-model');
    await sends(page, 'POST', new RegExp(`^/vulnresearch/api/targets/${tid}/runs$`), () => page.click('#vr-run'));
    await expect(page.locator('#vr-run-msg')).toContainText('findings proven', { timeout: RUN });
    const crash = page.locator('#vr-findings-body tr[data-cwe="CWE-20"]');
    await expect(crash).toContainText('vb_parser:parse_record', { timeout: T });
    await expect(crash.locator('[data-pov]')).toHaveAttribute('data-pov', 'reproduced');
    await expect(crash).toContainText('control not_reproduced');
    await expect(page.locator('#vr-findings-body')).not.toContainText('vb_parser_safe');
    await expect(page.locator('#vr-counts')).toContainText('dropped');
    await expect(page.locator('#vr-run-body')).toContainText('network none');
    await expect(page.locator('#vr-review-status')).toContainText('not asked');

    // 3. a patch: proposed by the registrant, validated in the sandbox, refused to them, accepted by the reviewer
    await crash.locator('button[data-pick]').click();
    await expect(page.locator('#vr-patch-for')).toContainText('vb_parser:parse_record');
    await page.fill('#vr-diff', s.patch);
    await sends(page, 'POST', /^\/vulnresearch\/api\/findings\/vrf-[0-9a-f]{12}\/patches$/, () => page.click('#vr-propose'));
    await expect(page.locator('#vr-propose-msg')).toContainText('the proof now fails and the tests pass', { timeout: RUN });
    await expect(page.locator('#vr-validation')).toContainText('not_reproduced', { timeout: T });
    await page.fill('#vr-review-reason', 'ent01 e2e: my own patch (must be refused)');
    await sends(page, 'POST', /\/review$/, () => page.locator('button[data-review="accept"]').click());
    await expect(page.locator('#vr-review-msg')).toContainText('Refused (separation)', { timeout: T });

    await rp.reload();
    await pick(rp, tid);
    await rp.locator('#vr-findings-body tr[data-cwe="CWE-20"] button[data-pick]').click();
    await expect(rp.locator('#vr-validation')).toContainText('pass', { timeout: T });
    await rp.fill('#vr-review-reason', 'ent01 e2e: read the diff and its proof; accept the proposal (synthetic)');
    await sends(rp, 'POST', /\/review$/, () => rp.locator('button[data-review="accept"]').click());
    await expect(rp.locator('#vr-review-msg')).toContainText(`patch_accepted by ${s.reviewer.id}`, { timeout: T });
    await expect(rp.locator('#vr-review-msg')).toContainText('Not applied to the target');

    // 3b. the false-positive and false-negative rates on the benchmark's planted labels (no model judges)
    await page.reload();
    await pick(page, tid);
    await page.uncheck('#vr-model');
    await sends(page, 'POST', new RegExp(`^/vulnresearch/api/targets/${tid}/measure$`), () => page.click('#vr-measure'));
    await expect(page.locator('#vr-run-msg')).toContainText('Measured: false-positive rate', { timeout: RUN });
    await page.reload();
    await expect(page.locator('#vr-measured')).toContainText('not yet reviewed by a person', { timeout: T });

    // 4. the bundle: prepared by the registrant, refused to them, published by the reviewer
    await pick(page, tid);
    await sends(page, 'POST', /\/bundle\/prepare$/, () => page.click('#vr-prepare'));
    await expect(page.locator('#vr-prepare-msg')).toContainText('Prepared bundle', { timeout: T });
    const prepared = (((await page.locator('#vr-prepare-msg').textContent()) || '').match(/[0-9a-f]{64}/) || [''])[0];
    expect(prepared).toMatch(/^[0-9a-f]{64}$/);
    await expect(page.locator('#vr-ledger')).toContainText('waiting for a person', { timeout: T });
    await page.fill('#vr-publish-reason', 'ent01 e2e: my own bundle (must be refused)');
    await sends(page, 'POST', /\/bundle\/publish$/, () => page.locator('button[data-publish="publish"]').click());
    await expect(page.locator('#vr-publish-msg')).toContainText('Refused (separation)', { timeout: T });

    await rp.reload();
    await pick(rp, tid);
    await expect(rp.locator('#vr-publish')).toContainText(s.registrant.id, { timeout: T });
    await rp.fill('#vr-publish-reason', 'ent01 e2e: read the bundle; publish it to ICDEV[OS] (synthetic)');
    await sends(rp, 'POST', /\/bundle\/publish$/, () => rp.locator('button[data-publish="publish"]').click());
    await expect(rp.locator('#vr-publish-msg')).toContainText(`bundle published by ${s.reviewer.id}`, { timeout: T });
    await expect(rp.locator('#vr-published-digest')).toHaveText(prepared, { timeout: T });
    await expect(rp.locator('#vr-published-bundle')).toContainText('icos-findings/v1');
    const href = await rp.locator('#vr-bundle-link').getAttribute('href');
    expect(href).toBe(`/vulnresearch/api/targets/${tid}/bundle`);
    const resp = await rp.request.get(href || '');
    expect(resp.ok()).toBeTruthy();
    const got = await resp.json();
    expect(got.bundle.schema).toBe('icos-findings/v1');
    expect(got.bundle.content_digest).toBe(prepared);
    expect(got.publication.content_digest).toBe(prepared);
    expect(got.publication.signed_by).toBe(s.reviewer.id);
    expect(got.publication.prepared_by).toBe(s.registrant.id);
    expect(got.publication.template).toBe('decl-wft-vr-findings-publish');
    const f = got.bundle.findings.find((x: { cwe: string }) => x.cwe === 'CWE-20');
    expect(f.pov.sealed).toBe(true);
    expect(JSON.stringify(f.pov)).not.toContain('"input"');
    expect(f.patch.review.decision).toBe('accept');
    expect(f.patch.applied).toBe(false);

    // 5. the twin observes; a finding's thread opens on its vr_events row
    await sends(rp, 'GET', /^\/vulnresearch\/api\/twin$/, () => rp.click('#vr-observe'));
    await expect(rp.locator('#vr-twin-verdict')).toBeVisible({ timeout: T });
    const iso = rp.locator('li[data-rule="vulnresearch.isolation_container"]').first();
    await expect(iso).toBeVisible({ timeout: T });
    await iso.locator('button[data-thread]').click();
    await expect(rp.locator('#vr-thread-resolved')).toContainText('run_recorded', { timeout: T });
    await expect(rp.locator('#vr-thread-resolved')).toContainText('vr_events');

    // 5b. the findings are queryable in the console's IQE widget (reads; sealed PoVs are no column)
    await rp.click('#iqew_vulnresearch .iqe-widget__header');
    await rp.fill('#iqew_vulnresearch_ta', 'Which findings have no accepted patch?');
    await sends(rp, 'POST', /^\/vulnresearch\/api\/iqe-query$/, () => rp.click('#iqew_vulnresearch_btn'));
    await expect(rp.locator('#iqew_vulnresearch_status')).toBeVisible({ timeout: T });
    await expect(rp.locator('#iqew_vulnresearch_status')).not.toContainText('Translating', { timeout: T });

    // 6. the stop: the reviewer revokes the target at once, with a reason; nothing scans it again
    await rp.fill('#vr-revoke-reason', 'ent01 e2e: the journey is done; stop testing this tree (synthetic)');
    await sends(rp, 'POST', /\/revoke$/, () => rp.click('#vr-revoke-btn'));
    await expect(rp.locator('#vr-revoke-msg')).toContainText(`Revoked by ${s.reviewer.id}`, { timeout: T });
    await expect(rp.locator('#vr-target-summary')).toContainText('revoked', { timeout: T });

    for (const p of [reg, rev]) await p.ctx.close();
  });

  for (const theme of ['dark', 'light']) {
    test(`the vulnerability discovery page in the ${theme} theme has no axe violations`, async ({ browser }) => {
      const { ctx, page } = await as(browser, s.reviewer, '/vulnresearch/', theme);
      await expect(page.locator('html')).toHaveAttribute('data-theme', theme);
      if (tid) {
        await pick(page, tid);
        await expect(page.locator('#vr-findings-body tr[data-cwe="CWE-20"]')).toBeVisible({ timeout: T });
      }
      await page.click('#vr-observe');
      await expect(page.locator('#vr-twin-verdict')).toBeVisible({ timeout: T });
      await page.addScriptTag({ path: AXE });
      const result = await page.evaluate(async () => {
        // @ts-expect-error axe is injected above
        const r = await window.axe.run(document.querySelector('.vr-wrap'),
          { runOnly: { type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'] } });
        return r.violations.map((v: { id: string; nodes: { target: string[]; failureSummary?: string }[] }) =>
          `${v.id}: ${v.nodes.slice(0, 3).map(n => `${n.target.join(' ')} [${(n.failureSummary || '').split('\n').pop()}]`).join('; ')}`);
      });
      expect(result, `axe violations on /vulnresearch/ in the ${theme} theme`).toEqual([]);
      await ctx.close();
    });
  }
});

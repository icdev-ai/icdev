// CUI // SP-CTI
// E2E: a source profiled on the Data Design Canvas, its discovery bundle prepared, and published to ICDEV[OS] by
// another person (osx-cap-02).
//
// Two fresh, card-named people (tests/e2e/fixtures/cap02_seed.py, synthetic): cap02i-e2eprep and cap02i-e2epub, each
// in its own browser context (its own session), each signed in by its own dashboard API key. Both hold the role that
// publishes, so what stops the preparer publishing their own bundle on the page is separation of duties. The seed also
// writes a SYNTHETIC SQLite source; the server reads its declaration (ICDEV_DISCOVERY_SOURCES_E2E). The journey, every
// write sent by the product's own pages and routes:
//
//   1. the preparer creates the design and two quality rules and runs them (the canvas's routes), then profiles the
//      source on the Explore page;
//   2. on /data-discovery/ the bundle shows the profile: types and counts, the PII column by pattern name and no
//      value, the passing and the failing quality rule;
//   3. the preparer prepares it and is refused publishing it; the publisher publishes it;
//   4. the published bundle downloads, and its digest is the one prepared, with its publication;
//   5. the twin observes the ledger and a finding's thread opens on its ledger row.
//
// Then the page in both themes (dark, light) with axe (the vendored axe-core) over its content.

import { test, expect } from './fixtures/observed_calls';
import type { Browser, BrowserContext, Page, Request } from '@playwright/test';
import { spawnSync } from 'child_process';
import { readFileSync } from 'fs';
import path from 'path';
import { PYTHON, icdevSubprocessEnv } from './fixtures/subprocess_env';

const ROOT = path.resolve(__dirname, '../..');
const SEED = path.join(ROOT, 'tests/e2e/fixtures/cap02_seed.py');
const AXE = path.join(ROOT, 'tools/testing/vendor/axe/axe.min.js');
const T = 60_000;

type Person = { id: string; name: string; role: string; key: string };
type Seeded = { run: string; preparer: Person; publisher: Person; source: string; key: string; design: string };

function seed(): Seeded {
  // The seeder writes to the database the RUN asked for, never an ambient one.
  const res = spawnSync(PYTHON, [SEED], {
    cwd: ROOT, encoding: 'utf8', env: icdevSubprocessEnv({ PYTHONIOENCODING: 'utf-8' }),
  });
  if (res.status !== 0) throw new Error(`cap02_seed.py failed: ${res.stderr || res.stdout}`);
  return JSON.parse(res.stdout.trim().split('\n').pop() || '{}');
}

/** A person's own browser context: their own session, signed in by their own key. */
async function as(browser: Browser, who: Person, where: string, theme = 'dark'): Promise<{ ctx: BrowserContext; page: Page }> {
  const ctx = await browser.newContext({ extraHTTPHeaders: { Authorization: `Bearer ${who.key}` }, acceptDownloads: true });
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

test.describe('Data discovery export to ICDEV[OS]: profile, prepare, a different person publishes (osx-cap-02)', () => {
  test.describe.configure({ mode: 'serial' });
  let s: Seeded;

  test.beforeAll(() => { s = seed(); });

  test('profile a source, prepare its bundle, refuse the preparer, publish it, download it, open the twin thread', async ({ browser }) => {
    test.setTimeout(300_000);
    const preparer = await as(browser, s.preparer, '/data/explore');
    const page = preparer.page;

    // 1. the design, its quality rules and their run, through the canvas's own routes
    const design = await page.request.post('/data/api/designs', { data: { name: s.design, classification: 'CUI' } });
    expect(design.status()).toBe(201);
    const designId = (await design.json()).id as string;
    for (const rule of [
      { name: 'cap02i area complete', table_name: 'track_summary', column_name: 'area', check_type: 'completeness', threshold: 90 },
      { name: 'cap02i outage id unique', table_name: 'relay_outages', column_name: 'id', check_type: 'uniqueness', threshold: 100 },
    ]) {
      const r = await page.request.post('/data/api/quality/rules', { data: { ...rule, design_id: designId } });
      expect(r.status()).toBe(201);
    }
    const ran = await page.request.post('/data/api/quality/run', { data: { design_id: designId, db_type: 'sqlite', path: s.source } });
    expect((await ran.json()).rules_run).toBe(2);

    //    the source profiled on the Explore page
    await page.reload();
    await expect(page.locator('#ex-discovery-link')).toHaveAttribute('href', '/data-discovery/');
    await page.selectOption('#designSelect', designId);
    await page.selectOption('#dbType', 'sqlite');
    await page.fill('#sqlitePath', s.source);
    await sends(page, 'POST', /^\/data\/api\/explore\/profile$/, () => page.click('#runProfileBtn'));
    await expect(page.locator('#profileResults')).toContainText('track_summary', { timeout: T });

    // 2. the bundle shows the profile: types and counts, PII by pattern name and no value, the quality results
    await page.click('#ex-discovery-link');
    await expect(page).toHaveURL(/\/data-discovery\/$/);
    await expect(page.locator(`[data-source="${s.key}"]`)).toHaveAttribute('aria-pressed', 'true', { timeout: T });
    await expect(page.locator('#ddx-digest')).toBeVisible({ timeout: T });
    await expect(page.locator('#ddx-help')).toContainText('metadata only, never a datum');
    await expect(page.locator('tr[data-col="relay_outages.id"]')).toContainText('2');
    const pii = page.locator('tr[data-pii="track_summary.analyst_email"]');
    await expect(pii).toContainText('email');
    await expect(page.locator('.ddx-wrap')).not.toContainText('example.org');
    await expect(page.locator('tr[data-kind="completeness"] [data-status]')).toHaveAttribute('data-status', 'pass');
    await expect(page.locator('tr[data-kind="uniqueness"] [data-status]')).toHaveAttribute('data-status', 'fail');

    // 3. the preparer prepares it, and is refused publishing it
    await sends(page, 'POST', new RegExp(`^/data-discovery/api/sources/${s.key}/prepare$`), () => page.click('#ddx-prepare'));
    await expect(page.locator('#ddx-prepare-msg')).toContainText(`Prepared ${s.key} bundle`, { timeout: T });
    await expect(page.locator('#ddx-ledger')).toContainText('waiting for a person', { timeout: T });
    const prepared = (await page.locator('#ddx-digest').textContent() || '').trim();
    expect(prepared).toMatch(/^[0-9a-f]{64}$/);
    await page.fill('#ddx-reason', 'cap02i e2e: my own (must be refused)');
    await sends(page, 'POST', new RegExp(`^/data-discovery/api/sources/${s.key}/publish$`),
      () => page.locator('button[data-publish="publish"]').click());
    await expect(page.locator('#ddx-publish-msg')).toContainText('Refused (separation)', { timeout: T });

    //    the publisher, another person, publishes it
    const publisher = await as(browser, s.publisher, '/data-discovery/');
    const pp = publisher.page;
    await expect(pp.locator('#ddx-ledger')).toContainText('waiting for a person', { timeout: T });
    await expect(pp.locator('#ddx-publish')).toContainText(s.preparer.id);
    await pp.fill('#ddx-reason', 'cap02i e2e: read the bundle, publish it (synthetic)');
    await sends(pp, 'POST', new RegExp(`^/data-discovery/api/sources/${s.key}/publish$`),
      () => pp.locator('button[data-publish="publish"]').click());
    await expect(pp.locator('#ddx-publish-msg')).toContainText(`${s.key} bundle published by ${s.publisher.id}`, { timeout: T });

    // 4. the published bundle downloads; its digest is the one prepared, with its publication
    await expect(pp.locator('#ddx-published-digest')).toHaveText(prepared, { timeout: T });
    await expect(pp.locator('#ddx-ledger')).toContainText('Newest published', { timeout: T });
    const [download] = await Promise.all([pp.waitForEvent('download', { timeout: T }), pp.click('#ddx-bundle-link')]);
    expect(download.suggestedFilename()).toBe(`${s.key}.json`);
    const bundle = JSON.parse(readFileSync(await download.path(), 'utf8'));
    expect(bundle.schema).toBe('icos-discovery/v1');
    expect(bundle.content_digest).toBe(prepared);
    expect(bundle.publication.content_digest).toBe(prepared);
    expect(bundle.publication.signed_by).toBe(s.publisher.id);
    expect(bundle.publication.prepared_by).toBe(s.preparer.id);
    expect(bundle.publication.template).toBe('decl-wft-data-discovery-publish');
    expect(JSON.stringify(bundle)).not.toContain('example.org');

    // 5. the twin observes the ledger; a finding's thread opens on its ledger row
    await sends(pp, 'GET', /^\/data-discovery\/api\/twin$/, () => pp.click('#ddx-observe'));
    await expect(pp.locator('#ddx-twin-verdict')).toBeVisible({ timeout: T });
    const finding = pp.locator('li[data-rule="data.discovery.quality_failing"]').first();
    await expect(finding).toContainText(`${s.key}: quality rule`);
    await finding.locator('button[data-thread]').click();
    await expect(pp.locator('#ddx-thread-resolved')).toContainText('data_discovery_export_events', { timeout: T });
    await expect(pp.locator('#ddx-thread-resolved')).toContainText(`published by ${s.publisher.id}`);
    await expect(pp.locator('#ddx-thread-resolved')).toContainText(prepared.slice(0, 16));

    for (const p of [preparer, publisher]) await p.ctx.close();
  });

  for (const theme of ['dark', 'light']) {
    test(`the data discovery export page in the ${theme} theme has no axe violations`, async ({ browser }) => {
      const { ctx, page } = await as(browser, s.publisher, '/data-discovery/', theme);
      await expect(page.locator('html')).toHaveAttribute('data-theme', theme);
      await expect(page.locator('#ddx-digest')).toBeVisible({ timeout: T });
      await page.click('#ddx-observe');
      await expect(page.locator('#ddx-twin-verdict')).toBeVisible({ timeout: T });
      await page.addScriptTag({ path: AXE });
      const result = await page.evaluate(async () => {
        // @ts-expect-error axe is injected above
        const r = await window.axe.run(document.querySelector('.ddx-wrap'),
          { runOnly: { type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'] } });
        return r.violations.map((v: { id: string; nodes: { target: string[]; failureSummary?: string }[] }) =>
          `${v.id}: ${v.nodes.slice(0, 3).map(n => `${n.target.join(' ')} [${(n.failureSummary || '').split('\n').pop()}]`).join('; ')}`);
      });
      expect(result, `axe violations on /data-discovery/ in the ${theme} theme`).toEqual([]);
      await ctx.close();
    });
  }
});

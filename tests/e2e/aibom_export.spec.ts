// CUI // SP-CTI
// E2E: ICDEV[IT]'s own AI-BOM (SPDX 3.0.1 AI + CycloneDX 1.7 ML-BOM), prepared, and published to ICDEV[OS] by another
// person (osx-cop-02).
//
// Two fresh, card-named people (tests/e2e/fixtures/cop02_seed.py, synthetic): cop02i-prep and cop02i-pub, each in its
// own browser context (its own session), each signed in by its own dashboard API key. Both hold the role that
// publishes, so what stops the preparer publishing their own AI-BOM is separation of duties. The server reads the
// SYNTHETIC components (ICDEV_AIBOM_SOURCES_E2E). The journey, every write sent by the product's own page:
//
//   1. on /ai-bom/ (dark theme) the components show with their digests, the two standards with their schema digests;
//   2. the preparer prepares it; the publish step is closed to them, saying why, and the route refuses them too;
//   3. the publisher (light theme) publishes it;
//   4. the published bundle and both documents download; the digest is the one prepared, with its publication;
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
const SEED = path.join(ROOT, 'tests/e2e/fixtures/cop02_seed.py');
const AXE = path.join(ROOT, 'tools/testing/vendor/axe/axe.min.js');
const FX = 'tests/e2e/fixtures/aibom';
const T = 60_000;

type Person = { id: string; name: string; role: string; key: string };
type Seeded = { run: string; preparer: Person; publisher: Person };

function seed(): Seeded {
  // The seeder writes to the database the RUN asked for, never an ambient one.
  const res = spawnSync(PYTHON, [SEED], {
    cwd: ROOT, encoding: 'utf8', env: icdevSubprocessEnv({ PYTHONIOENCODING: 'utf-8' }),
  });
  if (res.status !== 0) throw new Error(`cop02_seed.py failed: ${res.stderr || res.stdout}`);
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

test.describe('AI-BOM export to ICDEV[OS]: SPDX 3.0.1 + CycloneDX 1.7, prepared, a different person publishes (osx-cop-02)', () => {
  test.describe.configure({ mode: 'serial' });
  let s: Seeded;

  test.beforeAll(() => { s = seed(); });

  test('prepare the AI-BOM, the preparer cannot publish it, another person publishes it, download it, open the twin thread', async ({ browser }) => {
    test.setTimeout(300_000);
    const preparer = await as(browser, s.preparer, '/ai-bom/', 'dark');
    const page = preparer.page;
    await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark');

    // 1. the components and the standards
    await expect(page.locator('#aib-digest')).toBeVisible({ timeout: T });
    await expect(page.locator('#aib-help')).toContainText('by digest only');
    for (const id of [`router-config:${FX}/router.yaml`, 'model:syn-drafter', 'model:syn-reviewer',
      `dataset:${FX}/dataset.jsonl`, `gold-set:${FX}/gold.yaml`, `prompt:${FX}/prompts/draft.md`, `prompt:${FX}/prompts/review.md`]) {
      await expect(page.locator(`tr[data-component="${id}"]`)).toBeVisible();
    }
    await expect(page.locator('tr[data-component="model:syn-drafter"]')).toContainText('synthetic-provider');
    await expect(page.locator('#aib-standards')).toContainText('SPDX 3.0.1');
    await expect(page.locator('#aib-standards')).toContainText('CycloneDX 1.7');
    await expect(page.locator('#aib-summary')).toContainText('valid against SPDX 3.0.1 and CycloneDX 1.7');
    await expect(page.locator('.aib-wrap')).not.toContainText('draft a practice from its sources');

    // 2. the preparer prepares it; the step is closed to them, and the route refuses them
    await sends(page, 'POST', /^\/ai-bom\/api\/prepare$/, () => page.click('#aib-prepare'));
    await expect(page.locator('#aib-prepare-msg')).toContainText('Prepared the AI-BOM', { timeout: T });
    await expect(page.locator('#aib-ledger')).toContainText('waiting for a person', { timeout: T });
    const prepared = (await page.locator('#aib-digest').textContent() || '').trim();
    expect(prepared).toMatch(/^[0-9a-f]{64}$/);
    await expect(page.locator('#aib-why-not')).toContainText('you may not publish it');
    await expect(page.locator('button[data-publish="publish"]')).toBeDisabled();
    const self = await page.request.post('/ai-bom/api/publish', { data: { decision: 'publish', reason: 'cop02i e2e: my own (must be refused)', digest: prepared } });
    expect(self.status()).toBe(403);
    expect((await self.json()).code).toBe('separation');

    // 3. the publisher, another person (light theme), publishes it
    const publisher = await as(browser, s.publisher, '/ai-bom/', 'light');
    const pp = publisher.page;
    await expect(pp.locator('html')).toHaveAttribute('data-theme', 'light');
    await expect(pp.locator('#aib-ledger')).toContainText('waiting for a person', { timeout: T });
    await expect(pp.locator('#aib-publish')).toContainText(s.preparer.id);
    await expect(pp.locator('button[data-publish="publish"]')).toBeEnabled();
    await pp.fill('#aib-reason', 'cop02i e2e: read the AI-BOM, publish it (synthetic)');
    await sends(pp, 'POST', /^\/ai-bom\/api\/publish$/, () => pp.locator('button[data-publish="publish"]').click());
    await expect(pp.locator('#aib-publish-msg')).toContainText(`the AI-BOM was published by ${s.publisher.id}`, { timeout: T });

    // 4. the published bundle and both documents download; the digest is the one prepared, with its publication
    await expect(pp.locator('#aib-published-digest')).toHaveText(prepared, { timeout: T });
    const [download] = await Promise.all([pp.waitForEvent('download', { timeout: T }), pp.click('#aib-bundle-link')]);
    expect(download.suggestedFilename()).toBe('icdev-it.json');
    const bundle = JSON.parse(readFileSync(await download.path(), 'utf8'));
    expect(bundle.schema).toBe('icos-aibom/v1');
    expect(bundle.content_digest).toBe(prepared);
    expect(bundle.publication.content_digest).toBe(prepared);
    expect(bundle.publication.signed_by).toBe(s.publisher.id);
    expect(bundle.publication.prepared_by).toBe(s.preparer.id);
    expect(bundle.publication.template).toBe('decl-wft-aibom-publish');
    expect(bundle.standards).toEqual({ spdx: '3.0.1', cyclonedx: '1.7' });
    const [spdx] = await Promise.all([pp.waitForEvent('download', { timeout: T }), pp.click('#aib-spdx-link')]);
    expect(spdx.suggestedFilename()).toBe('icdev-it.spdx3.json');
    expect(JSON.parse(readFileSync(await spdx.path(), 'utf8'))['@context']).toBe('https://spdx.org/rdf/3.0.1/spdx-context.jsonld');
    const [cdx] = await Promise.all([pp.waitForEvent('download', { timeout: T }), pp.click('#aib-cdx-link')]);
    expect(cdx.suggestedFilename()).toBe('icdev-it.cdx.json');
    expect(JSON.parse(readFileSync(await cdx.path(), 'utf8')).specVersion).toBe('1.7');

    // 5. the twin observes the ledger (published: verified, never passed before it); the publication's thread opens
    //    on its ledger row
    await sends(pp, 'GET', /^\/ai-bom\/api\/twin$/, () => pp.click('#aib-observe'));
    await expect(pp.locator('#aib-twin-verdict')).toBeVisible({ timeout: T });
    await expect(pp.locator('#aib-twin-verdict [data-status]')).toHaveAttribute('data-status', 'pass');
    await sends(pp, 'GET', /^\/ai-bom\/api\/twin$/, () => pp.click('#aib-pub-thread'));
    await expect(pp.locator('#aib-thread-resolved')).toContainText('aibom_export_events', { timeout: T });
    await expect(pp.locator('#aib-thread-resolved')).toContainText(`published by ${s.publisher.id}`);
    await expect(pp.locator('#aib-thread-resolved')).toContainText(prepared.slice(0, 16));

    for (const p of [preparer, publisher]) await p.ctx.close();
  });

  for (const theme of ['dark', 'light']) {
    test(`the AI-BOM export page in the ${theme} theme has no axe violations`, async ({ browser }) => {
      const { ctx, page } = await as(browser, s.publisher, '/ai-bom/', theme);
      await expect(page.locator('html')).toHaveAttribute('data-theme', theme);
      await expect(page.locator('#aib-digest')).toBeVisible({ timeout: T });
      await page.click('#aib-observe');
      await expect(page.locator('#aib-twin-verdict')).toBeVisible({ timeout: T });
      await page.addScriptTag({ path: AXE });
      const result = await page.evaluate(async () => {
        // @ts-expect-error axe is injected above
        const r = await window.axe.run(document.querySelector('.aib-wrap'),
          { runOnly: { type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'] } });
        return r.violations.map((v: { id: string; nodes: { target: string[]; failureSummary?: string }[] }) =>
          `${v.id}: ${v.nodes.slice(0, 3).map(n => `${n.target.join(' ')} [${(n.failureSummary || '').split('\n').pop()}]`).join('; ')}`);
      });
      expect(result, `axe violations on /ai-bom/ in the ${theme} theme`).toEqual([]);
      await ctx.close();
    });
  }
});

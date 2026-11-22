// CUI // SP-CTI
// E2E: signed factory builds, a build published to ICDEV[OS] by a person (icx-ent-03).
//
// Three fresh, card-named people (tests/e2e/fixtures/ent03_seed.py, synthetic), each in its own browser context
// signed in by its own dashboard API key: ent03-preparer and ent03-publisher of the synthetic targets' organisation,
// and ent03-outsider of another. The e2e dashboard signs with a THROWAWAY factory key the seed wrote under
// tests/e2e/fixtures (ICDEV_FACTORY_BUILDS_E2E_KEY; the shipped configuration names no key). The seed built the model
// target with it (its AI-BOM linked to a promoted fine-tuning registry version, its weights signed with OpenSSF
// model-signing). The journey, every write pressed on the page:
//
//   1. the preparer opens the image target and presses Build and sign: the factory's key signs an OCI image build;
//   2. the signed build shows: its subject and key, its SBOM, the VEX statement, the transparency log said to be off,
//      the self-check re-run on this read, waiting for a person;
//   3. the preparer is refused publishing their own build (separation of duties);
//   4. the publisher, another person of the org, publishes it with a reason;
//   5. the published build shows; the export route returns {bundle, publication} as ICDEV[OS] imports it;
//   6. its twin thread resolves to the ledger row;
//   7. the publisher opens the model target: its AI-BOM names the registry promotion it rests on;
//   8. the outsider sees no target (two held back) and the target's route is "no such target".
//
// Then the page in both themes (dark, light) with axe (the vendored axe-core) over its content.

import { test, expect } from './fixtures/observed_calls';
import type { Browser, BrowserContext, Page, Request } from '@playwright/test';
import { spawnSync } from 'child_process';
import path from 'path';
import { PYTHON, icdevSubprocessEnv } from './fixtures/subprocess_env';

const ROOT = path.resolve(__dirname, '../..');
const SEED = path.join(ROOT, 'tests/e2e/fixtures/ent03_seed.py');
const AXE = path.join(ROOT, 'tools/testing/vendor/axe/axe.min.js');
const T = 60_000;
const IMAGE = '/factory-builds/?target=syn-ent03-portal';
const MODEL = '/factory-builds/?target=syn-ent03-classifier';

type Person = { id: string; name: string; role: string; key: string; org: string };
type Build = { digest: string; event_id: string };
type Seeded = { run: string; preparer: Person; publisher: Person; outsider: Person;
  builds: { 'syn-ent03-classifier': Build } };

function seed(): Seeded {
  // The seeder writes to the database the RUN asked for, never an ambient one.
  const res = spawnSync(PYTHON, [SEED], {
    cwd: ROOT, encoding: 'utf8', env: icdevSubprocessEnv({ PYTHONIOENCODING: 'utf-8' }),
  });
  if (res.status !== 0) throw new Error(`ent03_seed.py failed: ${res.stderr || res.stdout}`);
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

test.describe('Signed factory builds: a signed artefact published to ICDEV[OS] by another person (icx-ent-03)', () => {
  test.describe.configure({ mode: 'serial' });
  let s: Seeded;

  test.beforeAll(() => { s = seed(); });

  test('build and sign on the page, refuse the preparer, publish, export, open the thread, see the AI-BOM, hide it from another org', async ({ browser }) => {
    test.setTimeout(300_000);
    // 1. the preparer opens the image target and builds and signs it
    const preparer = await as(browser, s.preparer, IMAGE);
    const page = preparer.page;
    await expect(page.locator('#fb-help')).toContainText('CycloneDX 1.7');
    await expect(page.locator('#fb-help')).toContainText('SLSA v1.2');
    await expect(page.locator('#fb-tlog')).toContainText('off');
    await expect(page.locator('#fb-target')).toHaveAttribute('data-target', 'syn-ent03-portal');
    await page.fill('#fb-version', `1.0.${parseInt(s.run.slice(0, 4), 16)}`);
    await page.fill('#fb-note', 'ent03 e2e: build and sign the synthetic image');
    await sends(page, 'POST', /^\/factory-builds\/api\/targets\/syn-ent03-portal\/prepare$/, () => page.click('#fb-prepare'));

    // 2. the signed build shows, waiting for a person
    await expect(page.locator('#fb-prepared')).toContainText(`prepared by ${s.preparer.id}`, { timeout: T });
    await expect(page.locator('#fb-prepared')).toContainText('waiting for a person');
    const digest = (await page.locator('#fb-digest').getAttribute('data-digest')) || '';
    expect(digest).toMatch(/^sha256:[0-9a-f]{64}$/);
    const image = { digest };
    await expect(page.locator('#fb-self-check')).toContainText('verified now');
    await expect(page.locator('#fb-sbom')).toContainText('SBOM: container registry.syn-foundry.invalid/foundry/portal');
    await expect(page.locator('tr[data-vuln="SYN-ENT03-0001"]')).toContainText('not affected');
    await expect(page.locator('#fb-files')).toContainText('bundles/provenance.sigstore.json');

    // 3. the preparer is refused publishing their own build
    await page.fill('#fb-reason', 'ent03 e2e: my own (must be refused)');
    await sends(page, 'POST', /^\/factory-builds\/api\/targets\/syn-ent03-portal\/publish$/,
      () => page.locator('button[data-publish="publish"]').click());
    await expect(page.locator('#fb-publish-msg')).toContainText('Refused (separation)', { timeout: T });

    // 4. the publisher, another person of the org, publishes it
    const publisher = await as(browser, s.publisher, IMAGE);
    const pp = publisher.page;
    await expect(pp.locator('#fb-builders')).toContainText(s.preparer.id);
    await pp.fill('#fb-reason', 'ent03 e2e: read the SBOM, provenance and VEX, publish (synthetic)');
    await sends(pp, 'POST', /^\/factory-builds\/api\/targets\/syn-ent03-portal\/publish$/,
      () => pp.locator('button[data-publish="publish"]').click());

    // 5. the published build; the export route returns it with its publication beside it
    await expect(pp.locator('#fb-published')).toContainText(`published by ${s.publisher.id}`, { timeout: T });
    await expect(pp.locator('#fb-decided')).toContainText('published');
    const href = await pp.locator('#fb-export-link').getAttribute('href');
    expect(href).toBe('/factory-builds/api/targets/syn-ent03-portal/export');
    const resp = await pp.request.get(href || '');
    expect(resp.ok()).toBeTruthy();
    const ex = await resp.json();
    expect(Object.keys(ex).sort()).toEqual(['bundle', 'publication']);
    expect(ex.bundle.schema).toBe('icos-release-transfer/v1');
    expect(JSON.parse(ex.bundle.files['artefact.json']).schema).toBe('icos-artefact/v1');
    expect(ex.publication.schema).toBe('icdev-build-publication/v1');
    expect(ex.publication.content_digest).toBe(image.digest);
    expect(ex.publication.signed_by).toBe(s.publisher.id);
    expect(ex.publication.prepared_by).toBe(s.preparer.id);
    expect(ex.publication.template).toBe('decl-wft-fb-build-publish');

    // 6. the twin thread resolves to the ledger row
    await sends(pp, 'GET', /^\/factory-builds\/api\/twin$/, () => pp.click('#fb-thread'));
    await expect(pp.locator('#fb-thread-msg')).toContainText(`published by ${s.publisher.id}`, { timeout: T });
    await expect(pp.locator('#fb-thread-msg')).toContainText(image.digest.slice(7, 23));

    // 7. the model target: its AI-BOM names the fine-tuning registry promotion it rests on
    await pp.goto(MODEL);
    await expect(pp.locator('#fb-sbom')).toContainText('AI-BOM: machine-learning-model syn-foundry/defect-classifier');
    await expect(pp.locator('#fb-promotion')).toContainText('promoted');
    await expect(pp.locator('#fb-promotion')).toContainText(`syn-ent03-ml-lead-${s.run}`);
    await expect(pp.locator('#fb-promotion')).toContainText('OpenSSF model-signing');
    await expect(pp.locator('#fb-files')).toContainText('bundles/model.sigstore.json');

    // 8. another organisation sees no target, and the target's route says only "no such target"
    const outsider = await as(browser, s.outsider, '/factory-builds/');
    await expect(outsider.page.locator('#fb-held')).toContainText('2 target(s) not shown', { timeout: T });
    await expect(outsider.page.locator('tr[data-target="syn-ent03-portal"]')).toHaveCount(0);
    const hidden = await outsider.page.request.get('/factory-builds/api/targets/syn-ent03-portal');
    expect(hidden.status()).toBe(404);
    expect((await hidden.json()).error).toBe('no such target');

    for (const p of [preparer, publisher, outsider]) await p.ctx.close();
  });

  for (const theme of ['dark', 'light']) {
    test(`the signed factory builds page in the ${theme} theme has no axe violations`, async ({ browser }) => {
      const { ctx, page } = await as(browser, s.publisher, MODEL, theme);
      await expect(page.locator('html')).toHaveAttribute('data-theme', theme);
      await expect(page.locator('#fb-target')).toBeVisible({ timeout: T });
      await page.addScriptTag({ path: AXE });
      const result = await page.evaluate(async () => {
        // @ts-expect-error axe is injected above
        const r = await window.axe.run(document.querySelector('.fb-wrap'),
          { runOnly: { type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'] } });
        return r.violations.map((v: { id: string; nodes: { target: string[]; failureSummary?: string }[] }) =>
          `${v.id}: ${v.nodes.slice(0, 3).map(n => `${n.target.join(' ')} [${(n.failureSummary || '').split('\n').pop()}]`).join('; ')}`);
      });
      expect(result, `axe violations on /factory-builds/ in the ${theme} theme`).toEqual([]);
      await ctx.close();
    });
  }
});

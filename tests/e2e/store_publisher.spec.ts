// CUI // SP-CTI
// E2E: the store publisher, a signed agent-store package published for ICDEV[OS] by a person (icx-ent-04).
//
// Three fresh, card-named people (tests/e2e/fixtures/ent04_seed.py, synthetic), each in its own browser context
// signed in by its own dashboard API key: ent04-preparer and ent04-publisher of the synthetic packages' organisation,
// and ent04-outsider of another. The e2e dashboard signs Agent Cards with a THROWAWAY publisher key the seed wrote
// under tests/e2e/fixtures (ICDEV_STORE_PUBLISHER_E2E_KEY; the shipped configuration names no key). The seed prepared
// the MCP server package with it. The journey, every write pressed on the page:
//
//   1. the preparer opens the agent package and presses Prepare and sign: the seven scanner gates run on it and the
//      publisher's key signs its Agent Card;
//   2. the prepared version shows: its card's skills (its declared tools) and the content it binds, the self-check
//      re-run on this read, each gate's result (SAST not applicable to an agent), waiting for a person;
//   3. the preparer is refused publishing their own package (separation of duties);
//   4. the publisher, another person of the org, publishes it with a reason;
//   5. the published version shows; the export route returns {bundle, publication} as ICDEV[OS] imports it;
//   6. its twin thread resolves to the ledger row;
//   7. the publisher opens the MCP server package: its source passed the SAST gate;
//   8. the outsider sees no package (two held back) and the package's route is "no such package".
//
// Then the page in both themes (dark, light) with axe (the vendored axe-core) over its content.

import { test, expect } from './fixtures/observed_calls';
import type { Browser, BrowserContext, Page, Request } from '@playwright/test';
import { spawnSync } from 'child_process';
import path from 'path';
import { PYTHON, icdevSubprocessEnv } from './fixtures/subprocess_env';

const ROOT = path.resolve(__dirname, '../..');
const SEED = path.join(ROOT, 'tests/e2e/fixtures/ent04_seed.py');
const AXE = path.join(ROOT, 'tools/testing/vendor/axe/axe.min.js');
const T = 60_000;
const AGENT = '/store-publisher/?package=syn-ent04-ledger-agent';
const MCP = '/store-publisher/?package=syn-ent04-coverage-mcp';

type Person = { id: string; name: string; role: string; key: string; org: string };
type Prepared = { digest: string; event_id: string };
type Seeded = { run: string; preparer: Person; publisher: Person; outsider: Person;
  prepared: { 'syn-ent04-coverage-mcp': Prepared } };

function seed(): Seeded {
  // The seeder writes to the database the RUN asked for, never an ambient one.
  const res = spawnSync(PYTHON, [SEED], {
    cwd: ROOT, encoding: 'utf8', env: icdevSubprocessEnv({ PYTHONIOENCODING: 'utf-8' }),
  });
  if (res.status !== 0) throw new Error(`ent04_seed.py failed: ${res.stderr || res.stdout}`);
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

test.describe('Store publisher: a signed agent-store package published for ICDEV[OS] by another person (icx-ent-04)', () => {
  test.describe.configure({ mode: 'serial' });
  let s: Seeded;

  test.beforeAll(() => { s = seed(); });

  test('prepare and sign on the page, refuse the preparer, publish, export, open the thread, see the MCP gates, hide it from another org', async ({ browser }) => {
    test.setTimeout(300_000);
    // 1. the preparer opens the agent package and prepares and signs it
    const preparer = await as(browser, s.preparer, AGENT);
    const page = preparer.page;
    await expect(page.locator('#sp-help')).toContainText('A2A Agent Card signed with JWS');
    await expect(page.locator('#sp-help')).toContainText('seven marketplace scanner gates');
    await expect(page.locator('#sp-signing')).toContainText('configured');
    await expect(page.locator('#sp-package')).toHaveAttribute('data-package', 'syn-ent04-ledger-agent');
    const version = `1.0.${parseInt(s.run.slice(0, 4), 16)}`;
    await page.fill('#sp-version', version);
    await page.fill('#sp-note', 'ent04 e2e: prepare and sign the synthetic agent');
    await sends(page, 'POST', /^\/store-publisher\/api\/packages\/syn-ent04-ledger-agent\/prepare$/, () => page.click('#sp-prepare'));

    // 2. the prepared version shows, waiting for a person
    await expect(page.locator('#sp-prepared')).toContainText(`prepared by ${s.preparer.id}`, { timeout: T });
    await expect(page.locator('#sp-prepared')).toContainText(`Version ${version}`);
    await expect(page.locator('#sp-prepared')).toContainText('waiting for a person');
    const digest = (await page.locator('#sp-digest').getAttribute('data-digest')) || '';
    expect(digest).toMatch(/^[0-9a-f]{64}$/);
    await expect(page.locator('#sp-self-check')).toContainText('verified now');
    await expect(page.locator('#sp-card')).toContainText('skills: twin_coverage, sda_list');
    await expect(page.locator('tr[data-gate="sast_scan"]')).toContainText('not applicable');
    await expect(page.locator('tr[data-gate="secret_detection"]')).toContainText('pass');
    await expect(page.locator('tr[data-gate="digital_signature"]')).toContainText('pass');

    // 3. the preparer is refused publishing their own package
    await page.fill('#sp-reason', 'ent04 e2e: my own (must be refused)');
    await sends(page, 'POST', /^\/store-publisher\/api\/packages\/syn-ent04-ledger-agent\/publish$/,
      () => page.locator('button[data-publish="publish"]').click());
    await expect(page.locator('#sp-publish-msg')).toContainText('Refused (separation)', { timeout: T });

    // 4. the publisher, another person of the org, publishes it
    const publisher = await as(browser, s.publisher, AGENT);
    const pp = publisher.page;
    await expect(pp.locator('#sp-builders')).toContainText(s.preparer.id);
    await pp.fill('#sp-reason', 'ent04 e2e: read the card, gates, SBOM and red-team summary, publish (synthetic)');
    await sends(pp, 'POST', /^\/store-publisher\/api\/packages\/syn-ent04-ledger-agent\/publish$/,
      () => pp.locator('button[data-publish="publish"]').click());

    // 5. the published version; the export route returns it with its publication beside it
    await expect(pp.locator('#sp-published')).toContainText(`published by ${s.publisher.id}`, { timeout: T });
    await expect(pp.locator('#sp-decided')).toContainText('published');
    const href = await pp.locator('#sp-export-link').getAttribute('href');
    expect(href).toBe('/store-publisher/api/packages/syn-ent04-ledger-agent/export');
    const resp = await pp.request.get(href || '');
    expect(resp.ok()).toBeTruthy();
    const ex = await resp.json();
    expect(Object.keys(ex).sort()).toEqual(['bundle', 'publication']);
    expect(ex.bundle.schema).toBe('icos-store-package/v1');
    expect(ex.bundle.content_digest).toBe(digest);
    expect(ex.bundle.agent_card.signatures.length).toBe(1);
    expect(ex.bundle.agent_card.skills.map((x: { id: string }) => x.id)).toEqual(ex.bundle.tools);
    expect(ex.bundle.agent_card.metadata['icos.store/package_digest']).toMatch(/^[0-9a-f]{64}$/);
    expect(Object.keys(ex.bundle.scan).sort()).toEqual(['cui_marking_validation', 'dependency_audit', 'digital_signature',
      'sast_scan', 'sbom_generation', 'secret_detection', 'supply_chain_provenance']);
    expect(ex.publication.schema).toBe('icos-store-publication/v1');
    expect(ex.publication.content_digest).toBe(digest);
    expect(ex.publication.signed_by).toBe(s.publisher.id);
    expect(ex.publication.prepared_by).toBe(s.preparer.id);
    expect(ex.publication.template).toBe('icos-store-publish');

    // 6. the twin thread resolves to the ledger row
    await sends(pp, 'GET', /^\/store-publisher\/api\/twin$/, () => pp.click('#sp-thread'));
    await expect(pp.locator('#sp-thread-msg')).toContainText(`published by ${s.publisher.id}`, { timeout: T });
    await expect(pp.locator('#sp-thread-msg')).toContainText(digest.slice(0, 16));

    // 7. the MCP server package the seed prepared: its source passed the SAST gate
    await pp.goto(MCP);
    await expect(pp.locator('#sp-prepared')).toContainText(`prepared by ${s.preparer.id}`, { timeout: T });
    await expect(pp.locator('#sp-card')).toContainText('skills: coverage_lookup');
    await expect(pp.locator('tr[data-gate="sast_scan"]')).toContainText('pass');

    // 8. another organisation sees no package, and the package's route says only "no such package"
    const outsider = await as(browser, s.outsider, '/store-publisher/');
    await expect(outsider.page.locator('#sp-held')).toContainText('2 package(s) not shown', { timeout: T });
    await expect(outsider.page.locator('tr[data-package="syn-ent04-ledger-agent"]')).toHaveCount(0);
    const hidden = await outsider.page.request.get('/store-publisher/api/packages/syn-ent04-ledger-agent');
    expect(hidden.status()).toBe(404);
    expect((await hidden.json()).error).toBe('no such package');

    for (const p of [preparer, publisher, outsider]) await p.ctx.close();
  });

  for (const theme of ['dark', 'light']) {
    test(`the store publisher page in the ${theme} theme has no axe violations`, async ({ browser }) => {
      const { ctx, page } = await as(browser, s.publisher, MCP, theme);
      await expect(page.locator('html')).toHaveAttribute('data-theme', theme);
      await expect(page.locator('#sp-package')).toBeVisible({ timeout: T });
      await page.addScriptTag({ path: AXE });
      const result = await page.evaluate(async () => {
        // @ts-expect-error axe is injected above
        const r = await window.axe.run(document.querySelector('.sp-wrap'),
          { runOnly: { type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'] } });
        return r.violations.map((v: { id: string; nodes: { target: string[]; failureSummary?: string }[] }) =>
          `${v.id}: ${v.nodes.slice(0, 3).map(n => `${n.target.join(' ')} [${(n.failureSummary || '').split('\n').pop()}]`).join('; ')}`);
      });
      expect(result, `axe violations on /store-publisher/ in the ${theme} theme`).toEqual([]);
      await ctx.close();
    });
  }
});

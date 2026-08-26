// CUI // SP-CTI
// E2E Lifecycle Test: NOC Operations Canvas (NOCC) — cnr-ops-02
// Covers page loads, JSON API GETs, and the mutation lifecycles (alarm
// ingest -> ack -> clear; incident create -> update; RFC create; MOP generate),
// plus the hardened looking-glass not-configured state. Runs authenticated
// against a live dashboard, so the cnr-ops-01 fail-closed mutation gate is
// satisfied by the login session.

import { test, expect } from './fixtures/auth';
import { resolveBaseUrl } from './fixtures/base_url';

// ONE resolver, shared with playwright.config.ts and fixtures/auth.ts, so the
// origin these absolute URLs address is the origin the CSRF/session cookies
// were minted at. Two copies of the precedence 403'd every mutating request in
// the suite (qa-fail-a5dbf266dfb0ce4a). See fixtures/base_url.ts.
const BASE = resolveBaseUrl();
const CUI = 'CUI // SP-CTI';
const SS = 'playwright/screenshots';

async function goto(page: any, path: string) {
  await page.goto(`${BASE}${path}`, { waitUntil: 'domcontentloaded', timeout: 20000 });
}
async function shot(page: any, name: string) {
  await page.screenshot({ path: `${SS}/noc-e2e-${name}.png`, fullPage: false });
}

// ── Suite 1: Page loads (all sub-pages 200 + CUI banner) ─────────────────────

test.describe('NOCC — Page Load Lifecycle', () => {
  const subpages = [
    { path: '/noc',              name: 'overview',    keyword: 'noc' },
    { path: '/noc/alarms',       name: 'alarms',      keyword: 'alarm' },
    { path: '/noc/incidents',    name: 'incidents',   keyword: 'incident' },
    { path: '/noc/rfcs',         name: 'rfcs',        keyword: 'rfc' },
    { path: '/noc/mops',         name: 'mops',        keyword: 'mop' },
    { path: '/noc/maintenance',  name: 'maintenance', keyword: 'maintenance' },
    { path: '/noc/sla',          name: 'sla',         keyword: 'sla' },
  ];
  for (const { path, name, keyword } of subpages) {
    test(`${name} page loads with CUI banner`, async ({ page }) => {
      await goto(page, path);
      await shot(page, `01-load-${name}`);
      const body = await page.textContent('body');
      expect(body).toBeTruthy();
      expect(body).toContain(CUI);
      expect(body?.toLowerCase()).toContain(keyword.toLowerCase());
    });
  }
});

// ── Suite 2: API GETs return JSON ────────────────────────────────────────────

test.describe('NOCC — Read APIs', () => {
  const gets = [
    { path: '/api/noc/overview', name: 'overview' },
    { path: '/api/noc/alarms',   name: 'alarms', prop: 'alarms' },
    { path: '/api/noc/sla',      name: 'sla' },
  ];
  for (const { path, name, prop } of gets) {
    test(`GET ${path} returns JSON`, async ({ page }) => {
      const resp = await page.goto(`${BASE}${path}`, { waitUntil: 'domcontentloaded' });
      expect(resp?.status()).toBe(200);
      const json = JSON.parse((await page.textContent('body')) || '{}');
      if (prop) expect(json).toHaveProperty(prop);
    });
  }
});

// ── Suite 3: Alarm lifecycle — ingest -> ack -> clear ────────────────────────

test.describe('NOCC — Alarm Lifecycle', () => {
  test('ingest, acknowledge, then clear an alarm', async ({ page, request }) => {
    // Ingest (POST is auth-gated; page context carries the login session cookie).
    const create = await page.request.post(`${BASE}/api/noc/alarms`, {
      data: { alarm_source: 'e2e-nms', description: 'E2E synthetic alarm', severity: 'major' },
    });
    expect(create.status()).toBe(201);
    const { id } = await create.json();
    expect(id).toBeTruthy();

    const ack = await page.request.post(`${BASE}/api/noc/alarms/${id}/ack`, { data: { user_id: 'e2e' } });
    expect(ack.status()).toBe(200);
    expect((await ack.json()).success).toBeTruthy();

    const clear = await page.request.post(`${BASE}/api/noc/alarms/${id}/clear`, { data: {} });
    expect(clear.status()).toBe(200);

    await goto(page, '/noc/alarms');
    await shot(page, '03-alarm-lifecycle');
  });

  test('alarm ingest with no body is rejected 400 (not 401)', async ({ page }) => {
    const resp = await page.request.post(`${BASE}/api/noc/alarms`, { data: {} });
    expect(resp.status()).toBe(400);
  });
});

// ── Suite 4: Incident lifecycle — create -> update ───────────────────────────

test.describe('NOCC — Incident Lifecycle', () => {
  test('create an incident then update its status', async ({ page }) => {
    const create = await page.request.post(`${BASE}/api/noc/incidents`, {
      data: { title: 'E2E outage', severity: 'p2' },
    });
    expect(create.status()).toBe(201);
    const { id, incident_number } = await create.json();
    expect(id).toBeTruthy();
    expect(incident_number).toMatch(/^INC-/);

    const upd = await page.request.put(`${BASE}/api/noc/incidents/${id}`, {
      data: { status: 'investigating', assigned_to: 'e2e-oncall' },
    });
    expect(upd.status()).toBe(200);

    await goto(page, '/noc/incidents');
    await shot(page, '04-incident-lifecycle');
    const body = await page.textContent('body');
    expect(body).toContain(CUI);
  });
});

// ── Suite 5: RFC create + MOP generate ───────────────────────────────────────

test.describe('NOCC — RFC + MOP', () => {
  test('create an RFC then generate a MOP', async ({ page }) => {
    const rfc = await page.request.post(`${BASE}/api/noc/rfcs`, {
      data: { title: 'E2E firmware upgrade', change_type: 'standard', risk_level: 'medium' },
    });
    expect(rfc.status()).toBe(201);
    const { id: rfcId, rfc_number } = await rfc.json();
    expect(rfc_number).toMatch(/^RFC-/);

    const mop = await page.request.post(`${BASE}/api/noc/mops/generate`, {
      data: { rfc_id: rfcId, title: 'E2E firmware upgrade', context: 'upgrade core routers' },
    });
    expect(mop.status()).toBe(201);
    const mopJson = await mop.json();
    expect(mopJson).toHaveProperty('mop_id');
    expect(Array.isArray(mopJson.steps)).toBe(true);

    await goto(page, '/noc/mops');
    await shot(page, '05-rfc-mop');
  });
});

// ── Suite 6: Looking-glass hardening (cnr-ops-02) ────────────────────────────

test.describe('NOCC — Looking Glass', () => {
  test('looking-glass renders (not-configured or configured) without a broken iframe', async ({ page }) => {
    await goto(page, '/noc/looking-glass');
    await shot(page, '06-looking-glass');
    const body = await page.textContent('body');
    expect(body?.toLowerCase()).toContain('looking glass');
    // When HYPERGLASS_URL is unset/invalid the friendly ad-hoc-lookup panel shows,
    // never a javascript:/data: iframe src.
    const iframes = await page.$$eval('iframe', (els: Element[]) =>
      els.map((el: any) => el.getAttribute('src') || ''));
    for (const src of iframes) {
      expect(src.startsWith('javascript:')).toBe(false);
      expect(src.startsWith('data:')).toBe(false);
    }
  });
});

// CUI // SP-CTI

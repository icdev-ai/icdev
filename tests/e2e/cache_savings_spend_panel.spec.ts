// CUI // SP-CTI
// E2E Test: the Spend panel on the EXISTING /cache-savings page (xrv-cost-04).
//
// This is a SECTION on a page that already shipped, not a new page, so the
// 8-point page gate does not apply and this smoke asserts exactly three things:
// the section renders inside the real page chrome, the API behind it is GET
// only, and NO empty state is ever drawn as a dollar figure of zero.
//
// That last one is the whole card. A cost surface that renders `$0.00` over a
// board nothing attributed is claiming the work was free — and unlike a missing
// number, a zero closes the question instead of prompting anyone to go and
// measure. The panel is allowed to say "unmeasurable" and is allowed to say a
// real total; it is not allowed to say zero dollars for something unmeasured.

// `test`/`expect` come from ./fixtures/auth rather than @playwright/test for the
// same reason every other spec here does: a locally started dashboard applies
// csrf_protect to a raw APIRequestContext. See that file.
import { test, expect } from './fixtures/auth';
import { resolveBaseUrl } from './fixtures/base_url';

const BASE = resolveBaseUrl();
const PAGE = `${BASE}/cache-savings`;
const API = `${BASE}/api/cache-savings/spend`;

// THE PANEL HAS TWO HONEST RENDERINGS AND CI ONLY EVER SEES ONE (xrv-cost-05).
// `measured` draws the KPI row and the five-verdict table; `unmeasurable` draws
// a worded block instead, because `$0.00` over an unattributed board would claim
// the work was free — which is the whole card. A CI runner boots a fresh
// database with no dispatches, so it is STRUCTURALLY unmeasurable, while the
// live board this was written against has rows. Asserting the measured shape
// unconditionally therefore passed locally and failed on every runner.
// So these tests ask the API which rendering to expect, exactly as the two
// tests at the bottom of this file already do. That is not a weakening: the
// invariant the card exists for — no unmeasured figure drawn as zero dollars —
// is asserted in BOTH branches, and each branch still asserts its own shape in
// full.
async function panelState(page): Promise<{ state: string; headline: string }> {
  const resp = await page.request.get(API);
  expect(resp.status(), `GET ${API} returned ${resp.status()}`).toBe(200);
  const payload = await resp.json();
  expect(['measured', 'unmeasurable']).toContain(payload.state);
  return payload;
}

test.describe('Cache Savings — Spend panel', () => {
  test.beforeEach(async ({ page }) => {
    await page.addInitScript(() => {
      localStorage.setItem('icdev_tour_completed', '1');
      localStorage.setItem('icdev_tour_last_step', '999');
    });
  });

  test('the Spend section renders on the existing page', async ({ page }) => {
    const resp = await page.request.get(PAGE);
    expect(resp.status(), `GET /cache-savings returned ${resp.status()}`).toBeLessThan(400);

    await page.goto(PAGE);
    await page.waitForLoadState('domcontentloaded');

    const body = (await page.textContent('body')) ?? '';
    expect(body).not.toContain('Internal Server Error');
    expect(body).not.toContain('Traceback');
    expect(body).toContain('Spend by Card');

    const payload = await panelState(page);
    if (payload.state === 'measured') {
      // The four figures the panel exists to carry.
      for (const label of ['Attributed Spend', 'Spend That Shipped',
                           'Unpriced Dispatches', 'Unmeasurable Cards']) {
        expect(body, `missing KPI: ${label}`).toContain(label);
      }
    } else {
      // Unmeasurable renders IN WORDS and says why. An empty board must never
      // reach a dollar figure, and it must never render as silence either —
      // a panel that simply omits its numbers is indistinguishable from one
      // that measured nothing worth showing.
      const section = body.split('Spend by Card')[1] ?? '';
      expect(section, 'an unmeasurable panel must say so').toContain('Unmeasurable');
      // The HEADLINE, not one reason's prose: `no_attributed_rows`,
      // `ledger_unreadable` and `unavailable` render different sentences, and
      // pinning one of them would make this pass or fail on which way the
      // panel happened to be unmeasurable.
      expect(section, 'an unmeasurable panel must state why')
        .toContain(payload.headline);
      expect(section, 'an unmeasured figure rendered as zero dollars')
        .not.toContain('$0.00');
    }

    // The SECTION, not the whole page: this file is committed as the card's
    // evidence, and a full-page shot of /cache-savings is mostly the two
    // caching tables the panel sits below.
    const section = page.locator('#spend-by-card');
    await expect(section).toBeVisible();
    // The dashboard's floating chrome (the CLI-bridge panel, the IQE launcher)
    // is position:fixed, so an element shot of a section near the bottom of the
    // page captures the overlay sitting on the panel's own footnote. This is
    // for the committed EVIDENCE only and asserts nothing.
    await page.addStyleTag({
      content: '.clib-panel, .iqe-launcher, #iqe-fab, .icdev-fab { display: none !important; }',
    });
    await section.screenshot({
      path: 'playwright/screenshots/xrv-cost-04-spend-panel.png',
    });
  });

  test('all five outcomes are named, so a missing one cannot read as zero',
       async ({ page }) => {
    await page.goto(PAGE);
    await page.waitForLoadState('domcontentloaded');
    const section = ((await page.textContent('body')) ?? '').split('Spend by Card')[1] ?? '';

    const payload = await panelState(page);
    if (payload.state === 'measured') {
      // A verdict absent from the table is indistinguishable from one that
      // measured zero, so the closed set always renders in full.
      for (const verdict of ['Shipped', 'Reverted', 'Abandoned', 'In flight',
                             'Unmeasurable']) {
        expect(section, `missing outcome row: ${verdict}`).toContain(verdict);
      }
      // Each one states what it MEANS: a bare label beside a dollar figure
      // invites the reader to invent the definition.
      expect(section).toContain('landed on the default branch');
    } else {
      // There is no table to be missing a row from — and the panel accounts
      // for its own absence in words rather than drawing an empty one, which
      // is the same claim this test makes on the measured side.
      expect(section, 'an unmeasurable panel must say so').toContain('Unmeasurable');
      expect(section, 'an unmeasurable panel must state why')
        .toContain(payload.headline);
    }
  });

  test('the API is GET only and reports its own cache age', async ({ page }) => {
    const resp = await page.request.get(API);
    expect(resp.status()).toBe(200);
    const payload = await resp.json();

    expect(['measured', 'unmeasurable']).toContain(payload.state);
    expect(typeof payload.cache_age_seconds).toBe('number');
    expect(payload.cache_ttl_seconds).toBe(120);
    expect(payload.by_verdict).toHaveLength(5);

    // No POST sibling: the panel reports what the ledger and git already say.
    const posted = await page.request.post(API, { failOnStatusCode: false });
    expect(posted.status()).toBe(405);

    // A malformed window is refused rather than silently defaulted.
    const bad = await page.request.get(`${API}?window_days=abc`,
                                       { failOnStatusCode: false });
    expect(bad.status()).toBe(400);
  });

  test('an unmeasured figure is never rendered as zero dollars', async ({ page }) => {
    const payload = await (await page.request.get(API)).json();

    // The contract, asserted against whatever this deployment actually holds:
    // every figure is either a real measurement or null — never 0 standing in
    // for "nobody measured".
    if (payload.state === 'unmeasurable') {
      expect(payload.total_cost_usd).toBeNull();
      expect(payload.shipped_cost_share_pct).toBeNull();
      expect(payload.headline).toContain('no attributed dispatches');
    }
    for (const row of payload.by_verdict) {
      if (row.tasks === 0) {
        expect(row.cost_usd,
               `${row.verdict} has no cards but carries a dollar figure`).toBeNull();
      }
      expect(row.note, `${row.verdict} renders without saying what it means`)
        .toBeTruthy();
    }

    await page.goto(PAGE);
    await page.waitForLoadState('domcontentloaded');
    const section = ((await page.textContent('body')) ?? '').split('Spend by Card')[1] ?? '';
    if (payload.state === 'unmeasurable') {
      expect(section).not.toContain('$0.00');
    }
  });
});

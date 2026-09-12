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
//
// THE LABEL ASSERTIONS ARE UNCONDITIONAL AND THAT IS DELIBERATE (xrv-cost-06).
// They were red on every CI board until the panel was fixed, because the
// template rendered the four KPIs and the five outcomes ONLY in the measured
// state while `shape()` had always handed it all five with every figure null
// (`_empty_verdicts`) and the API had always served them — the JSON and the
// page disagreed about what this panel carries. Making the spec conditional was
// the other option and was rejected: the closed set exists so that a verdict
// absent from the table cannot be read as one that measured zero, and that
// argument applies MOST on the board where nothing was measured. So the labels
// are a fact about the panel in both states, and what is state-dependent is the
// FIGURES — asserted below, where an unmeasured panel must carry none at all.

// `test`/`expect` come from ./fixtures/auth rather than @playwright/test for the
// same reason every other spec here does: a locally started dashboard applies
// csrf_protect to a raw APIRequestContext. See that file.
import { test, expect } from './fixtures/auth';
import { resolveBaseUrl } from './fixtures/base_url';

const BASE = resolveBaseUrl();
const PAGE = `${BASE}/cache-savings`;
const API = `${BASE}/api/cache-savings/spend`;

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

    // The four figures the panel exists to carry, asserted INSIDE the panel.
    // `body.split('Spend by Card')[1]` is everything after the heading — the
    // IQE widget and the page's own pricing footnote included — so a check
    // written against it is weaker than it reads.
    const panel = (await page.locator('#spend-by-card').textContent()) ?? '';
    for (const label of ['Attributed Spend', 'Spend That Shipped',
                         'Unpriced Dispatches', 'Unmeasurable Cards']) {
      expect(panel, `missing KPI: ${label}`).toContain(label);
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
    const section = (await page.locator('#spend-by-card').textContent()) ?? '';

    // A verdict absent from the table is indistinguishable from one that
    // measured zero, so the closed set always renders in full — in BOTH
    // states, with every figure withheld when nothing was measured.
    for (const verdict of ['Shipped', 'Reverted', 'Abandoned', 'In flight',
                           'Unmeasurable']) {
      expect(section, `missing outcome row: ${verdict}`).toContain(verdict);
    }
    // Each one states what it MEANS: a bare label beside a dollar figure
    // invites the reader to invent the definition.
    expect(section).toContain('landed on the default branch');
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
    const section = (await page.locator('#spend-by-card').textContent()) ?? '';
    if (payload.state === 'unmeasurable') {
      // Scoped to the panel, the assertion can be the STRONG one: not merely
      // "no $0.00" but no dollar figure and no percentage anywhere in it. The
      // closed set renders here (above), so this is what proves that rendering
      // it costs nothing — every row and every KPI is an em-dash.
      expect(section).not.toContain('$0.00');
      expect(section, 'an unmeasured panel drew a dollar figure').not.toMatch(/\$/);
      expect(section, 'an unmeasured panel drew a percentage').not.toMatch(/\d\s*%/);

      // And the captions do not carry the measured state's claim across: the
      // unpriced story ("dispatches ran, none reported a price") is a DIFFERENT
      // finding from "nothing was attributed", with a different fix.
      expect(section).not.toContain('no dispatch reported a price');
      expect(section).toContain('nothing in this window was attributed to a card');
    } else {
      // The other half of the same contract: a measured panel is not allowed to
      // hide behind em-dashes either.
      expect(section).toMatch(/\$\d/);
    }
  });
});

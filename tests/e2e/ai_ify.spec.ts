// CUI // SP-CTI
// E2E Test: AI-ify Canvas (formerly "AI Augmentation Canvas" / AAC) — page
// renders, the guided scan wizard is present, the scan API rejects empty input,
// and the legacy /ai-augmentation route redirects to /ai-ify.

// `test`/`expect` come from ./fixtures/auth, not @playwright/test: the scan POST
// below 403s CSRF_FAILED against a locally started dashboard otherwise, because
// ICDEV_DASHBOARD_DEV_AUTOLOGIN gives every request a cookie session and
// `csrf_protect` then demands a token the raw APIRequestContext does not carry.
// CI hides it with ICDEV_AUTH_BYPASS. See that file for why. tsh-e2e-01-d2.
import { test, expect } from './fixtures/auth';
import { BASE_URL as BASE } from './fixtures/base_url';

// Same precedence as `playwright.config.ts`'s DASHBOARD_URL, deliberately: this
// spec-local constant is NOT covered by globalSetup's reachability assert, which
// probes the CONFIGURED baseURL. Reading ICDEV_DASHBOARD_URL alone meant a run
// launched with ICDEV_E2E_BASE_URL still sent these five tests at the container
// gateway and burned a timeout each, with nothing failing once to say why
// (qa-fail-e2e-baseurl-01). That fix was applied HERE and nowhere else, so the
// copies in fixtures/auth.ts and fixtures/govcon_cpmp.ts kept the defect and it
// resurfaced as 403 CSRF_FAILED (qa-fail-84f92cebcf4fe498). The expression now
// has one home and this file imports it.
const AIIFY = `${BASE}/ai-ify/`;

test.describe('AI-ify Canvas', () => {
  test.beforeEach(async ({ page }) => {
    await page.addInitScript(() => {
      localStorage.setItem('icdev_tour_completed', '1');
      localStorage.setItem('icdev_tour_last_step', '999');
    });
  });

  test('HTTP 200 — page loads without server error', async ({ page }) => {
    const resp = await page.request.get(AIIFY);
    expect(resp.status(), `GET /ai-ify/ returned ${resp.status()}`).toBeLessThan(400);

    await page.goto(AIIFY);
    await page.waitForLoadState('domcontentloaded');

    const body = (await page.textContent('body')) ?? '';
    expect(body).not.toContain('Internal Server Error');
    expect(body).not.toContain('Traceback');
    expect(body).not.toContain('werkzeug');

    await page.screenshot({
      path: 'playwright/screenshots/ai_ify_01_page_load.png',
      fullPage: true,
    });
  });

  test('CUI banner is visible', async ({ page }) => {
    await page.goto(AIIFY);
    await page.waitForLoadState('domcontentloaded');
    const body = (await page.textContent('body')) ?? '';
    expect(body).toContain('CUI');
  });

  test('page heading and description are present', async ({ page }) => {
    await page.goto(AIIFY);
    await page.waitForLoadState('domcontentloaded');
    const body = (await page.textContent('body')) ?? '';
    expect(body).toContain('AI-ify');
    expect(body).toContain('Codebase AI Opportunity Assessment');
  });

  test('scan wizard elements are present', async ({ page }) => {
    await page.goto(AIIFY);
    await page.waitForLoadState('domcontentloaded');

    // Wizard steps may be display:none until navigated, so assert presence in
    // the DOM rather than visibility.
    await expect(page.locator('#stepper')).toBeAttached();
    await expect(page.locator('#ilLevel')).toBeAttached();
    await expect(page.locator('#runBtn')).toBeAttached();
  });

  test('IQE widget is attached', async ({ page }) => {
    await page.goto(AIIFY);
    await page.waitForLoadState('domcontentloaded');
    const widget = page.locator('.iqe-widget, #iqe-minibar, [data-api], [id*="iqe"]');
    await expect(widget.first()).toBeAttached();
  });

  test('scan API returns 400 for empty input_ref', async ({ request }) => {
    // Relative path on purpose: `request` is bootstrapped against Playwright's
    // configured baseURL, and that is the origin its CSRF cookie belongs to.
    const resp = await request.post('/ai-ify/api/scan', {
      data: { input_type: 'local_path', input_ref: '', il_level: 'il4' },
    });
    expect(resp.status()).toBe(400);
    const body = await resp.json();
    expect(body.error).toBeTruthy();
  });

  test('legacy /ai-augmentation redirects to /ai-ify', async ({ page }) => {
    const resp = await page.request.get(`${BASE}/ai-augmentation/`, { maxRedirects: 0 });
    expect([301, 302, 307, 308]).toContain(resp.status());
    expect(resp.headers()['location'] ?? '').toContain('/ai-ify');
  });
});
// CUI // SP-CTI

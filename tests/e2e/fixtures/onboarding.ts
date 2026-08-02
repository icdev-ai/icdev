// CUI // SP-CTI
/**
 * ICDEV™ E2E onboarding-overlay suppression + login helper (tsh-e2e-01-d3)
 *
 * THE FAILURE THIS FIXES
 * ----------------------
 * `genesis.spec.ts` and `proposal_genesis.spec.ts` failed all 15 of their tests
 * locally — every one of them inside `beforeEach`, on the same call:
 *
 *   TimeoutError: locator.fill: Timeout 10000ms exceeded.
 *     waiting for locator('input[type="password"], input[name="api_key"], …').first()
 *     locator resolved to <input id="onb-apikey" type="password" …>
 *     2 × waiting for element to be visible, enabled and editable
 *       - element is not visible
 *
 * The specs open `/login`, and their "fill the API key if this build asks for
 * one" step used a deliberately broad locator. `/login` 302s straight to `/`
 * under dev auto-login, so what that locator actually resolved to was the
 * onboarding wizard's API-key box — `#onb-apikey`, inside `#onb-backdrop`, which
 * is `display:none` until the wizard opens. `count() > 0` was true, so the
 * `if` fired; `fill()` then waited the full `actionTimeout` for an element that
 * is never going to become visible on its own.
 *
 * WHY CI PASSES AND LOCAL DOES NOT
 * --------------------------------
 * `base.html` skips the wizard include entirely when `auth_bypass` is set:
 *
 *   {% if not auth_bypass %}
 *   {% include "includes/_onboarding_wizard.html" ignore missing %}
 *   {% endif %}
 *
 * CI sets `ICDEV_AUTH_BYPASS: "true"` in `jobs.e2e.env`, so `#onb-apikey` is not
 * in the DOM there at all, `count()` is 0, and the whole login block no-ops.
 * Locally `playwright.config.ts` sets the same flag — but only in
 * `webServer.env`, which applies **only when Playwright starts the dashboard
 * itself**. `reuseExistingServer: true` means an already-running dashboard (from
 * `/start`, the kanban runner, or a plain `python tools/dashboard/app.py`) is
 * used as-is, and that server renders the wizard. Same class of local/CI split
 * as the CSRF one in `fixtures/auth.ts`.
 *
 * Verified against the live local dashboard before writing this:
 *
 *   GET /login                    → 302 to /
 *   GET /            | grep       → 2 × input[type="password"], both id="onb-…"
 *   GET /api/onboarding/state     → {"completed": true, …}
 *
 * THE FIX
 * -------
 * Two independent problems, so two parts:
 *
 *   1. `loginIfPrompted()` only ever touches an input a **user could actually
 *      type into** — `:visible`, and never one belonging to the wizard
 *      (`:not([id^="onb-"])`). A hidden field can no longer make the suite wait
 *      ten seconds for it. This alone fixes the reported timeout.
 *
 *   2. `suppressOnboarding()` guarantees the overlay never opens in the first
 *      place. Part 1 is enough on a dashboard whose user has finished
 *      onboarding, which is what a developer machine usually has; it is NOT
 *      enough on a fresh database, where `completed` is false, the wizard opens
 *      on DOMContentLoaded and `#onb-backdrop` (`position:fixed; inset:0;
 *      z-index:10500`) covers the viewport — so every `.click()` in these specs
 *      would then fail as intercepted. Suppression is what makes the specs pass
 *      against *any* dashboard rather than only a broken-in-the-right-way one.
 *
 * WHY STUB THE STATE ENDPOINT RATHER THAN CLICK "Skip setup"
 * ----------------------------------------------------------
 * Clicking the real button is the more authentic dismissal, but the wizard only
 * renders itself after `GET /api/onboarding/state` resolves — so a test would
 * have to poll for an overlay that usually never appears, and pay that wait on
 * every navigation. Answering the same GET with `completed: true` reaches the
 * identical end state (both `_onboarding_wizard.html`'s inline script and
 * `static/js/onboarding.js` gate `_show()` on `completed === false`) with no
 * race and no polling. Only GET is intercepted; a PATCH still goes to the real
 * server, so nothing here can hide a broken write path.
 *
 * This is a no-op in CI: with `ICDEV_AUTH_BYPASS` there is no wizard to
 * suppress, and the stubbed GET is one the page never issues.
 *
 * USAGE
 * -----
 *   import { suppressOnboarding, loginIfPrompted } from './fixtures/onboarding';
 *
 *   test.beforeEach(async ({ page }) => {
 *     await suppressOnboarding(page);   // BEFORE the first goto — installs an init script
 *     await loginIfPrompted(page, BASE);
 *   });
 */

import type { Page } from '@playwright/test';

/** The wizard's full-viewport backdrop; `.onb-open` is what makes it cover the page. */
export const ONBOARDING_BACKDROP = '#onb-backdrop';

/** Endpoint both wizard implementations read to decide whether to open. */
export const ONBOARDING_STATE_ROUTE = '**/api/onboarding/state';

/**
 * Inputs that count as a real login prompt.
 *
 * `:visible` is the load-bearing part — it is what stops a hidden field from
 * costing a 10s `actionTimeout`. `:not([id^="onb-"])` is belt-and-braces: if a
 * future change ever leaves the wizard open, the login step must still not type
 * a dashboard password into the onboarding form's API-key box.
 */
const LOGIN_KEY_SELECTOR = [
  'input[name="api_key"]',
  'input#api_key',
  'input[type="password"]',
  'input[placeholder*="key" i]',
]
  .map((sel) => `${sel}:visible:not([id^="onb-"])`)
  .join(', ');

/** Matches `tests/e2e/tour-suppressed-state.json`, which most other specs use. */
const TOUR_KEYS: Record<string, string> = {
  icdev_tour_completed: '1',
  icdev_tour_last_step: '999',
};

/**
 * Make both first-run overlays — the guided tour and the onboarding wizard —
 * unable to open for the rest of this page's life.
 *
 * Call once per `page`, before the first `goto`: `addInitScript` applies to
 * navigations made after it is installed, not to one already in flight.
 */
export async function suppressOnboarding(page: Page): Promise<void> {
  await page.addInitScript((keys: Record<string, string>) => {
    try {
      for (const [k, v] of Object.entries(keys)) localStorage.setItem(k, v);
    } catch {
      /* storage can be blocked; the wizard stub below is the part that matters */
    }
  }, TOUR_KEYS);

  await page.route(ONBOARDING_STATE_ROUTE, async (route) => {
    if (route.request().method() !== 'GET') {
      await route.continue();
      return;
    }
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ completed: true, current_step: 0, steps_done: [] }),
    });
  });
}

/**
 * Fill and submit a login form **only if one is actually on screen**.
 *
 * A no-op under dev auto-login (`/login` redirects to `/`, nothing to fill) and
 * under `ICDEV_AUTH_BYPASS`, which is why both specs can keep a single
 * `beforeEach` that works locally and in CI.
 */
export async function loginIfPrompted(
  page: Page,
  baseUrl: string,
  apiKey = 'sparkpilot',
): Promise<void> {
  await page.goto(`${baseUrl}/login`);
  await page.waitForLoadState('domcontentloaded');

  const keyInput = page.locator(LOGIN_KEY_SELECTOR);
  if ((await keyInput.count()) === 0) return;

  await keyInput.first().fill(apiKey);

  const submit = page
    .getByRole('button', { name: /Login|Sign In|Submit/i })
    .or(page.locator('button[type="submit"]'));
  if ((await submit.count()) > 0) {
    await submit.first().click();
    await page.waitForLoadState('domcontentloaded');
  }
}

/**
 * Close anything left open over the page — an `onb-open` backdrop that got past
 * the stub, or a `<dialog open>` a previous test opened and did not close.
 *
 * Cheap and synchronous in the page, so it is safe to call after a navigation
 * without waiting on anything.
 */
export async function dismissOverlays(page: Page): Promise<void> {
  await page.evaluate((backdropSel: string) => {
    document.querySelector(backdropSel)?.classList.remove('onb-open');
    document.querySelectorAll('dialog[open]').forEach((d) => {
      const dialog = d as HTMLDialogElement;
      if (typeof dialog.close === 'function') dialog.close();
      else dialog.removeAttribute('open');
    });
  }, ONBOARDING_BACKDROP);
}
// CUI // SP-CTI

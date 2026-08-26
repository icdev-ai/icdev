// CUI // SP-CTI
/**
 * ICDEV™ E2E API auth bootstrap — CSRF double-submit (tsh-e2e-01-d2)
 *
 * THE FAILURE THIS FIXES
 * ----------------------
 * Every mutating API call made through Playwright's `request` fixture came back
 * `403 {"code":"CSRF_FAILED"}` locally while passing in CI. Measured on
 * `cpmp_portfolio.spec.ts` against a locally running dashboard: 10 failed,
 * 14 passed, and all ten failures were the same 403 on a POST or a PUT. Every
 * GET in the same spec returned 200 — so this was never a login problem, and
 * there is no pre-seeded auth file to copy from CI.
 *
 * WHY CI PASSES AND LOCAL DOES NOT
 * --------------------------------
 * `tools/security/csrf.py::csrf_protect` rejects a mutating request that carries
 * a cookie session but neither a matching `X-CSRF-Token` header nor a browser
 * `Sec-Fetch-Site` header. It returns early — no rejection — in two cases that
 * matter here:
 *
 *   1. `ICDEV_AUTH_BYPASS` is truthy.          ← what CI relies on
 *   2. `session['user_id']` is unset.          ← nothing to forge, nothing to check
 *
 * CI sets `ICDEV_AUTH_BYPASS: "true"` in `jobs.e2e.env`, so CSRF never engages
 * there. Locally, `playwright.config.ts` sets the same flag — but only inside
 * `webServer.env`, which applies **only when Playwright starts the dashboard
 * itself**. `reuseExistingServer: true` means a dashboard already listening on
 * the port is used as-is, and that server (started by `/start`, the kanban
 * runner, or a plain `python tools/dashboard/app.py`) has no bypass flag. It
 * does have dev auto-login, so `session['user_id']` IS set — which is precisely
 * the combination `csrf_protect` is built to reject.
 *
 * Confirmed against a live local dashboard before writing this file:
 *
 *   POST /api/cpmp/contracts                          → 403 CSRF_FAILED
 *   POST /api/cpmp/contracts  + X-CSRF-Token: <cookie> → 201
 *
 * THE FIX — DO WHAT THE APP'S OWN JAVASCRIPT DOES
 * -----------------------------------------------
 * `base.html` wraps `fetch`/`XMLHttpRequest` and attaches the `icdev_csrf`
 * cookie value as the `X-CSRF-Token` header on every same-origin mutating
 * request. `request` is a bare HTTP client, so it never ran that wrapper. This
 * module performs the same double-submit handshake:
 *
 *   1. GET `/` in a throwaway context — dev auto-login establishes the session,
 *      and the `after_request` hook in `csrf.py` issues the `icdev_csrf` cookie.
 *   2. Read that cookie out of the context's storage state.
 *   3. Build the real request context from the same cookie jar, with the token
 *      pinned as a default header.
 *
 * NOT taken: sending a bare `Sec-Fetch-Site: same-origin` header. The server
 * accepts it (branch 2 of `csrf_protect`) and it is one line, but it is a header
 * a browser stamps and page code cannot forge — spoofing it from a test client
 * would mean the suite could no longer tell a working CSRF implementation from a
 * broken one. The double-submit path is the one real clients actually use.
 *
 * NOT taken: setting `ICDEV_AUTH_BYPASS` for local runs. That would match CI,
 * but it matches CI by turning the check off — the local suite would keep
 * exercising strictly less of the app than it appears to. Bootstrapping a real
 * token means these specs now pass **with CSRF enforced**, which is more than
 * CI does today.
 *
 * WORKS IN BOTH ENVIRONMENTS
 * --------------------------
 * Under `ICDEV_AUTH_BYPASS` (CI) no `icdev_csrf` cookie is issued, no header is
 * attached, and `csrf_protect` exits before it would look for one — so this is a
 * no-op there rather than a second code path to keep working.
 *
 * USAGE — import `test`/`expect` from here instead of `@playwright/test`:
 *
 *   import { test, expect } from './fixtures/auth';
 *
 * Both `request` AND the browser `context` (so `page.request`) are then
 * CSRF-bootstrapped. Nothing else about a spec changes.
 *
 * WHY `page.request` NEEDS IT TOO (qa-fail-49655511c721a165)
 * ---------------------------------------------------------
 * The first version of this file bootstrapped only the `request` fixture and
 * left `page` alone, reasoning that a real browser context gets the cookie and
 * `Sec-Fetch-Site` for free. That is true of a request the BROWSER issues --
 * a navigation, a form submit, a `fetch` from page JS. It is NOT true of
 * `page.request` / `context.request`: that is the same bare HTTP client as
 * `request`, sharing the context's cookie jar and nothing else. It carries the
 * session cookie (so `csrf_protect` engages) and neither `X-CSRF-Token` nor
 * `Sec-Fetch-Site` (so it is rejected). Measured on run qa-1787358426: 30 of 31
 * failures were this 403, and 17 of the 30 went through `page.request`
 * (noc_canvas, wfc_lifecycle, idp_portal) where switching `request` alone
 * would have fixed nothing.
 *
 * The cure is the one Playwright offers for a context: `setExtraHTTPHeaders`.
 * `BrowserContextAPIRequestContext` reads the context's `extraHTTPHeaders` on
 * every call (playwright-core/lib/server/fetch.js), so pinning the token there
 * covers `page.request` without touching each call site. The header also rides
 * on browser navigations, where a GET ignores it, and on browser-issued
 * mutating requests, where `base.html` attaches the SAME value from the same
 * cookie -- so the two sources cannot disagree unless the session is rotated
 * mid-test (a login flow), which none of the API specs do.
 *
 * A hand-rolled `browser.newContext()` / `browser.newPage()` in a `beforeAll`
 * bypasses every fixture, so it gets `createAuthedBrowserContext(browser)`.
 */

import {
  test as base,
  expect,
  type APIRequestContext,
  type Browser,
  type BrowserContext,
  type BrowserContextOptions,
} from '@playwright/test';
import { BASE_URL, resolveBaseUrl, sameOrigin } from './base_url';

/** Set by `tools/security/csrf.py::register_csrf`; readable by page JS by design. */
export const CSRF_COOKIE = 'icdev_csrf';

/** The header `csrf_protect` compares against the session token. */
export const CSRF_HEADER = 'X-CSRF-Token';

/**
 * Same resolution `playwright.config.ts` and `fixtures/govcon_cpmp.ts` use, so a
 * spec and its auth bootstrap can never end up pointed at different servers.
 *
 * That claim used to be FALSE and nothing checked it: this constant read
 * `ICDEV_DASHBOARD_URL` while the config preferred `ICDEV_E2E_BASE_URL`, so a
 * run setting both bootstrapped the session on one host spelling and issued the
 * spec's requests on the other. A cookie jar is keyed by host, so the session
 * never rode along and every mutating request 403'd CSRF_FAILED. All three now
 * import ./base_url (qa-fail-84f92cebcf4fe498).
 */
export const DEFAULT_BASE_URL = BASE_URL;

/** The `playwright` worker fixture — typed structurally to avoid a deep import. */
type PlaywrightFixture = {
  request: { newContext(options?: Record<string, unknown>): Promise<APIRequestContext> };
};

function truthy(value: string | undefined): boolean {
  return ['1', 'true', 'yes', 'on'].includes((value ?? '').trim().toLowerCase());
}

let warned = false;

/** One line per process — a warning repeated by 24 tests is noise, not a signal. */
function warnOnce(message: string): void {
  if (warned) return;
  warned = true;
  console.log(`  ! E2E auth bootstrap: ${message}`);
}

let warnedOrigin = false;

/**
 * Say ONCE when the URL a spec is about to be bootstrapped against is not the
 * one the rest of the suite resolves to.
 *
 * Its own latch, not `warnOnce`'s: this is the failure that produced 4 silent
 * 403s in `cpmp_cdrl.spec.ts` on run qa-1787705278, and it must not be
 * swallowed by an unrelated CSRF-token warning that happened to fire first.
 * `sameOrigin` compares the host SPELLING deliberately — `localhost` and
 * `127.0.0.1` are one server and two cookie jars, which is the whole defect.
 */
function warnOnOriginDivergence(baseURL: string): void {
  if (warnedOrigin || sameOrigin(baseURL)) return;
  warnedOrigin = true;
  console.log(
    `  ! E2E auth bootstrap: bootstrapping against ${baseURL} but the suite resolves ` +
      `${resolveBaseUrl()} — a session cookie is keyed by host, so mutating requests ` +
      'will 403 CSRF_FAILED even when both point at the same server. ' +
      'Set ICDEV_E2E_BASE_URL and ICDEV_DASHBOARD_URL to the same spelling.',
  );
}

/**
 * Build an `APIRequestContext` that can perform mutating requests against a
 * CSRF-enforcing dashboard.
 *
 * Never throws on a bootstrap failure: it falls back to a plain context so the
 * run fails at the assertion that actually cares, with that test's own error,
 * rather than collapsing every spec into an opaque fixture error.
 */
export async function createAuthedRequestContext(
  playwright: PlaywrightFixture,
  baseURL: string = DEFAULT_BASE_URL,
): Promise<APIRequestContext> {
  warnOnOriginDivergence(baseURL);
  const probe = await playwright.request.newContext({ baseURL });
  let storageState: unknown;
  let token = '';

  try {
    // Dev auto-login sets session['user_id']; the after_request hook in csrf.py
    // then issues `icdev_csrf`. Both land in this context's cookie jar.
    await probe.get('/', { failOnStatusCode: false });
    const state = (await probe.storageState()) as { cookies: Array<{ name: string; value: string }> };
    storageState = state;
    token = state.cookies.find((c) => c.name === CSRF_COOKIE)?.value ?? '';
  } catch (err) {
    warnOnce(`could not reach ${baseURL} (${(err as Error).message}) — continuing without a CSRF token`);
  } finally {
    await probe.dispose();
  }

  if (!token && !truthy(process.env.ICDEV_AUTH_BYPASS)) {
    // Expected under ICDEV_AUTH_BYPASS (no session cookie is issued and CSRF is
    // off). Without it, an absent token means the mutating specs are about to
    // 403 — say so here rather than let it surface as 24 unexplained failures.
    warnOnce(
      `no ${CSRF_COOKIE} cookie from ${baseURL} and ICDEV_AUTH_BYPASS is unset — ` +
        'mutating requests will 403 CSRF_FAILED if that server has a login session',
    );
  }

  return playwright.request.newContext({
    baseURL,
    storageState,
    extraHTTPHeaders: token ? { [CSRF_HEADER]: token } : undefined,
  });
}

/**
 * Pin the CSRF token onto an existing browser context so `context.request`
 * (which is what `page.request` is) can perform mutating requests.
 *
 * Same handshake as `createAuthedRequestContext`, through the context's own
 * cookie jar: GET `/` establishes the dev auto-login session and receives the
 * `icdev_csrf` cookie; the cookie value becomes a context-wide extra header.
 *
 * Returns the token, or `''` when none was issued (ICDEV_AUTH_BYPASS, or an
 * unreachable server). Never throws, for the same reason as above.
 */
export async function bootstrapBrowserContext(
  context: BrowserContext,
  baseURL: string = DEFAULT_BASE_URL,
): Promise<string> {
  warnOnOriginDivergence(baseURL);
  let token = '';
  try {
    await context.request.get(`${baseURL}/`, { failOnStatusCode: false });
    const cookies = await context.cookies(baseURL);
    token = cookies.find((c) => c.name === CSRF_COOKIE)?.value ?? '';
  } catch (err) {
    warnOnce(`could not reach ${baseURL} (${(err as Error).message}) — continuing without a CSRF token`);
  }
  if (token) {
    await context.setExtraHTTPHeaders({ [CSRF_HEADER]: token });
  } else if (!truthy(process.env.ICDEV_AUTH_BYPASS)) {
    warnOnce(
      `no ${CSRF_COOKIE} cookie from ${baseURL} and ICDEV_AUTH_BYPASS is unset — ` +
        'mutating requests will 403 CSRF_FAILED if that server has a login session',
    );
  }
  return token;
}

/**
 * For a spec that builds its own context in a hook (`beforeAll` gets no `page`
 * or `context` fixture): a `browser.newContext()` that is already bootstrapped.
 * The caller owns it and must `close()` it.
 */
export async function createAuthedBrowserContext(
  browser: Browser,
  options: BrowserContextOptions = {},
  baseURL: string = DEFAULT_BASE_URL,
): Promise<BrowserContext> {
  const context = await browser.newContext({ baseURL, ...options });
  await bootstrapBrowserContext(context, baseURL);
  return context;
}

/**
 * Drop-in replacement for `@playwright/test`'s `test`, with `request` overridden
 * by the bootstrapped context above and `context` (hence `page` and
 * `page.request`) bootstrapped in place.
 */
export const test = base.extend<{ request: APIRequestContext }>({
  request: async ({ playwright, baseURL }, use) => {
    const context = await createAuthedRequestContext(
      playwright as unknown as PlaywrightFixture,
      baseURL ?? DEFAULT_BASE_URL,
    );
    await use(context);
    await context.dispose();
  },
  context: async ({ context, baseURL }, use) => {
    await bootstrapBrowserContext(context, baseURL ?? DEFAULT_BASE_URL);
    await use(context);
  },
});

export { expect };
// CUI // SP-CTI

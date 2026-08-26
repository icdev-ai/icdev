// CUI // SP-CTI
/**
 * ICDEV™ E2E base URL — ONE resolver, because two of them 403 the whole suite.
 *
 * THE FAILURE THIS FIXES (qa-fail-a5dbf266dfb0ce4a)
 * ------------------------------------------------
 * `playwright.config.ts` resolves the URL under test as
 *
 *     ICDEV_E2E_BASE_URL || ICDEV_DASHBOARD_URL || http://localhost:<PORT>
 *
 * and every spec-local `BASE` constant resolved it as
 *
 *     ICDEV_DASHBOARD_URL || http://localhost:5050
 *
 * Identical whenever exactly one of the two variables is set — and the QA sweep
 * sets BOTH. Measured on run qa-1787705278 (2026-08-26), whose environment
 * carried `ICDEV_E2E_BASE_URL=http://127.0.0.1:5050` alongside
 * `ICDEV_DASHBOARD_URL=http://localhost:5050`:
 *
 *   * `fixtures/auth.ts` bootstraps the CSRF double-submit handshake against
 *     the fixture `baseURL`, i.e. the CONFIG value — so the dashboard session
 *     cookie and the `icdev_csrf` cookie landed in the request context's jar
 *     under host `127.0.0.1`.
 *   * The spec then issued its requests at the ABSOLUTE spec-local `BASE`, i.e.
 *     `http://localhost:5050`. A cookie jar is keyed by host, so neither cookie
 *     rode along.
 *   * `ICDEV_DASHBOARD_DEV_AUTOLOGIN` then minted a BRAND NEW session for that
 *     cookieless request, with a brand new `_csrf_token`, and `csrf_protect`
 *     compared it against the pinned header from the OTHER origin.
 *
 * Verified against the live dashboard — a mutating request carrying no cookies
 * answers `403 {"code":"CSRF_FAILED"}`, not `401`, precisely because the auth
 * `before_request` hook logs the caller in before `csrf_protect` looks at the
 * session. So the symptom is not "unauthenticated": it is every POST/PUT in the
 * suite failing an integrity check, which reads as a product defect. That run
 * filed 39 QA cards; the CPMP/GovCon ones were all this.
 *
 * THE FIX — resolve it in ONE place and import it.
 * ------------------------------------------------
 * `resolveBaseUrl()` is the only precedence in the suite. `playwright.config.ts`
 * calls it for `use.baseURL`, `fixtures/auth.ts` calls it for the bootstrap
 * origin, and every spec-local `BASE` calls it for its absolute URLs — so the
 * origin a spec talks to is the origin its cookies were minted at, by
 * construction rather than by two constants happening to agree.
 *
 * This is the SECOND time the split shipped. `qa-fail-e2e-baseurl-01` fixed
 * exactly this precedence in `ai_ify.spec.ts` — at that ONE call site, leaving
 * the other eight to be found one QA sweep at a time. A shared resolver is what
 * makes the next spec correct without anybody remembering.
 * `tests/test_e2e_base_url_single_source.py` fails a spec that re-derives it.
 *
 * WHY `ICDEV_E2E_BASE_URL` WINS
 * ----------------------------
 * The two variables answer different questions and `playwright.config.ts`
 * documents the distinction: `ICDEV_DASHBOARD_URL` is "how does a process reach
 * the dashboard" (`.env` legitimately sets it to a container gateway such as
 * `http://host.docker.internal:5050` for agents running inside a container),
 * and `ICDEV_E2E_BASE_URL` is "what does the test runner on the host navigate
 * to". Where they disagree, a test runner wants the second — so this keeps the
 * config's existing precedence rather than inventing a third.
 */

/** The port an unconfigured local dashboard listens on. */
export const DEFAULT_PORT = '5050';

/**
 * The one base URL the whole E2E suite talks to.
 *
 * A function rather than a constant so that a caller which DOES want a module
 * constant (`fixtures/auth.ts`'s `DEFAULT_BASE_URL`, a spec's `BASE`) gets its
 * value from this precedence instead of writing a second copy of it — which is
 * the entire defect above.
 */
export function resolveBaseUrl(): string {
  return (
    process.env.ICDEV_E2E_BASE_URL ||
    process.env.ICDEV_DASHBOARD_URL ||
    `http://localhost:${process.env.ICDEV_DASHBOARD_PORT || DEFAULT_PORT}`
  );
}

/** Which variable supplied the in-force base URL — for diagnostics only. */
export function baseUrlSource(): string {
  if (process.env.ICDEV_E2E_BASE_URL) return 'ICDEV_E2E_BASE_URL';
  if (process.env.ICDEV_DASHBOARD_URL) return 'ICDEV_DASHBOARD_URL';
  return 'derived from ICDEV_DASHBOARD_PORT';
}
// CUI // SP-CTI

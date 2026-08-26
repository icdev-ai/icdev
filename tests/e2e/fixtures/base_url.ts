// CUI // SP-CTI
/**
 * ICDEV™ E2E base URL — ONE resolution, shared by the config, the auth
 * bootstrap and every spec-local `BASE` constant.
 *
 * THE FAILURE THIS FIXES (qa-fail-0d954757a83824da)
 * -------------------------------------------------
 * `playwright.config.ts` resolves `use.baseURL` as
 * `ICDEV_E2E_BASE_URL || ICDEV_DASHBOARD_URL || http://localhost:<PORT>`, while
 * `fixtures/auth.ts` and `fixtures/govcon_cpmp.ts` each carried their own copy
 * that read `ICDEV_DASHBOARD_URL` alone. A run with BOTH variables set — which
 * is what the QA sweep does (`ICDEV_E2E_BASE_URL=http://127.0.0.1:5050`,
 * `ICDEV_DASHBOARD_URL=http://localhost:5050`) — therefore pointed the CSRF
 * bootstrap at `127.0.0.1` and the specs' own requests at `localhost`.
 *
 * That is ONE server behind TWO host spellings, and a cookie jar is keyed by
 * host. So the bootstrap established a session and an `icdev_csrf` token on
 * `127.0.0.1`, and every spec request to `localhost` arrived with a DIFFERENT
 * session carrying a DIFFERENT token while the pinned `X-CSRF-Token` header
 * (an `extraHTTPHeaders` value, attached regardless of host) still held the
 * first one. Measured against the live dashboard:
 *
 *   GET  localhost:5050/api/cpmp/contracts                    → 200
 *   PUT  localhost:5050/api/cpmp/contracts/<id>/status
 *        + X-CSRF-Token bootstrapped on 127.0.0.1             → 403 CSRF_FAILED
 *   PUT  same, token bootstrapped on the SAME host            → 400 (real answer)
 *
 * Every GET passes and only mutating calls fail, so it reads as a product
 * defect in whichever endpoint happened to be exercised
 * (`gcpl-cset-11: PUT /api/cpmp/contracts/<id>/status`) rather than as an
 * environment mismatch.
 *
 * WHY A MODULE AND NOT A THIRD COPY OF THE EXPRESSION
 * ---------------------------------------------------
 * This is the same defect as `qa-fail-e2e-baseurl-01` — "one variable
 * answering two questions" — surfacing a second time. That fix corrected
 * `playwright.config.ts` and `ai_ify.spec.ts` and left the shared fixtures
 * reading `ICDEV_DASHBOARD_URL` alone; `auth.ts` even documented its constant
 * as "the same resolution `playwright.config.ts` and `fixtures/govcon_cpmp.ts`
 * use, so a spec and its auth bootstrap can never end up pointed at different
 * servers", which was the precise thing that was not true. A comment claiming
 * two expressions agree cannot enforce it. One expression can.
 *
 * This module imports nothing, so `playwright.config.ts` can consume it without
 * pulling `@playwright/test` fixtures into config load.
 *
 * See `playwright.config.ts` for why `ICDEV_E2E_BASE_URL` takes precedence:
 * `ICDEV_DASHBOARD_URL` answers "how does a process reach the dashboard" (and
 * is legitimately a container gateway), `ICDEV_E2E_BASE_URL` answers "what does
 * the test runner on the host navigate to".
 */

/**
 * Resolve the base URL of the dashboard under test.
 *
 * Exported as a function as well as a constant so a caller that mutates
 * `process.env` after module load (globalSetup, a test harness) can re-derive
 * it rather than read a stale snapshot.
 */
export function resolveBaseUrl(): string {
  return (
    process.env.ICDEV_E2E_BASE_URL ||
    process.env.ICDEV_DASHBOARD_URL ||
    `http://localhost:${process.env.ICDEV_DASHBOARD_PORT || '5050'}`
  );
}

/** The in-force base URL at module load — what `playwright.config.ts` pins. */
export const BASE_URL = resolveBaseUrl();
// CUI // SP-CTI

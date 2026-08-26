// CUI // SP-CTI
/**
 * ONE answer to "which URL is the dashboard under test" (qa-fail-84f92cebcf4fe498).
 *
 * THE FAILURE THIS FIXES
 * ----------------------
 * `playwright.config.ts` resolves `baseURL` as
 * `ICDEV_E2E_BASE_URL || ICDEV_DASHBOARD_URL || http://localhost:<port>`, while
 * `fixtures/auth.ts` and `fixtures/govcon_cpmp.ts` resolved their own constant
 * as `ICDEV_DASHBOARD_URL || http://localhost:5050` — ignoring
 * `ICDEV_E2E_BASE_URL` entirely. Set both (the QA sweep does:
 * `ICDEV_E2E_BASE_URL=http://127.0.0.1:5050`, `ICDEV_DASHBOARD_URL=http://localhost:5050`)
 * and the two disagree on the SPELLING OF THE HOST while pointing at the same
 * server. Everything still resolves, so nothing errors — and every mutating
 * request comes back `403 CSRF_FAILED`.
 *
 * WHY A HOST SPELLING IS LOAD-BEARING
 * -----------------------------------
 * `auth.ts` bootstraps the CSRF double-submit handshake by GETting `/` on
 * `baseURL` — 127.0.0.1 — so the dev-auto-login session cookie and the
 * `icdev_csrf` cookie are stored under host `127.0.0.1`. A cookie jar is keyed
 * by HOST, and `127.0.0.1` and `localhost` are two hosts. The spec then POSTs
 * to `BASE` — localhost — carrying no session cookie and an `X-CSRF-Token`
 * header from a session that request will never join. On the server,
 * `_auth_before_request` runs FIRST and dev-auto-login sets `session['user_id']`,
 * so `csrf_protect` engages, finds no matching token in this brand-new session
 * and no `Sec-Fetch-Site`, and returns 403.
 *
 * Measured against the live dashboard on 2026-08-25, same server, same token,
 * the only difference being how the host is spelled:
 *
 *   POST http://127.0.0.1:5050/api/cpmp/contracts/<id>/generate-due  -> 200
 *   POST http://localhost:5050/api/cpmp/contracts/<id>/generate-due  -> 403 CSRF_FAILED
 *
 * On run qa-1787705278 that took out exactly the four role-gated POSTs in
 * `cpmp_cdrl.spec.ts` (gcpl-cdrl-01/04/10/13) and left all nine GETs green —
 * the signature of a CSRF failure, not of a broken endpoint. `require_role` was
 * ruled out from the evidence: `dashboard_auth_log` has recorded no
 * `permission_denied` since 2026-07-08.
 *
 * NOT taken: making `auth.ts` send `Sec-Fetch-Site: same-origin`. The server
 * accepts it, but it is a header a browser stamps and page code cannot forge —
 * spoofing it would mean the suite could no longer tell a working CSRF
 * implementation from a broken one. `fixtures/auth.ts` already declined it for
 * that reason.
 *
 * NOT taken: patching `govcon_cpmp.ts` alone. `ai_ify.spec.ts` was patched that
 * way for the same defect (qa-fail-e2e-baseurl-01) and the copies in the two
 * shared fixtures survived. A per-file constant IS the bug; a resolver that
 * every caller imports is what stops it coming back.
 */

/**
 * The dashboard URL for this run — the SAME expression `playwright.config.ts`
 * uses for `use.baseURL` and for `webServer.url`.
 *
 * Read at call time, never cached, so a test that mutates `process.env` in a
 * hook (and globalSetup, which loads `.env` before the config is evaluated) is
 * observed rather than frozen out.
 */
export function resolveBaseUrl(): string {
  return (
    process.env.ICDEV_E2E_BASE_URL ||
    process.env.ICDEV_DASHBOARD_URL ||
    `http://localhost:${process.env.ICDEV_DASHBOARD_PORT || '5050'}`
  );
}

/**
 * Module-level convenience for the many `const BASE = ...` spec constants.
 *
 * Evaluated once at import. That is correct for a spec constant — Playwright
 * imports specs after globalSetup has loaded `.env` — but prefer
 * `resolveBaseUrl()` anywhere the value is read inside a fixture or a hook.
 */
export const BASE_URL = resolveBaseUrl();

/**
 * True when `candidate` addresses the same server as `resolveBaseUrl()` **by
 * the same host spelling**.
 *
 * Deliberately NOT a DNS or a same-server check: `localhost` and `127.0.0.1`
 * are the same server and are still two cookie jars, so answering "same server"
 * here would hide the exact defect this module exists for.
 */
export function sameOrigin(candidate: string, reference: string = resolveBaseUrl()): boolean {
  try {
    const a = new URL(candidate);
    const b = new URL(reference);
    return a.protocol === b.protocol && a.hostname === b.hostname && a.port === b.port;
  } catch {
    return false;
  }
}
// CUI // SP-CTI

// CUI // SP-CTI
// Close network migration sessions a spec created, from an afterAll hook.
//
// Same defect as fixtures/kanban_cleanup.ts, one canvas over. A spec that
// creates a session through the wizard leaves an `in_progress` row on the real
// board, indistinguishable from an engineer's stalled cutover. The NMCE genesis
// reflex flags any such row after 7 days and raises a kanban card that burns an
// agent session — four accumulated between 2026-08-09 and 2026-08-14, each with
// a byte-identical downstream footprint, after 36 earlier ones were archived by
// hand. A test that permanently dirties the system under test is not a passing
// test.
//
// Three things make this less trivial than `request.patch(...)`:
//
//   1. `request` is a TEST-scoped fixture, so it does not exist in afterAll. The
//      worker-scoped `playwright` fixture can build an equivalent context.
//
//   2. `/migration-canvas/api/*` is behind `mdc_login_required`, which 401s a
//      context carrying no session cookie unless ICDEV_AUTH_BYPASS is set. So
//      the caller passes the page's storageState and the cleanup inherits the
//      login the test already performed, rather than depending on the CI-only
//      bypass being on.
//
//   3. Mutating routes are CSRF-protected with a double-submit cookie whenever
//      a cookie session exists. The token has to be echoed in the X-CSRF-Token
//      header; storageState carries the cookie it is read from.
//
// There is no DELETE route for a network session, and archiving is the right
// verb anyway: `archived` is the terminal status every active-session query and
// the reflex already agree on (constants.NET_SESSION_TERMINAL_STATUSES), and
// the PATCH writes a net_session_status_changed audit row, so the close is
// evidenced rather than silent.

import type { PlaywrightWorkerArgs, TestInfo } from '@playwright/test';

const CSRF_COOKIE = 'icdev_csrf';

/** Terminal status used to close a session out. Must stay in NET_SESSION_TERMINAL_STATUSES. */
export const ARCHIVED = 'archived';

/**
 * Best-effort archive of `ids`.
 *
 * `storageState` is the value of `await page.context().storageState()` captured
 * during the test — capture it as soon as the session id is known, so a test
 * that fails half way through still cleans up. Failures are warned about rather
 * than thrown — cleanup must not turn an otherwise green run red — but they are
 * never swallowed silently, because a cleanup that quietly stopped working is
 * how the residue accumulated in the first place.
 */
export async function archiveNetSessions(
  playwright: PlaywrightWorkerArgs['playwright'],
  testInfo: TestInfo,
  ids: Array<string | null>,
  specName: string,
  storageState?: any,
): Promise<void> {
  const present = ids.filter(Boolean) as string[];
  if (!present.length) return;

  const api = await playwright.request.newContext({
    baseURL: (testInfo.project.use as { baseURL?: string }).baseURL,
    storageState,
  });
  try {
    const { cookies } = await api.storageState();
    const token = cookies.find((c) => c.name === CSRF_COOKIE)?.value ?? '';
    for (const id of present) {
      const resp = await api
        .patch(`/migration-canvas/api/network-migration/${id}`, {
          headers: token ? { 'X-CSRF-Token': token } : {},
          data: { status: ARCHIVED },
        })
        .catch(() => null);
      if (!resp || resp.status() >= 300) {
        console.warn(
          `[${specName}] could not archive session ${id}` +
            (resp ? ` (HTTP ${resp.status()})` : ' (request failed)') +
            ' — it will remain in_progress and the NMCE reflex will flag it as stale',
        );
      }
    }
  } finally {
    await api.dispose();
  }
}

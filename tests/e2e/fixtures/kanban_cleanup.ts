// CUI // SP-CTI
// Delete kanban tasks a spec created, from an afterAll hook.
//
// Specs that create board tasks must remove them again. Without this, every run
// leaves fixtures on the real board where they are indistinguishable from work:
// six had accumulated as backlog items, and the scheduler will happily promote
// and dispatch an agent against "[E2E Test] Playwright smoke task". A test that
// permanently dirties the system under test is not a passing test.
//
// Two things make this less trivial than `request.delete(...)`:
//
//   1. `request` is a TEST-scoped fixture, so it does not exist in afterAll. The
//      worker-scoped `playwright` fixture can build an equivalent context.
//
//   2. Mutating routes are CSRF-protected with a double-submit cookie. A fresh
//      APIRequestContext carries no cookie, so an unprepared DELETE returns
//      403 CSRF_FAILED — not 404, and not silence. The token has to be obtained
//      from a GET first and echoed in the X-CSRF-Token header.

import type { APIRequestContext, PlaywrightWorkerArgs, TestInfo } from '@playwright/test';

const CSRF_COOKIE = 'icdev_csrf';

/** Obtain the double-submit CSRF token, or '' when the app is not enforcing it. */
async function csrfToken(api: APIRequestContext): Promise<string> {
  await api.get('/').catch(() => null); // sets the cookie; failure is not fatal
  const { cookies } = await api.storageState();
  return cookies.find((c) => c.name === CSRF_COOKIE)?.value ?? '';
}

/**
 * Best-effort delete of `ids`, in the order given.
 *
 * Pass children before parents: a child carries depends_on_task_id to its
 * parent. Failures are warned about rather than thrown — cleanup must not turn
 * an otherwise green run red — but they are never swallowed silently, because a
 * cleanup that quietly stopped working is how the fixtures accumulated in the
 * first place.
 */
export async function deleteKanbanTasks(
  playwright: PlaywrightWorkerArgs['playwright'],
  testInfo: TestInfo,
  ids: Array<string | null>,
  specName: string,
): Promise<void> {
  const present = ids.filter(Boolean) as string[];
  if (!present.length) return;

  const api = await playwright.request.newContext({
    baseURL: (testInfo.project.use as { baseURL?: string }).baseURL,
  });
  try {
    const token = await csrfToken(api);
    for (const id of present) {
      const resp = await api
        .delete(`/api/kanban/tasks/${id}`, {
          headers: token ? { 'X-CSRF-Token': token } : {},
        })
        .catch(() => null);
      if (!resp || resp.status() >= 300) {
        console.warn(
          `[${specName}] could not delete fixture ${id}` +
            (resp ? ` (HTTP ${resp.status()})` : ' (request failed)') +
            ' — it will remain on the board',
        );
      }
    }
  } finally {
    await api.dispose();
  }
}

// CUI // SP-CTI
/**
 * ICDEV™ E2E — the environment an ICDEV PYTHON SUBPROCESS a spec spawns must run
 * with, so it lands on the database the run asked for.
 *
 * THE DEFECT THIS CLOSES (qa-fail-679a43311f34d5c9)
 * -------------------------------------------------
 * `webServerDatabaseEnv()` redirects the dashboard PLAYWRIGHT STARTS — it is
 * merged into `webServer.env` in `playwright.config.ts` and nowhere else. A spec
 * that spawns its own ICDEV Python process with a bare `{ ...process.env }`
 * inherit carries the operator's ambient `ICDEV_DATABASE_URL` through unchanged,
 * and every connection site in `tools/db/storage.py` reads that DSN BEFORE the
 * discrete `ICDEV_PG_DATABASE`. So under the documented isolation recipe
 *
 *     ICDEV_PG_DATABASE=icdev_e2e npx playwright test
 *
 * the SERVER went to `icdev_e2e` and the spec's own subprocess went to the
 * canonical `icdev`. MEASURED in this worktree 2026-09-09, same interpreter,
 * nothing else changed:
 *
 *     ICDEV_PG_DATABASE=icdev_e2e                        -> 'icdev'
 *     ICDEV_PG_DATABASE=icdev_e2e ICDEV_DATABASE_URL=''  -> 'icdev_e2e'
 *
 * Two consequences, and the second is the serious one: the spec fails because it
 * seeds one database and reads another, AND the fixture rows land in the
 * CANONICAL BOARD while `globalSetup` prints a tick saying the run is isolated.
 * That tick was a true statement about the server — a process that is not the
 * one doing the writing.
 *
 * WHY A SHARED FUNCTION AND NOT THE PATTERN REPEATED PER SPEC
 * ----------------------------------------------------------
 * Three sites were fixed one at a time, each carrying its own copy of the
 * reasoning, and the fourth (`dwo_trigger_linkage`) was left — which is the
 * shape CLAUDE.md's autonomy-lrn-01 rule names: a per-site repair passes at
 * every site it was applied to and says nothing about the next one. With ONE
 * function there is one place the precedence is spelled, and
 * `tests/test_e2e_subprocess_isolation.py` can assert over the SOURCE that every
 * spawn of the interpreter goes through it — an assertion that is impossible to
 * write against a pattern.
 *
 * `webServerDatabaseEnv()` IS APPLIED LAST, ON PURPOSE. A caller's `extra` keys
 * must not be able to outrank the database redirect: a spec that set
 * `ICDEV_DATABASE_URL` itself would reinstate exactly this defect, and there is
 * no legitimate reason for one to. Everything else a caller passes wins over the
 * ambient environment as usual.
 *
 * IT RETURNS THE AMBIENT ENVIRONMENT UNCHANGED when no database was requested,
 * because `webServerDatabaseEnv()` returns `{}` then — so an ordinary local run
 * is not altered by this module at all.
 */

import { webServerDatabaseEnv } from './e2e_database';

/** The interpreter a spec spawns. ONE binding, so specs cannot disagree. */
export const PYTHON = process.env.ICDEV_PYTHON || 'python';

/**
 * Environment for an ICDEV Python subprocess: the ambient environment, then the
 * caller's own variables, then the database the RUN asked for — last, so it
 * cannot be overridden.
 */
export function icdevSubprocessEnv(
  extra: Record<string, string> = {},
  env: NodeJS.ProcessEnv = process.env,
): Record<string, string> {
  const merged: Record<string, string> = {};
  for (const [key, value] of Object.entries(env)) {
    if (value !== undefined) merged[key] = value;
  }
  return { ...merged, ...extra, ...webServerDatabaseEnv(env) };
}
// CUI // SP-CTI

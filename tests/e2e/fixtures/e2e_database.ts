// CUI // SP-CTI
/**
 * ICDEV™ E2E database isolation — ONE resolver for "which database did this run
 * ASK for", because the config's advice and the code's behaviour disagreed.
 *
 * THE FAILURE THIS FIXES (qa-fail-6a87916931be3793)
 * -------------------------------------------------
 * `playwright.config.ts` documented a recipe for pointing a local run at a
 * throwaway database, and gave the reason plainly — the suite writes fixtures,
 * and running it against the canonical `icdev` leaves them there:
 *
 *     python tools/db/bootstrap_pg.py
 *     ICDEV_PG_DATABASE=icdev_e2e ICDEV_PG_DB=icdev_e2e npx playwright test
 *
 * THAT COMMAND REDIRECTED NOTHING. `.env` sets `ICDEV_DATABASE_URL`, and every
 * connection site in `tools/db/storage.py` reads the DSN FIRST — the discrete
 * `ICDEV_PG_DATABASE` is consulted only when no DSN is present. Measured on
 * this deployment 2026-09-05, exactly the documented variables exported:
 *
 *     SELECT current_database()  ->  icdev          <- NOT icdev_e2e
 *
 * So an operator following the documented recipe believed they were isolated
 * and ran ~840 tests' worth of fixture writes into the canonical board. This is
 * a live mechanism for the E2E residue already seen there (stale session cards).
 * `tools/db/shadowed_migration_audit.py` had already learned this precedence
 * and warns about it in its own docstring; the Playwright config had not.
 *
 * TWO FORMS WORK, AND BOTH ARE MEASURED (not reasoned about)
 * ----------------------------------------------------------
 *   * `ICDEV_DATABASE_URL=postgresql://…/icdev_e2e` — the variable that wins
 *     outright. It beats `.env` because the loaders call `load_dotenv(…,
 *     override=False)`, which skips a key already present in the environment.
 *   * `ICDEV_PG_DATABASE=icdev_e2e` — the historically documented form. It only
 *     wins once the DSN is out of the way, so `webServerDatabaseEnv()` also
 *     exports `ICDEV_DATABASE_URL` as an EMPTY STRING. Empty is not absent: the
 *     key stays present, so `override=False` will not let `.env` put the DSN
 *     back, and `storage.py`'s `if db_url:` is falsy so the keyword form (host,
 *     port, user, password, dbname — all in `.env`) is used. Verified end to
 *     end on Windows: the child process sees `''`, and `current_database()`
 *     answers `icdev_e2e`.
 *
 * WHY THIS IS A SHARED MODULE. The config uses it to BUILD the server's
 * environment and `globalSetup.ts` uses it to ASSERT the server obeyed. A
 * second copy of the precedence in either place is how this defect happened in
 * the first place, so there is exactly one.
 *
 * THE RESOLVER STATES INTENT ONLY. It reports which database the run asked for
 * — it can never report which one the server reached, because that is not
 * knowable from an environment variable. That question is answered by
 * `/api/health`, which measures `current_database()` off the live connection.
 */

/** Parse the database name out of a PostgreSQL DSN. Null when there isn't one. */
export function parseDsnDatabase(dsn: string | undefined): string | null {
  const raw = (dsn ?? '').trim();
  if (!raw) return null;
  try {
    // The URL parser handles query strings, credentials and IPv6 hosts; a DSN
    // that is not a URL at all (a libpq keyword string) falls through to null
    // rather than being guessed at.
    const parsed = new URL(raw);
    const name = decodeURIComponent(parsed.pathname.replace(/^\//, '')).trim();
    return name || null;
  } catch {
    return null;
  }
}

export interface RequestedDatabase {
  /** The database this run asked for, or null when it asked for nothing. */
  name: string | null;
  /** Which variable said so — for an error message that names the real cause. */
  source: string | null;
  /**
   * True when a PER-RUN knob named it, false when it came from the ambient
   * deployment configuration. The verdict wording depends on this and must not
   * be collapsed: "the server is on the database you asked for" and "you asked
   * for nothing and this is the canonical board" are different facts.
   */
  explicit: boolean;
}

/**
 * Variables an operator sets to redirect ONE run. `.env` on this deployment
 * carries `ICDEV_PG_DB` and `ICDEV_DATABASE_URL` but NOT `ICDEV_PG_DATABASE`,
 * and `.env.example`/`.env.sample`/docker-compose agree, so a present
 * `ICDEV_PG_DATABASE` is a deliberate act rather than deployment config. CI
 * sets it with no DSN at all, so nothing in the repo has both.
 */
const EXPLICIT_SOURCES = ['ICDEV_E2E_DATABASE', 'ICDEV_PG_DATABASE'] as const;

/**
 * Which database did this run ASK for?
 *
 * THE PER-RUN KNOB OUTRANKS THE AMBIENT DSN, WHICH IS THE OPPOSITE OF THE
 * CONNECTION PRECEDENCE, AND DELIBERATELY SO. `storage.py` reads the DSN first;
 * that is the defect, not the contract. Measured on this deployment 2026-09-05,
 * an ordinary shell already exports `ICDEV_DATABASE_URL` (naming `icdev`) and
 * `ICDEV_PG_DB=icdev`, so ranking the DSN higher would let the ambient
 * configuration silently outrank the very variable the operator typed to escape
 * it — reproducing the card's defect inside its own fix. It was caught by
 * running this end to end, not by reading it.
 *
 * `webServerDatabaseEnv()` then MAKES the intent true rather than merely
 * reporting it: when the name wins, the DSN is cleared for the server, so the
 * process this asserts against really is on the requested database.
 *
 * Null means NOT REQUESTED. That is never the same as isolated.
 */
export function requestedDatabase(env: NodeJS.ProcessEnv = process.env): RequestedDatabase {
  const ordered: Array<[string, string | null]> = [
    ['ICDEV_E2E_DATABASE', (env.ICDEV_E2E_DATABASE ?? '').trim() || null],
    ['ICDEV_PG_DATABASE', (env.ICDEV_PG_DATABASE ?? '').trim() || null],
    ['ICDEV_DATABASE_URL', parseDsnDatabase(env.ICDEV_DATABASE_URL)],
    ['ICDEV_PG_DB', (env.ICDEV_PG_DB ?? '').trim() || null],
  ];
  for (const [source, name] of ordered) {
    if (name) {
      return {
        name,
        source,
        explicit: (EXPLICIT_SOURCES as readonly string[]).includes(source),
      };
    }
  }
  return { name: null, source: null, explicit: false };
}

/**
 * The database variables to merge into `webServer.env`, so the documented
 * command actually redirects the server Playwright starts.
 *
 * Returns `{}` when the run asked for nothing — a plain local run keeps whatever
 * `.env` configures, unchanged. Nothing here hardcodes a database, a host or a
 * credential: the values are the operator's own, and the empty-DSN case falls
 * through to the discrete `ICDEV_PG_*` settings already in `.env`.
 */
export function webServerDatabaseEnv(env: NodeJS.ProcessEnv = process.env): Record<string, string> {
  const requested = requestedDatabase(env);
  if (!requested.name) return {};

  // An explicit DSN already outranks `.env`; pass it through untouched rather
  // than rebuilding it, so no credential is ever reassembled here.
  if (requested.source === 'ICDEV_DATABASE_URL') {
    return { ICDEV_DATABASE_URL: (env.ICDEV_DATABASE_URL ?? '').trim() };
  }

  return {
    // Empty, NOT absent — see the module note. This is what lets the discrete
    // name win over the DSN `.env` would otherwise supply.
    ICDEV_DATABASE_URL: '',
    ICDEV_PG_DATABASE: requested.name,
    ICDEV_PG_DB: requested.name,
  };
}
// CUI // SP-CTI

# E2E isolation covered one writer — survey (qa-fail-679a43311f34d5c9)

Measured 2026-09-09 in worktree `C:/AI/ICDev/.tmp/worktrees/qa-fail-679a43311f34d5c9`,
PostgreSQL, ambient `ICDEV_DATABASE_URL` naming the canonical `icdev`.

## The defect, re-derived

`qa-fail-6a87916931be3793` made the documented recipe redirect the dashboard and
made `globalSetup` measure it. `webServerDatabaseEnv()` is merged into
`webServer.env` and nowhere else, so it redirects the dashboard **Playwright
starts**. Four specs spawn their own ICDEV Python process and inherited the
ambient DSN, which every connection site in `tools/db/storage.py` reads **before**
the discrete `ICDEV_PG_DATABASE`.

Same interpreter, nothing else changed:

```
ICDEV_PG_DATABASE=icdev_e2e                        -> icdev        <- the defect
ICDEV_PG_DATABASE=icdev_e2e ICDEV_DATABASE_URL=''  -> icdev_e2e    <- the fix
```

Re-derive with `python tests/e2e/fixtures/database_probe.py` under each env.

So a run printed `✓ E2E database confirmed: server is on 'icdev_e2e'` while its
fixtures were committed to the canonical board. **The tick was true, about a
process that was not the one doing the writing.**

## The census, replayed over real history

Population: the four spec files that spawn an ICDEV Python process, read out of
git rather than rewritten by hand. Three columns, never merged — `reaches` asks
"did rows go to the wrong database", `routed` asks "is it spelled the agreed way",
and only the first is a live defect.

| ref | in_scope | reaches | routed | real defects |
|---|---|---|---|---|
| `58ebed0aa~1` — before the per-site repair | 4 | 0 | 0 | **4** |
| `58ebed0aa` — after it | 4 | 3 | 0 | **1** (`dwo_trigger_linkage.spec.ts:169`) |
| this change | 4 | 4 | 4 | **0** |

The one remaining real defect is the site the sibling commit named as not fixed.
The three it did fix were behaviourally correct in **three separate spellings** of
the precedence — reported above as `reaches` but not `routed`, never counted as a
bad write. They now go through one function, which is what makes the census
expressible at all: an assertion that every spawn uses *the* builder cannot be
written against a repeated pattern.

## The assertion now refuses the incident

`globalSetup` against a dashboard measurably on `icdev_e2e` (`/api/health` →
`{"database":"icdev_e2e","database_measured":true}`), with
`tests/e2e/fixtures/subprocess_env.ts` temporarily reverted to the bare inherit:

```
  ✓ baseURL reachable: http://127.0.0.1:5093 → HTTP 200
E2E DATABASE ISOLATION FAILED — refusing to run.
  requested : icdev_e2e (via ICDEV_PG_DATABASE)
  verdict   : mismatch
  writers:
    server     confirmed     icdev_e2e (postgresql)
    subprocess mismatch      icdev (postgresql)
```

Restored, the same command prints two ticks:

```
  ✓ E2E database confirmed (server): measured on 'icdev_e2e' (via ICDEV_PG_DATABASE)
  ✓ E2E database confirmed (subprocess): measured on 'icdev_e2e' (via ICDEV_PG_DATABASE)
```

## The env the previously-unfixed spawn now gets

`icdevSubprocessEnv()` with exactly the extras `dwo_trigger_linkage` passes,
evaluated inside a Playwright run under the documented recipe:

```
GATEWAY_ENV {"ICDEV_DATABASE_URL":"","ICDEV_PG_DATABASE":"icdev_e2e","ICDEV_PG_DB":"icdev_e2e","PORT":"8999"}
HOSTILE_ENV {"ICDEV_DATABASE_URL":"","ICDEV_PG_DATABASE":"icdev_e2e"}
```

`HOSTILE_ENV` is a caller that passes its own `ICDEV_DATABASE_URL`: the redirect is
merged **last**, so it cannot be outranked.

## Cost

The subprocess probe is one `spawnSync`, once per run, only when a database was
requested: **409 / 417 / 447 / 415 / 417 ms** (5 runs, this host) against a
17.5-minute suite. Standing it down alone is `ICDEV_E2E_DB_CHECK_SUBPROCESS=0`,
and a run that does so **says** the writer was not measured — otherwise the kill
switch restores the original defect exactly.

## Verified, and not verified

* Two DIC workspace specs, documented recipe, server on `icdev_e2e`: **3 passed
  (15.4s)** — same result the per-site repair reported, so routing through the
  shared builder is behaviour-preserving.
* `dwo_trigger_linkage.spec.ts` **skipped**, on this deployment's own documented
  condition: `GET /api/studio/event-sources` answers 404 because those routes are
  not registered in `tools/dashboard/api/studio.py`. Its assertions are therefore
  still unverified by execution — stated, not implied. What *is* measured is the
  environment its spawn receives (above) and its coverage by the census.
* 843 tests in 67 files still parse (`npx playwright test --list`).
* Red-first: `tests/test_e2e_subprocess_isolation.py` is **17 failed / 6 passed**
  against the merge base and **23 passed** here.
* The canonical board was not written to: `dic_documents` 57, `dic_suggestions`
  62 after every run above — the pre-run counts this card recorded.

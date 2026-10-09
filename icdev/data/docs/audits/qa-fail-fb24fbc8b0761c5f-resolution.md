# qa-fail-fb24fbc8b0761c5f — gcpl-cset-18 was the merged base-URL defect, filed a 15th time

**Card:** `[QA] gcpl-cset: CPMP Portfolio & Contract Setup — CPMP-SETUP > gcpl-cset-18: PUT /api/cpmp/wbs/<id> updates WBS percent complete`
**Run:** `qa-1787705278` **Spec:** `tests/e2e/cpmp_portfolio.spec.ts:203`
**Reported:** `expect([200, 201, 204, 400, 404]).toContain(403)`
**Resolution:** no code change — already fixed on `main` by 448863f20 (#1935), verified below.

## It was never the WBS endpoint

`PUT /api/cpmp/wbs/<id>` answers **200** when the CSRF token it carries was
minted at the same host as the session cookie it carries. Measured against the
live dashboard before touching anything, same wbs row, same body, only the
token's origin varied:

| session cookie jar | `X-CSRF-Token` minted at | result |
|---|---|---|
| `localhost:5050` | `127.0.0.1:5050` | **403** `{"code":"CSRF_FAILED"}` |
| `localhost:5050` | `localhost:5050`  | **200** `{"status":"ok","wbs_id":"…"}` |

403 is not in the spec's accepted list, so the failure surfaced as a WBS
percent-complete defect. The route is correct and always was.

## The cause was already found, fixed and merged

This is one of **14 cards from run `qa-1787705278`, across 8 spec files, that
are the same fixture defect** — `playwright.config.ts` resolved the URL under
test as `ICDEV_E2E_BASE_URL || ICDEV_DASHBOARD_URL || http://localhost:<PORT>`
while nine spec-local `BASE` constants resolved it as
`ICDEV_DASHBOARD_URL || http://localhost:5050`. Those agree whenever exactly one
variable is set; that sweep set **both**, divergently. A cookie jar is keyed by
host, so `fixtures/auth.ts` bootstrapped the CSRF double-submit handshake at one
origin while each spec issued its requests at the other.

448863f20 (`qa-fail-a5dbf266dfb0ce4a`, PR #1935) made
`tests/e2e/fixtures/base_url.ts::resolveBaseUrl()` the suite's only precedence
and converted every consumer. **`tests/e2e/fixtures/govcon_cpmp.ts` is one of
them**, and it is where `cpmp_portfolio.spec.ts` gets its `BASE` — so this card
was covered by construction the moment that PR landed, without naming
`gcpl-cset-18`.

## Verified, under the environment that produced the failure

Both variables set and divergent, exactly as the sweep had them:

```
ICDEV_E2E_BASE_URL=http://127.0.0.1:5050 \
ICDEV_DASHBOARD_URL=http://localhost:5050 \
npx playwright test tests/e2e/cpmp_portfolio.spec.ts --project=chromium
→ 24 passed (2.0m)
```

That is the whole spec, not only `gcpl-cset-18` — the four other cards this run
filed against `cpmp_portfolio.spec.ts` (`gcpl-cset-08/12/16/19`, all still open)
pass in the same run. `tests/test_e2e_base_url_single_source.py`, the gated
guard that fails a spec re-deriving the expression, is green (7 passed).

## Why this is filed rather than built

Nothing in the tree needed to change, so there is no fix to prove RED-first.
Writing one anyway would mean either a second resolver — the exact defect
448863f20 removed — or a test pinned to a passing endpoint that never failed.
The card is closed on the measurement above.

Sibling cards from this run that are the same cause and can be closed the same
way once their branches are checked: the open `qa-fail-*` set behind PRs #1933,
#1934 and #1936, plus the `gcpl-cset-*`/`gcpl-perf-*` cards still in
`scheduled`/`validating`. Each should be re-run against current `main` before
any code is written for it.

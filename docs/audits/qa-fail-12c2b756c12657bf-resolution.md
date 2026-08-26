# qa-fail-12c2b756c12657bf — gcpl-dft-13 was the merged base-URL defect, filed again

**Card:** `[QA] gcpl-dft: GovCon AI Drafting — DRAFT > gcpl-dft-13: POST /api/govcon/knowledge-base creates new KB entry`
**Run:** `qa-1787705278` **Spec:** `tests/e2e/govcon_drafting.spec.ts:163`
**Reported:** `expect([200, 201, 400, 409]).toContain(403)`
**Resolution:** no code change — already fixed on `main` by 448863f20 (#1935), verified below.

## Read the assertion the right way round

The card's error text renders as `Expected value: 403 / Received array: [200, 201,
400, 409]`, which invites reading 403 as the *wanted* status. It is the reverse:
Playwright's `toContain` prints the container as `received` and the needle as
`expected`, so the array is the spec's accepted list and **403 is what the route
actually returned**. The symptom is a forbidden POST, not a missing one.

## It was never the knowledge-base endpoint

`POST /api/govcon/knowledge-base` answers **200** when the CSRF token it carries
was minted at the same host as the session cookie it carries. Measured against
the live dashboard before touching anything, same payload, only the token's
origin varied:

| session cookie jar | `X-CSRF-Token` minted at | result |
|---|---|---|
| `localhost:5050` | `127.0.0.1:5050` | **403** `{"code":"CSRF_FAILED"}` |
| `localhost:5050` | `localhost:5050`  | **200** |

And the route does real work when asked properly — a valid payload returns
`{"block_id":"69d13067-…","status":"ok"}`, i.e. it creates. The route is correct
and always was.

## The cause was already found, fixed and merged

This is one of the cards from run `qa-1787705278` that share a single fixture
defect: `playwright.config.ts` resolved the URL under test as
`ICDEV_E2E_BASE_URL || ICDEV_DASHBOARD_URL || http://localhost:<PORT>` while nine
spec-local `BASE` constants resolved it as `ICDEV_DASHBOARD_URL ||
http://localhost:5050`. Those agree whenever exactly one variable is set; that
sweep set **both**, divergently. A cookie jar is keyed by host, so
`fixtures/auth.ts` bootstrapped the CSRF double-submit handshake at one origin
while each spec issued its requests at the other.

448863f20 (`qa-fail-a5dbf266dfb0ce4a`, PR #1935) made
`tests/e2e/fixtures/base_url.ts::resolveBaseUrl()` the suite's only precedence
and converted every consumer. `govcon_drafting.spec.ts` takes its `BASE` from
`tests/e2e/fixtures/govcon_cpmp.ts`, which is one of them, and `fixtures/auth.ts`
takes its bootstrap origin from the same function — so this card was covered by
construction the moment that PR landed, without anybody naming `gcpl-dft-13`.
Confirmed present here: `git merge-base --is-ancestor 448863f20 HEAD` succeeds.

## Verified, under the environment that produced the failure

Both variables set and divergent, exactly as the sweep had them:

```
ICDEV_E2E_BASE_URL=http://127.0.0.1:5050 \
ICDEV_DASHBOARD_URL=http://localhost:5050 \
npx playwright test tests/e2e/govcon_drafting.spec.ts --project=chromium
→ 13 passed (14.6s)
```

That is the whole spec, not only `gcpl-dft-13`.
`tests/test_e2e_base_url_single_source.py`, the gated guard that fails a spec
re-deriving the expression, is green (7 passed).

## Why this is filed rather than built

Nothing in the tree needed to change, so there is no fix to prove RED-first.
Writing one anyway would mean either a second resolver — the exact defect
448863f20 removed — or a test pinned to a passing endpoint that never failed.
The card is closed on the measurement above.

## Two things worth carrying forward

**The ordinal in these titles is not trustworthy.** Two independent branches each
titled themselves "filed a 16th time" — 4091366ae (NOCC, merged as #1941) and
0c91ffb73 (`qa-fail-86fe338f55f5295b`, open as #1943). Concurrent sessions each
counted the merged history they could see and neither could see the other, so the
sequence now has a duplicate in it. The count is a hand-maintained number in a
commit subject with no uniqueness check; treat it as "many", and do not derive a
total by reading the newest title. This title therefore states no number.

**`gcpl-dft-13` cannot currently tell creation from rejection**, independent of
this defect. It posts `category: 'compliance'`, which is not a valid category —
the endpoint answers **200** with `{"message":"Invalid category: compliance.
Valid: ['capability_description', 'approach', 'staffing', 'tools_used',
'past_…']"}` and creates nothing, and the spec accepts 200, so the test named
"creates new KB entry" passes on a request that created no entry. Two separable
issues sit under that: the test sends an invalid category, and the route returns
200 for a rejected write where the spec's own list already anticipates 400. Both
are out of scope for this card — it was filed on the 403 and the 403 is gone —
but neither is fixed by closing it, and a card that asserts creation should
assert the `block_id`.

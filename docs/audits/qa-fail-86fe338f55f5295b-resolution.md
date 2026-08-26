# qa-fail-86fe338f55f5295b — the NOCC incident lifecycle was the merged base-URL defect, filed a 16th time

**Card:** `[QA] NOCC — Incident Lifecycle > create an incident then update its status`
**Run:** `qa-1787705278` **Spec:** `tests/e2e/noc_canvas.spec.ts:96`
**Reported:** `expect(create.status()).toBe(201)` received `403`
**Resolution:** no code change — already fixed on `main` by 448863f20 (#1935), verified below.

## It was never the incidents endpoint

`POST /api/noc/incidents` answers **201** when the `X-CSRF-Token` it carries was
minted at the same host as the session cookie it carries. Measured against the
live dashboard before touching anything — same endpoint, same body
(`{"title": "E2E outage", "severity": "p2"}`), only the token's origin varied:

| session cookie jar | `X-CSRF-Token` minted at | result |
|---|---|---|
| `localhost:5050` | `127.0.0.1:5050` | **403** `{"code":"CSRF_FAILED"}` |
| `localhost:5050` | `localhost:5050`  | **201** `{"id":"585505ea-…","incident_number":"INC-2026-585505"}` |

The two origins mint genuinely different tokens (`4f43ca53…` vs `8980c86c…`),
which is the whole mechanism. The route is correct and always was.

## The cause was already found, fixed and merged

This is one of the **14 cards from run `qa-1787705278`, across 8 spec files,
that are the same fixture defect** — `playwright.config.ts` resolved the URL
under test as `ICDEV_E2E_BASE_URL || ICDEV_DASHBOARD_URL || http://localhost:<PORT>`
while nine spec-local `BASE` constants resolved it as
`ICDEV_DASHBOARD_URL || http://localhost:5050`. Those agree whenever exactly one
variable is set; that sweep set **both**, divergently. A cookie jar is keyed by
host, so `fixtures/auth.ts` bootstrapped the CSRF double-submit handshake at one
origin while each spec issued its requests at the other.

448863f20 (`qa-fail-a5dbf266dfb0ce4a`, PR #1935) made
`tests/e2e/fixtures/base_url.ts::resolveBaseUrl()` the suite's only precedence
and converted every consumer. **`tests/e2e/noc_canvas.spec.ts` is one of them**
— it is named in `fixtures/auth.ts` as one of the three specs whose failures went
through `page.request` — so this card was covered by construction the moment that
PR landed, without naming the incident lifecycle test.

## Verified before AND after, under the environment that produced the failure

Both variables set and divergent, exactly as the sweep had them. The pre-fix tree
was still checked out in the main working copy (`415c01d06`, which carries the old
two-copy expression), so the same command against both trees is a real before/after
rather than an assertion that the fix works:

```
ICDEV_E2E_BASE_URL=http://127.0.0.1:5050 \
ICDEV_DASHBOARD_URL=http://localhost:5050 \
ICDEV_NO_SERVER=1 npx playwright test tests/e2e/noc_canvas.spec.ts --project=chromium

  415c01d06 (pre-fix, spec-local BASE)  ->  5 failed, 10 passed
  995bb9b02 (post-fix, resolveBaseUrl)  -> 15 passed (37.3s)
```

All five pre-fix failures are this one cause, and the card's own test is among
them. The other four (`overview page loads`, both alarm-lifecycle tests, and
`RFC + MOP`) were filed as their own cards by the same sweep.
`tests/test_e2e_base_url_single_source.py`, the gated guard that fails a spec
re-deriving the expression, is green (7 passed).

## Why this is filed rather than built

Nothing in the tree needed to change, so there is no fix to prove RED-first.
Writing one anyway would mean either a second resolver — the exact defect
448863f20 removed — or a test pinned to an endpoint that never failed.
The card is closed on the measurements above.

**Two open PRs are stale duplicates of this same cause** and should be closed
rather than merged: #1933 (`kanban/qa-fail-84f92cebcf4fe498`) and #1934
(`kanban/qa-fail-b2537204d4a9b6dd`) both re-implement the base-URL resolver that
#1935 already merged. A third was not opened for this card.

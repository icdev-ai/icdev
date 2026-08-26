# qa-fail-550375c3c18c9474 — the NOCC alarm-ingest 403 was the merged base-URL defect, filed a 16th time

**Card:** `[QA] NOCC — Alarm Lifecycle > alarm ingest with no body is rejected 400 (not 401)`
**Run:** `qa-1787705278` **Spec:** `tests/e2e/noc_canvas.spec.ts:92`
**Reported:** `expect(received).toBe(400)` — received `403`
**Resolution:** no code change — already fixed on `main` by 448863f20 (#1935), verified below.

## It was never the alarm endpoint

`POST /api/noc/alarms` with an empty body answers **400**, which is exactly what
the test asserts. `tools/noc_canvas/blueprint.py:186` reads the body and refuses
when neither `alarm_source` nor `description` is present, before touching the
database.

Measured against the live dashboard before touching anything — same endpoint,
same empty body, same session cookie jar, only the origin the `icdev_csrf`
token was minted at varied:

| session cookie jar | `X-CSRF-Token` minted at | result |
|---|---|---|
| `localhost:5050` | `localhost:5050`  | **400** `{"error":"alarm_source and description are required"}` |
| `localhost:5050` | `127.0.0.1:5050` | **403** `{"code":"CSRF_FAILED"}` |

The two hosts mint different tokens (`c1080a5b3e08…` vs `f22274845a2f…`), and
`csrf_protect` runs before the route. So the request never reached the
validation the test was written to exercise. The route is correct and always was.

The card's own title records why this reads as a product defect rather than a
fixture one: the test guards against `401`, and the failure is a `403`. The auth
`before_request` hook logs the caller in *before* `csrf_protect` looks at the
session, so a cookieless mutating request fails an integrity check rather than
an authentication one — which looks like an authorization bug in the NOC
mutation gate (`cnr-ops-01`) and is not.

## The cause was already found, fixed and merged

This is one of **39 cards from run `qa-1787705278`** that are the same fixture
defect. `playwright.config.ts` resolved the URL under test as
`ICDEV_E2E_BASE_URL || ICDEV_DASHBOARD_URL || http://localhost:<PORT>` while
nine spec-local `BASE` constants resolved it as
`ICDEV_DASHBOARD_URL || http://localhost:5050`. Those agree whenever exactly one
variable is set; that sweep set **both**, divergently
(`ICDEV_E2E_BASE_URL=http://127.0.0.1:5050` beside
`ICDEV_DASHBOARD_URL=http://localhost:5050`). A cookie jar is keyed by host, so
`fixtures/auth.ts` bootstrapped the CSRF double-submit handshake at one origin
while each spec issued its absolute-URL requests at the other.

448863f20 (`qa-fail-a5dbf266dfb0ce4a`, PR #1935) made
`tests/e2e/fixtures/base_url.ts::resolveBaseUrl()` the suite's only precedence
and converted every consumer. **`tests/e2e/noc_canvas.spec.ts` is one of the
nine files that PR changed**, so this card was covered by construction the
moment it landed, without naming the NOCC alarm test.

## Verified, under the environment that produced the failure

Both variables set and divergent, exactly as the sweep had them:

```
ICDEV_E2E_BASE_URL=http://127.0.0.1:5050 \
ICDEV_DASHBOARD_URL=http://localhost:5050 \
npx playwright test tests/e2e/noc_canvas.spec.ts --project=chromium
→ 15 passed (29.0s)
```

That is the whole spec, not only this test. `tests/test_e2e_base_url_single_source.py`,
the gated guard that fails a spec re-deriving the expression, is green (7 passed).

## Three sibling cards close on the same run

Run `qa-1787705278` filed four cards against `noc_canvas.spec.ts`, and all four
tests pass in the run above:

| card | test | line |
|---|---|---|
| `qa-fail-550375c3c18c9474` (this one) | alarm ingest with no body is rejected 400 | 92 |
| `qa-fail-dcfb057208b84116` | ingest, acknowledge, then clear an alarm | 72 |
| `qa-fail-86fe338f55f5295b` | create an incident then update its status | 101 |
| `qa-fail-3cad345eb6db2176` | create an RFC then generate a MOP | 125 |

The other three are still `scheduled`/`validating`. Each is the same cause and
can be closed on this measurement rather than dispatched — every one of them is
a mutating POST/PUT through the same fixture.

## Why this is filed rather than built

Nothing in the tree needed to change, so there is no fix to prove RED-first.
Writing one anyway would mean either a second base-URL resolver — the exact
defect 448863f20 removed — or a test pinned to an endpoint that never failed.
The card is closed on the measurement above.

This is the **16th** filing of one merged defect (`qa-fail-fb24fbc8b0761c5f` was
the 15th, see `docs/audits/qa-fail-fb24fbc8b0761c5f-resolution.md`). The
remaining open `qa-fail-*` cards from this run should each be re-run against
current `main` before any code is written for them.

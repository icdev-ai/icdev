# qa-fail-230a8da047055c77 — the CPMP EVM snapshot POST was the merged base-URL defect, filed an 18th time

**Card:** `[QA] gcpl-evm: CPMP EVM Engine — CPMP-EVM > gcpl-evm-01: POST /api/cpmp/contracts/<id>/evm records monthly snapshot`
**Run:** `qa-1787705278` **Spec:** `tests/e2e/cpmp_evm.spec.ts:38`
**Reported:** `expect([200, 201, 400, 409]).toContain(resp.status())` received `403`
**Resolution:** no code change — already fixed on `main` by 448863f20 (#1935), verified below.

## It was never the EVM endpoint

`POST /api/cpmp/contracts/<id>/evm` answers from its own handler when the
`X-CSRF-Token` it carries was minted at the same host as the session cookie it
carries. Measured against the live dashboard before touching anything — one
cookie jar bootstrapped at `127.0.0.1:5050` exactly as `fixtures/auth.ts` does,
one token, one body, one contract (`df32ba49-…`), and **only the host spelling of
the POST varied**:

| POST origin | result |
|---|---|
| `http://127.0.0.1:5050` | **400** `{"message":"wbs_id and period_date required"}` |
| `http://localhost:5050` | **403** `{"code":"CSRF_FAILED"}` |

The 400 is the point, not a second defect: my probe deliberately sent
`wbs_id: null`, so the request reached the handler and the handler validated the
body. **400 is inside the test's own accepted set** `[200, 201, 400, 409]` — at
the correct origin even a degenerate body satisfies `gcpl-evm-01`. The route is
correct and always was.

The competing explanation was ruled out in one query rather than assumed.
`require_role` writes a `dashboard_auth_log` row on every refusal and
`csrf_protect` writes none; the newest `permission_denied` row on this board is
**2026-07-08** (104 rows lifetime), seven weeks before the sweep. No role denied
anything here.

## The cause was already found, fixed and merged

This is one of the cards from run `qa-1787705278` that are the same fixture
defect — `playwright.config.ts` resolved the URL under test as
`ICDEV_E2E_BASE_URL || ICDEV_DASHBOARD_URL || http://localhost:<PORT>` while the
spec-local `BASE` constants resolved it as
`ICDEV_DASHBOARD_URL || http://localhost:5050`. Those agree whenever exactly one
variable is set; that sweep set **both**, divergently. A cookie jar is keyed by
host, so `fixtures/auth.ts` bootstrapped the CSRF double-submit handshake at one
origin while the spec issued its requests at the other.

448863f20 (`qa-fail-a5dbf266dfb0ce4a`, PR #1935) made
`tests/e2e/fixtures/base_url.ts::resolveBaseUrl()` the suite's only precedence and
converted every consumer. This spec takes its `BASE` from
`tests/e2e/fixtures/govcon_cpmp.ts`, which is one of them — it imports
`resolveBaseUrl` and cites this defect in a comment — so the card was covered by
construction the moment that PR landed, without naming the EVM test.

## Verified before AND after, under the environment that produced the failure

Both variables set and divergent, exactly as the sweep had them. The pre-fix tree
was still checked out in the main working copy (`415c01d06`, which predates
`fixtures/base_url.ts` entirely), so the same command against both trees is a real
before/after rather than an assertion that the fix works:

```
ICDEV_E2E_BASE_URL=http://127.0.0.1:5050 \
ICDEV_DASHBOARD_URL=http://localhost:5050 \
npx playwright test tests/e2e/cpmp_evm.spec.ts --project=chromium

  415c01d06 (pre-fix, spec-local BASE)  ->  1 failed, 11 passed (35.3s)
  2ac088fda (post-fix, resolveBaseUrl)  -> 12 passed (22.0s)
```

The single pre-fix failure **is the card's own test**, and the spec's only
mutating call. The other eleven are GETs and all eleven passed on both trees —
the signature shape of this defect, and the reason it reads as a product bug: a
cookie jar keyed by host cannot break a request that needs no session.

`tests/test_e2e_base_url_single_source.py`, the gated guard that fails a spec
re-deriving the expression, is green (7 passed).

## Why this is filed rather than built

Nothing in the tree needed to change, so there is no fix to prove RED-first.
Writing one anyway would mean either a second resolver — the exact defect
448863f20 removed — or a test pinned to an endpoint that never failed.
The card is closed on the measurements above.

`cpmp_evm.spec.ts` drew exactly one card from this sweep, so unlike the two NOCC
filings (`qa-fail-550375c3c18c9474`, `qa-fail-86fe338f55f5295b`, same spec file,
different endpoints) there is no sibling to cross-reference.

**One open PR is a stale duplicate of this same cause** and should be closed
rather than merged: #1933 (`kanban/qa-fail-84f92cebcf4fe498`) re-implements the
base-URL resolver that #1935 already merged.

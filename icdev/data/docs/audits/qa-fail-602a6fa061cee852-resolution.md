# qa-fail-602a6fa061cee852 — the CPMP subcontractor create was the merged base-URL defect, filed an 18th time

**Card:** `[QA] gcpl-perf: CPMP Performance Tracking — CPMP-PERF > gcpl-perf-01: POST /api/cpmp/contracts/<id>/subcontractors adds sub with FAR 52.219-9 fields`
**Run:** `qa-1787705278` **Spec:** `tests/e2e/cpmp_performance.spec.ts:29`
**Reported:** `expect(resp.status()).toBe(201)` received `403`, body `{"code":"CSRF_FAILED"}`
**Resolution:** no code change — already fixed on `main` by 448863f20 (#1935), verified below.

## The fix commit names this exact spec

This card needs less inference than its seventeen siblings did. 448863f20's own
message records the re-run that closed it:

> `cpmp_performance 2 failed -> 21 passed`

`tests/e2e/cpmp_performance.spec.ts` **is** the spec on this card, and the PR
originated from `gcpl-perf-09` — a different test in this same file, from this
same run. `gcpl-perf-01` was one of the 21 that went green, without being named.
The spec draws `BASE` from `tests/e2e/fixtures/govcon_cpmp.ts`, which that PR
converted to the shared resolver (`fixtures/govcon_cpmp.ts`, +10/-…), so this
card was covered by construction the moment #1935 landed.

## It was never the subcontractors endpoint

`POST /api/cpmp/contracts/<id>/subcontractors` answers **201** when the
`X-CSRF-Token` it carries was minted at the same host as the session cookie it
carries. Measured against the live dashboard on this card's own endpoint —
same contract, same body, **only the token's origin varied**:

| session cookie jar | `X-CSRF-Token` minted at | result |
|---|---|---|
| `localhost:5050` | `127.0.0.1:5050` | **403** `{"code":"CSRF_FAILED","error":"CSRF token missing or invalid",…}` |
| `localhost:5050` | `localhost:5050`  | **201** `{"status":"ok","sub_id":"ccfe91fe-…"}` |

The two origins mint genuinely different tokens (`565e3c26…` vs `b785aa04…`),
which is the whole mechanism. The 403 body is byte-identical to the one on the
card. The route is correct and always was.

Contract under test: `df32ba49-9c39-4760-b0f0-51d3c44697e5`. The probe left one
extra `GCPL Origin Probe LLC` subcontractor row behind — the same kind of row the
spec itself writes on every run, disclosed rather than deleted, because removing
it is a destructive write against the live board to tidy a test artifact.

## Verified under the environment that produced the failure

Both variables set and divergent, exactly as the sweep had them:

```
ICDEV_E2E_BASE_URL=http://127.0.0.1:5050 \
ICDEV_DASHBOARD_URL=http://localhost:5050 \
ICDEV_NO_SERVER=1 npx playwright test tests/e2e/cpmp_performance.spec.ts --project=chromium

  3d387533e (post-fix, resolveBaseUrl)  ->  21 passed (22.0s)
```

All 21 pass, `gcpl-perf-01` among them — the card's own test, under the exact
environment that failed it. Unlike the three prior audits there is no pre-fix
tree left in the main working copy to re-run (it has advanced to `3d387533e`),
so the "before" here is the endpoint-level table above rather than a second
whole-spec run: it isolates the one variable the fix changed, on the one
endpoint this card names, which the whole-spec run cannot do.

`tests/test_e2e_base_url_single_source.py`, the gated guard that fails any spec
re-deriving the precedence, is green (7 passed).

## Why this is filed rather than built

Nothing in the tree needed to change, so there is no fix to prove RED-first.
Writing one anyway would mean either a second resolver — the exact defect
448863f20 removed — or a test pinned to an endpoint that never failed.
The card is closed on the measurements above.

## Duplicate PRs from this same cause remain open

Still open and still stale, both re-implementing the resolver #1935 already
merged; they should be closed rather than merged:

* **#1933** `kanban/qa-fail-84f92cebcf4fe498`
* **#1934** `kanban/qa-fail-b2537204d4a9b6dd`

Both were already named for closure in `qa-fail-86fe338f55f5295b-resolution.md`
(the 17th filing) and are still open, which is itself worth noting: the audit
trail records the recommendation but nothing acts on it. **#1937**
(`kanban/qa-fail-0d954757a83824da`, `gcpl-cset` — CPMP Portfolio & Contract
Setup) is a fourth card from this same run and same CPMP family, and #1944
(`gcpl-dft-13`) closed as the same cause. No PR was opened for this card.

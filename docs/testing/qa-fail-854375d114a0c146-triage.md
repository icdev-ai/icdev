# CUI // SP-CTI

# Triage — qa-fail-854375d114a0c146 (2026-10-17)

**Test:** `nav_intelligence_compliance.spec.ts` — Compliance Menu > Compliance Debt
(`/boundary/compliance-debt`) — HTTP < 400
**Filed by:** QA sweep run `qa-1792189214` (see `qa-sweep-833abf45-2026-10-16.md`)
**Error:** `TimeoutError: apiRequestContext.get: Timeout 10000ms exceeded.`

## Verdict: transient, no code defect — no route change made

The handler is `bdc_compliance_debt_page` in `tools/boundary_canvas/blueprint.py`. It
only calls `render_template("boundary_canvas/compliance_debt.html")`. It does no
database or network work, so there is nothing in the request path that could take
10s by itself. The page's data loads afterwards, client-side, from `/api/compliance-debt/*`.

## Evidence

| check | result |
|---|---|
| `curl` `127.0.0.1:5050/boundary/compliance-debt` | 200 in 0.26s |
| `curl` `localhost:5050/...` x5 | 200 in 0.45–0.48s, every time |
| sweep's own post-run re-check (2026-10-16) | 0.58s / 0.57s, did not reproduce |
| `playwright test nav_intelligence_compliance.spec.ts -g "Compliance Debt"` | **2 passed** (HTTP < 400, no server error) in 8.5s |

The timeout came during a 62-minute, 853-test run. Four other GET timeouts in the same
run hit unrelated routes. That points to load-time contention on the shared
dashboard, not to this route. The sampler logged no host stall at this
failure's timestamp, so `during_stall` was `no`. A sampler that polls every few
seconds can still miss a short stall.

## Not done, deliberately

The 10s per-request budget in the spec was not raised. A 0.5s route that needed 10s was
held up by something outside the route, and widening the budget would hide real
regressions on the routes that really are slow (`/genesis`, `/health/canvases`; those
have their own cards).

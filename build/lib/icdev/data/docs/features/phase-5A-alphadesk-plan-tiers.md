# CUI // SP-CTI
# Phase 5A — FathomDesk Plan Tiers + Quota Enforcement

Landed 2026-04-17. Tracks as Task #80.

## What

Introduces tenant-scoped plan tiers (`free` / `pro` / `enterprise`) with
a feature matrix and hard quotas that the 4 high-value creation endpoints
now enforce. Adds a `/billing` page and an operator CLI for tier
management. Stripe wiring is **deliberately deferred** to Phase 5B.

## Files

| Path | Role |
|------|------|
| `args/plan_tiers.yaml` | Tier catalog (hot-reloaded) |
| `tools/trading/billing/__init__.py` | Module marker + API doc |
| `tools/trading/billing/tiers.py` | Loader, tier lookup, quota engine, usage summary |
| `tools/trading/billing/admin_cli.py` | `list-tiers`, `list-tenants`, `set-tier`, `usage` |
| `tools/trading/dashboard/templates/billing.html` | `/billing` page |
| `tools/trading/dashboard/app.py` | 4 quota gates + `/billing` + `/api/billing/summary` + `/api/billing/tier` |
| `tools/trading/dashboard/templates/base.html` | Sidebar link to `/billing` |

## Tier matrix (as shipped)

| Feature / Quota | free | pro | enterprise |
|---|---|---|---|
| Members | 1 | 10 | unlimited |
| Alert rules | 10 | 100 | unlimited |
| API tokens per user | 5 | 25 | unlimited |
| Share links / month | 10 | 100 | unlimited |
| Invitations / month | 3 | 50 | unlimited |
| Watchlist tickers | 25 | 250 | unlimited |
| Target allocations | 10 | 100 | unlimited |
| White-label branding | ✗ | ✓ | ✓ |
| PDF branding | ✗ | ✓ | ✓ |
| Advisor /clients page | ✗ | ✓ | ✓ |
| SSO / SAML | ✗ | ✗ | ✓ |
| Priority support | ✗ | ✓ | ✓ |
| Monthly price | $0 | $49 | contact sales |

All values live in `args/plan_tiers.yaml` — hot-reloaded, no code change
needed to adjust.

## Quota enforcement contract

Callers measure the current count, then call
`tiers.check_quota(tenant_id, key, current_count)` before persisting a
new row. On breach, `QuotaExceeded` (subclass of `ValueError`) is raised
with a user-facing message; the endpoint returns HTTP **402 Payment
Required** with body `{error, quota, limit, tier, upgrade_url: "/billing"}`.

## Admin CLI

```bash
python -m tools.trading.billing.admin_cli list-tiers
python -m tools.trading.billing.admin_cli list-tenants
python -m tools.trading.billing.admin_cli set-tier --tenant default --tier pro
python -m tools.trading.billing.admin_cli usage --tenant default
```

## Deferred to Phase 5B

- Stripe Checkout Session for upgrades (tier → `stripe_price_id`)
- Webhook handler for subscription lifecycle events
- Self-service payment-method portal link
- Invoice emails + dunning + proration
- Downgrade grace period

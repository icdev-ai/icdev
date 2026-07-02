# Billing

> Shard of `tools/manifest.md`. See index at `tools/manifest.md`.

## Billing

| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Metering | tools/billing/metering.py | Fire-and-forget usage metering — `record_usage(tenant_id, event_type, quantity, **kwargs)` writes to `usage_events` in a background daemon thread; `get_usage_summary(tenant_id, since)` returns `{event_type: total_quantity}`. Event types: `llm_token`, `api_call`, `storage_mb`, `canvas_load`, `coworker_task`. | `record_usage(tenant_id, event_type, quantity)` | None (async write); `get_usage_summary` → `dict` |
| Stripe Webhook Handler | tools/billing/stripe_handler.py | Stripe webhook HMAC-SHA256 signature verifier + billing_status sync. `verify_stripe_signature(payload, sig_header)` validates timing + signature; `handle_webhook(event_type, event_data)` maps `invoice.payment_succeeded/failed` and `customer.subscription.deleted` to `billing_status` updates on the `tenants` table. Requires `ICDEV_STRIPE_WEBHOOK_SECRET` env var. | `handle_webhook(event_type, event_data)` | `dict {handled, event_type, tenant_id, billing_status}` |
| Tier (shim) | tools/billing/tier.py | Backward-compat shim re-exporting `TIER_ORDER`, `get_active_tier`, `tier_satisfies` from `icdev.tools.billing.tier`. New code should import from `icdev.tools.billing.tier` directly. | imported | re-exports canonical tier symbols |

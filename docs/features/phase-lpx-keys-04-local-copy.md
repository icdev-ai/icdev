# LPX keys-04 — Local canvas copy distribution: per-person keys, no real key on any laptop

CUI // SP-CTI

## The problem this closes

ICDEV canvases run as **local copies**, one per person. If the real provider key
were distributed into N local `.env` files it:

- could not be revoked for one person without rotating it for everyone, and
- would break every copy simultaneously when rotated.

So a local copy must reach the **shared, hosted gateway** with a **per-person
virtual key** (issued via `lpx-keys-01`, budgeted via `lpx-keys-02`, revoked/
rotated per-person via `lpx-keys-03`). The real key stays server-side on the
gateway and is never distributed.

## What shipped

### 1. Fail-closed egress (in `tools/llm/proxy_gateway.py`, called from the router)

Local-copy mode is opt-in via `ICDEV_LLM_LOCAL_COPY=true`. When on:

- `apply_gateway_to_provider_cfg` redirects every **cloud** provider to the
  gateway **even if** `ICDEV_LLM_PROXY_ENABLED` is unset (a local copy has no
  other legitimate path) and **forces** `api_key_env` to the virtual-key env var.
  A real provider key in the environment is therefore **never read** — even if a
  caller swallows the guard below. Local (`ollama`) and GovCloud (`bedrock`)
  types are untouched.
- `enforce_local_copy_egress` raises `LocalCopyEgressError` with operator-facing
  onboarding guidance when no virtual key is provisioned. It is called in
  `router._get_provider` **outside** the broad `try/except` that wraps gateway
  resolution, so the clear message is not swallowed.

Result: a local copy with no reachable gateway / no virtual key **fails closed**
with a clear message; it never silently falls back to a real key.

> Defense in depth: the swallow-proofing (forced virtual key) and the
> clear-message guard are two independent mechanisms. Even if the raise is
> caught somewhere upstream, the credential presented is always the virtual key
> (empty → auth failure at the gateway), so no real key can leak.

### 2. Onboarding that makes the wrong thing hard

`.env.local-copy.template` — the per-person laptop template. It ships the gateway
URL and a virtual-key slot and **no real-provider-key slot** (no
`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `AWS_SECRET_ACCESS_KEY`, or
`LITELLM_MASTER_KEY`). If `icdev init` writes the `.env` for a local copy (see the
`pkg-` card), this template is the surface it should write.

### 3. Preflight

`local_copy_preflight()` reports `{local_copy, virtual_key_set, gateway_url,
gateway_reachable, ok, message}` for onboarding/health checks — never raises.

## Per-person revocation

Revocation/rotation are per-key (`lpx-keys-03`) and take effect immediately for
every enforcement path (lookup/check read the flipped status) without touching
anyone else's key — exactly the property distributing the real key would destroy.

## Dependency

Depends on `lpx-router-03` (green): the six direct-to-provider call sites are
migrated and the `provider_bypass` coherence gate blocks new ones, so a local
copy has no bypass path that would tempt an operator into pasting a real key.

## Tests

`tests/test_lpx_local_copy.py` (7): forced virtual key never real, redirect even
without proxy-enabled, no-virtual-key fail-closed with clear message,
local/GovCloud untouched, non-local-copy no-op, preflight status, and the
onboarding template has no real-key slot. Airgap regression
(`test_lpx_airgap_no_proxy_dependency`) and proxy-gateway tests stay green.

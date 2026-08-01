# LPX final wave — observability, CUI egress, and V&V

**Classification:** CUI // SP-CTI
**Status:** shipped
**Tasks:** `lpx-obs-01`, `lpx-obs-02`, `lpx-egress-02`, `lpx-vv-01`, `lpx-vv-02`, `lpx-xcut-01`

This is the final wave of the LLM Proxy Layer (LPX). Waves 1–2 shipped the proxy
gateway, virtual keys, per-key/team budgets, and rate ceilings. This wave adds
observability, the CUI egress guarantee, and the verification that ties the
card's central claim to tests. The proxy remains **opt-in and off by default**;
with it disabled, ICDEV behaves byte-identically to before.

## What shipped

| Task | Deliverable |
|------|-------------|
| `lpx-obs-01` | `tools/llm/proxy_metrics.py` — proxy spend/rate metrics on the Ops Hub LLMOps page (`/ops/llm`). Aggregates the ICDEV ledger (`llm_proxy_spend`, `llm_proxy_team_usage`) with a best-effort Prometheus scrape of the LiteLLM `/metrics` endpoint. Air-gap safe (never requires the proxy). |
| `lpx-obs-02` | `tools/llm/proxy_reconcile.py` — reconciles proxy spend vs `token_tracker` by windowed aggregation (not a row join), with a configurable divergence gate and explained structural gaps. |
| `lpx-egress-02` | CUI egress gate in `tools/llm/proxy_gateway.py` + `router._enforce_routing_policy`: classified/controlled content never silently traverses the proxy to a cloud provider. Fail-closed, invoke-time, configurable ceiling (default UNCLASSIFIED). |
| `lpx-vv-01` | `tests/test_lpx_no_real_key_reachable.py` — proves no real provider key is reachable when the proxy is enabled (all cloud types), byte-identical when disabled, and that the six migrated call sites read no real key. |
| `lpx-vv-02` | `tests/e2e/test_lpx_vv02_key_abstraction_e2e.py` — an academy/gameday session proves the key abstraction end to end (per-guild key issuance, virtual-key-only, action succeeds with no real key). UI reachability verified via Playwright. |
| `lpx-xcut-01` | Close-out: committed + amended the strategy docs, manifest/commands entries for the new tools, this feature writeup, companion + coherence sweep. |

## Key design points

- **Observability without coupling.** The two cost records (proxy vs
  `token_tracker`) are surfaced separately and reconciled by aggregation, never
  joined — they are keyed differently and a join would be empty/fragile.
- **CUI stays local.** The proxy is a new cloud egress path; the egress gate
  ensures CUI+ never traverses it by default. Raising the ceiling
  (`ICDEV_LLM_PROXY_MAX_CLASSIFICATION`) is an explicit ATO-boundary decision.
  Redaction is not bypassed — it already runs before provider resolution.
- **The central claim is tested, not asserted.** Virtual-key-only egress is
  proven deterministically across every cloud provider type and at the academy
  session boundary; the provider-bypass coherence gate keeps the six migrated
  sites from regressing.

## Strategy-doc corrections

The pre-implementation strategy (`docs/anthropic-proxy-strategy.md`) was amended
with two corrections the build established: the "just a `base_url` change" claim
was incomplete (six call sites bypassed the router and were migrated), and a
fourth product option — extending the in-house `bedrock_proxy.py` — was missing
from the comparison. See that doc's "Corrections established by the LPX build"
section. `docs/keycloak-vs-proxy-sequencing.md` is committed as gate context
(Keycloak itself is out of scope for LPX).

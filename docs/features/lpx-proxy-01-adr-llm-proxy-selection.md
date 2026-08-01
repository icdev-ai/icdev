# ADR (LPX): LLM Proxy / Virtual-Key Layer — Product Selection

- **Status:** Accepted
- **Date:** 2026-07-25
- **Card:** LPX (LLM Proxy / virtual keys)
- **Task:** `lpx-proxy-01`
- **Supersedes/amends:** `docs/anthropic-proxy-strategy.md` (the strategy doc predates the
  finding that six call sites bypass the router; see "Correction" below).

---

## Context

ICDEV wants an opt-in LLM **proxy** layer so that a single shared provider key (e.g. an
Anthropic key used by `apps/forge_academy` / `apps/ai_gameday` cohorts) can be abstracted
behind **virtual keys** with per-cohort budgets, rate limits, rotation, and spend
observability — without every student or session holding the real provider credential.

The strategy doc `docs/anthropic-proxy-strategy.md` compared five external products and
recommended **LiteLLM Proxy** (MIT, Python/FastAPI, Docker-first). This ADR revisits that
recommendation under two constraints the strategy doc did not fully weigh, and records the
decision plus the explicit rejections so `lpx-proxy-02` has an unambiguous target.

### Correction to the strategy doc (load-bearing)

The strategy doc's Integration Touchpoints table claimed the only ICDEV change needed was a
`base_url` update and that `tools/llm/anthropic_provider.py` / `tools/llm/router.py` need
"None". That is **incomplete**. A router-only re-point leaves six call sites still POSTing
raw requests to `https://api.anthropic.com/v1/messages` with the real key read straight
from `os.environ["ANTHROPIC_API_KEY"]`:

- `tools/network/routes/ai.py` (three sites)
- `tools/network/routes/topology.py` (two sites)
- `tools/network/routes/twin_migration.py` (one site)

Any proxy decision is therefore contingent on `lpx-router-01/02/03` migrating those sites
onto `LLMRouter` and a coherence gate preventing regressions. The proxy only abstracts keys
for traffic that actually flows through the router.

---

## Decision

**Adopt LiteLLM Proxy as the opt-in, default-OFF proxy service** for cloud-egress cohort
use cases (academy / gameday), deployed as a Docker-Compose service behind a profile and a
feature flag, with the ICDEV LLM router pointed at it **only when the flag is on**.

Scope guardrails baked into the decision:

1. **Opt-in, OFF by default.** The proxy is a Compose profile that does not start by
   default (`lpx-proxy-02`) and a `.env` flag that, when unset, leaves every provider
   resolving to its existing `base_url` (`lpx-proxy-03`). Cloud egress must never become a
   new default path.
2. **Air-gap untouched.** With `ICDEV_LLM_PROVIDER=ollama` and two-tier disabled, no import
   or code path may depend on the proxy being present (`lpx-egress-01`).
3. **CUI stays local.** The proxy is a *cloud* egress path. Classified / CUI-adjacent
   traffic must not be routed to it; that boundary is enforced separately
   (`lpx-egress-02`). The in-tree Bedrock proxy remains the GovCloud/CUI credential path.
4. **In-tree operability.** Virtual-key issuance, budgets, rotation, and spend
   reconciliation are wrapped by ICDEV Python tools (`tools/llm/proxy_keys.py`,
   `lpx-keys-*`, `lpx-obs-*`) rather than leaving operators to `curl` the admin API.

---

## Options considered

### 1. LiteLLM Proxy — **SELECTED**
- MIT license, Python/FastAPI, Docker-Compose native — matches ICDEV's stack; ICDEV
  developers can patch/pin.
- Native Anthropic support (`/v1/messages` and OpenAI-compatible `/v1/chat/completions`),
  virtual keys, per-key/per-team RPM/TPM/budget caps, Prometheus metrics — the exact
  feature set the card requires, off the shelf.
- Can reuse ICDEV's existing PostgreSQL as its metadata store; no new database.
- **Cost accepted:** adds one third-party container to the SBOM / supply-chain surface. This
  is acceptable *because* the service is opt-in and default-OFF and is scoped to the
  cloud-egress academy/gameday use case, not the air-gapped ATO boundary. Pin the image by
  digest and record it in the SBOM when the profile is enabled.

### 2. Extend `tools/saas/bedrock/bedrock_proxy.py` into a general HTTP proxy — **REJECTED (kept for CUI)**
- The in-tree Bedrock proxy already does BYOK-vs-shared-pool credential resolution and
  per-tenant token metering against `platform.db`, and is CUI/audit-aware — attractive for
  staying in-tree and avoiding a new container in the ATO boundary.
- **Rejected as the academy/gameday proxy** because turning it into a general
  provider-agnostic HTTP reverse proxy means building virtual-key issuance, budget
  enforcement, RPM/TPM rate limiting, caching, and a spend dashboard ourselves —
  re-implementing LiteLLM's core value at material cost and maintenance burden, for a use
  case (cloud egress for cohorts) where a new container is acceptable.
- **Retained** as the CUI/GovCloud credential path: CUI-adjacent Bedrock traffic continues
  to use `bedrock_proxy.py` + `token_metering.py`, and is explicitly **not** routed through
  LiteLLM (`lpx-egress-02`). The two proxies serve disjoint trust zones.

### 3. Bifrost (Maxim AI) — REJECTED
- Apache-2.0, Claude-rate-limit-aware — genuinely strong on Anthropic tier management.
- Go binary: less hackable by ICDEV's Python-native team, fewer integrations, and no
  in-house patching path inside an air-gapped release. The Claude-rate-tier advantage does
  not outweigh losing Python-native operability.

### 4. Envoy AI Gateway — REJECTED
- K8s-CRD-only; virtual keys/budgets/caching require custom Envoy filter development for
  features LiteLLM ships out of the box. High operational complexity for a 20–80-seat
  cohort use case. Overkill.

### 5. Membrane API Gateway — REJECTED
- Java `llmGateway` plugin is capable, but Java is foreign to ICDEV's stack and adds a JVM
  runtime to the boundary. Smaller community than LiteLLM.

### 6. LLM API Key Proxy (Mirrowel) — REJECTED
- Pure-Python/MIT and simple, but no built-in per-user budgets or spend dashboards (the
  card's core requirement) and a very small community (~500 stars). Fine for a PoC, not for
  the budget-enforcement guarantee this card must make.

---

## Consequences

- `lpx-proxy-02` has an unambiguous target: **LiteLLM Proxy** as a Docker-Compose service
  behind a profile, image pinned by digest, OFF by default.
- `lpx-proxy-03` points `args/llm_config.yaml` provider `base_url` (+ virtual-key env) at
  the proxy **only** behind the feature flag; unset flag = today's behaviour.
- `lpx-proxy-04` (K8s manifests) applies only because a deployable service was chosen;
  follow existing `k8s/` conventions with a NetworkPolicy scoping egress.
- Supply-chain: the LiteLLM image is a new SBOM entry; it must be pinned and scanned when
  the profile is enabled, and it never ships in the air-gapped/CUI baseline.
- The Bedrock proxy remains the sanctioned CUI path; LiteLLM is cloud-egress only.

## Weighed explicitly (per task requirement)

| Concern | Disposition |
|---------|-------------|
| Air-gap / ATO boundary impact | Proxy is opt-in, OFF by default, never in the air-gap path; ATO baseline unchanged unless a program deliberately enables it. |
| SBOM + supply-chain surface | One new third-party container, pinned by digest, scanned on enable, excluded from the air-gapped/CUI baseline. |
| Can a 3rd-party proxy hold CUI-adjacent traffic? | **No.** LiteLLM is cloud-egress only; CUI/classified stays on the in-tree Bedrock proxy (`lpx-egress-02`). |
| Who operates it? | Cohort operators via ICDEV Python tools (`tools/llm/proxy_keys.py`, `lpx-keys-*`), not raw admin-API `curl`. |

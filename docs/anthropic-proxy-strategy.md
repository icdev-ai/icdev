# Anthropic API Key Proxy Strategy for ICDEV™ /gameday & /academy

## Executive Summary

**Yes.** One Anthropic API key can absolutely be shared behind a proxy and fully abstracted from students. The proxy sits between ICDEV and Anthropic's API, holding the real key server-side. Students (and ICDEV's LLM router) communicate with the proxy using lightweight *virtual keys* or no key at all. The proxy injects the real Anthropic key on the outbound leg.

This pattern is standard for multi-tenant LLM platforms. For ICDEV's Python/Flask + Docker Compose + K8s stack, the best-fit solution is **LiteLLM Proxy** (MIT license, Python, Docker-first).

---

## Current State — ICDEV's LLM Architecture

| Component | Relevance to Proxy Decision |
|-----------|----------------------------|
| `tools/llm/router.py` | Config-driven provider resolution via `args/llm_config.yaml`. Already supports `base_url` override for any provider. |
| `tools/llm/anthropic_provider.py` | Direct Anthropic SDK client. Accepts `api_key` and `base_url` in constructor. |
| `tools/llm/gateway.py` | Pre/post-invoke guardrails (injection detection, PII scrub, rate limits, cost caps, audit trail). |
| `tools/llm/rate_gate.py` | Process-level concurrency gate + inter-call pause. Supports `global`/`cluster` scope via file/PostgreSQL leases. |
| `tools/llm/proxy_resolver.py` | Rotating egress proxy resolver (HTTP_PROXY env). Already designed for proxying. |
| `docker-compose.yml` | 909-line GovCloud profile with pgvector, multi-service orchestration, health checks, resource limits. |
| `k8s/` | 30+ deployment manifests, HPA, RBAC, network policies, node autoscaler. |
| `apps/forge_academy/` | The academy module that students interact with. Uses the same LLM router as the rest of ICDEV. |

**Key insight:** ICDEV already has an internal "gateway" (`tools/llm/gateway.py`) but it is *client-side* — it wraps LLM calls with guardrails but does not proxy traffic or hide provider keys. The missing piece is a *server-side* reverse proxy that:
1. Holds the real Anthropic key
2. Issues virtual keys to students/tenants
3. Enforces per-student rate limits and budgets
4. Logs usage for cost attribution

---

## Top Open-Source Proxy Candidates for ICDEV

### 1. LiteLLM Proxy ⭐ RECOMMENDED

| Attribute | Detail |
|-----------|--------|
| **License** | MIT (free, no AGPL concerns) |
| **Language** | Python (FastAPI + async) |
| **Deploy** | Docker, Docker Compose, Helm, pip |
| **Anthropic support** | Native — both `/v1/messages` (Anthropic native) and `/v1/chat/completions` (OpenAI-compatible) |
| **Virtual keys** | ✅ Yes — issue per-student or per-team keys |
| **Rate limiting** | ✅ Per-key, per-model, per-user, per-team RPM/TPM/TPD |
| **Budget caps** | ✅ Per-key spend limits with soft/hard thresholds |
| **Caching** | ✅ Redis-backed semantic + prompt caching |
| **Observability** | ✅ Prometheus metrics, Langfuse/Langsmith tracing, spend dashboards |
| **Fallback routing** | ✅ Multi-provider failover (Anthropic → Bedrock → OpenRouter) |
| **Team/org mgmt** | ✅ Via UI or API |

**Why it fits ICDEV:**
- Same Python ecosystem — ICDEV developers can patch/contribute if needed
- Docker Compose is a one-line addition to the existing `docker-compose.yml`
- ICDEV's `anthropic_provider.py` only needs `base_url` changed to point at LiteLLM
- ICDEV's `openai_provider.py` (OpenAI-compatible) can also route through LiteLLM for unified billing
- Existing `llm_config.yaml` provider pattern maps cleanly to LiteLLM's `model_list`
- Supports virtual keys with budget caps — critical for 20-80 students where one rogue prompt loop could burn the whole monthly budget

**Quick Start for ICDEV:**
```yaml
# Add to docker-compose.yml
services:
  litellm-proxy:
    image: ghcr.io/berriai/litellm:main-latest
    ports:
      - "4000:4000"
    environment:
      ANTHROPIC_API_KEY: ${ANTHROPIC_API_KEY}
      LITELLM_MASTER_KEY: ${LITELLM_MASTER_KEY}  # Admin key for mgmt API
    volumes:
      - ./config/litellm_config.yaml:/app/config.yaml
    command: ["--config", "/app/config.yaml", "--port", "4000"]
    networks:
      - icdev-net
```

```yaml
# config/litellm_config.yaml
model_list:
  - model_name: claude-sonnet
    litellm_params:
      model: anthropic/claude-sonnet-4-20250514
      api_key: os.environ/ANTHROPIC_API_KEY

general_settings:
  master_key: os.environ/LITELLM_MASTER_KEY
  proxy_batch_write_at: 10
  database_url: postgresql://${ICDEV_PG_USER}:${ICDEV_PG_PASSWORD}@icdev-postgres:5432/icdev

# Per-student virtual keys with budgets
team_settings:
  - team_id: forge-academy-students
    max_budget: 50.0        # $50/month per team
    tpm_limit: 100000
    rpm_limit: 60
```

**ICDEV client-side change (minimal):**
```yaml
# args/llm_config.yaml — point anthropic provider at LiteLLM
providers:
  anthropic:
    type: anthropic
    api_key_env: LITELLM_STUDENT_KEY   # Virtual key, not real Anthropic key
    base_url: http://litellm-proxy:4000
```

---

### 2. Bifrost (by Maxim AI)

| Attribute | Detail |
|-----------|--------|
| **License** | Apache 2.0 |
| **Language** | Go |
| **Deploy** | Docker, Helm, binary |
| **Anthropic support** | ✅ Native, with Claude-specific rate limit awareness |
| **Virtual keys** | ✅ Per-consumer keys |
| **Rate limiting** | ✅ Built for Claude's tiered RPM/TPM/TPD limits |
| **Key distribution** | ✅ Multiple Anthropic keys across orgs for load balancing |
| **Observability** | ✅ Real-time dashboards |

**Why consider:** Purpose-built for Claude rate limit management. If Anthropic's rate tiers (Start → Build → Scale → Enterprise) are the primary pain point, Bifrost handles this better than generic proxies.

**Trade-off:** Go binary — less hackable by ICDEV's Python-native team. Fewer provider integrations than LiteLLM.

---

### 3. Envoy AI Gateway

| Attribute | Detail |
|-----------|--------|
| **License** | Apache 2.0 |
| **Language** | C++ (Envoy proxy) + Go (controller) |
| **Deploy** | Kubernetes CRDs only |
| **Anthropic support** | Via Envoy's extensible filter chain |
| **Virtual keys** | Via custom policies |
| **Rate limiting** | Via Envoy's token bucket + custom backends |

**Why consider:** ICDEV already has K8s manifests (`k8s/`). If the long-term target is K8s-native deployment, Envoy AI Gateway is the most "enterprise" choice.

**Trade-off:** Much higher operational complexity. Requires K8s expertise, CRD management, and custom Envoy filter development for features LiteLLM provides out-of-the-box (virtual keys, budgets, caching). Overkill for 20-80 students.

---

### 4. Membrane API Gateway

| Attribute | Detail |
|-----------|--------|
| **License** | Apache 2.0 |
| **Language** | Java |
| **Deploy** | Docker, K8s, standalone |
| **Anthropic support** | ✅ Native `llmGateway` plugin with per-user token budgets |
| **Virtual keys** | ✅ `simpleStore` with per-user API keys + token limits |

**Why consider:** Has a purpose-built `llmGateway` plugin for Anthropic with per-user token budgets and model whitelisting. Java ecosystem if ICDEV ever adds JVM services.

**Trade-off:** Java-based — foreign to ICDEV's Python stack. Smaller community than LiteLLM.

---

### 5. LLM API Key Proxy (by Mirrowel)

| Attribute | Detail |
|-----------|--------|
| **License** | MIT |
| **Language** | Python (FastAPI) |
| **Deploy** | Docker, executable (Windows/macOS/Linux) |
| **Anthropic support** | ✅ Native `/v1/messages` endpoint |
| **Virtual keys** | ✅ Single proxy key, multi-provider behind it |
| **Resilience** | ✅ Automatic key rotation, failover, cooldowns |

**Why consider:** Pure Python, MIT license, very simple. Good for a quick proof-of-concept.

**Trade-off:** Much smaller community (523 stars vs LiteLLM's 10K+). Fewer enterprise features (no built-in budget dashboards, limited RBAC).

---

## Comparative Matrix

| Criteria | LiteLLM | Bifrost | Envoy AI GW | Membrane | LLM API Key Proxy |
|----------|---------|---------|-------------|----------|-------------------|
| **License** | MIT | Apache 2.0 | Apache 2.0 | Apache 2.0 | MIT |
| **Language** | Python | Go | C++/Go | Java | Python |
| **Docker Compose** | ✅ Native | ✅ | ❌ K8s only | ✅ | ✅ |
| **Anthropic native** | ✅ | ✅✅ (Claude-optimized) | Via filters | ✅ Plugin | ✅ |
| **Virtual keys** | ✅ | ✅ | Via policy | ✅ | ✅ |
| **Per-user budgets** | ✅ | ✅ | Custom | ✅ | ❌ |
| **Rate limiting** | ✅ Granular | ✅ Claude-aware | ✅ Envoy-native | ✅ Token-based | ✅ Basic |
| **Fallback routing** | ✅ 100+ providers | ✅ Multi-key | ✅ | Limited | ✅ |
| **Caching** | ✅ Redis/semantic | ❌ | ✅ | ❌ | ❌ |
| **Observability** | ✅ Built-in UI | ✅ Dashboard | ✅ Prometheus | Basic | ❌ |
| **Community size** | ⭐⭐⭐ 10K+ stars | ⭐⭐ Growing | ⭐⭐⭐ CNCF | ⭐⭐ 1.5K stars | ⭐ 523 stars |
| **ICDEV fit** | ⭐⭐⭐ **Best** | ⭐⭐ Good | ⭐⭐ Overkill | ⭐⭐ Okay | ⭐⭐ Simple |

---

## Prioritized Recommendations

### Priority 1 — Deploy LiteLLM Proxy (Immediate)

**Action:** Add LiteLLM Proxy as a Docker Compose service in ICDEV's existing `docker-compose.yml`.

**Why now:**
- Zero changes to ICDEV's LLM router architecture — just a `base_url` config change
- Students never see the real Anthropic key
- Per-student virtual keys with $5-10/month budgets prevent runaway spend
- ICDEV's existing PostgreSQL can be LiteLLM's metadata store (no new DB)
- Can be done in under 2 hours

**Security model:**
```
Student → ICDEV Academy UI → ICDEV LLM Router → LiteLLM Proxy (holds real key) → Anthropic API
                    ↑                                          ↑
               Virtual key                              Real ANTHROPIC_API_KEY
```

### Priority 2 — Enable Per-Student Key Rotation (Week 1)

Use LiteLLM's management API to generate virtual keys per student or per session:
```bash
curl -X POST http://litellm-proxy:4000/key/generate \
  -H "Authorization: Bearer $LITELLP_MASTER_KEY" \
  -d '{
    "budget": 10.0,
    "models": ["anthropic/claude-sonnet-4"],
    "rpm_limit": 60,
    "tpm_limit": 100000
  }'
```

### Priority 3 — Add Fallback to Bedrock (Week 2)

ICDEV already has `bedrock_provider.py`. Configure LiteLLM to fall back to AWS Bedrock Claude if Anthropic direct API hits rate limits:
```yaml
model_list:
  - model_name: claude-sonnet
    litellm_params:
      model: anthropic/claude-sonnet-4-20250514
    fallback_models:
      - bedrock/anthropic.claude-sonnet-4-20250514-v1:0
```

### Priority 4 — Monitor & Alert (Ongoing)

LiteLLM exposes Prometheus metrics. ICDEV's existing `prometheus_client` dependency can scrape:
- `litellm_spend` per virtual key
- `litellm_requests` per model
- `litellm_rate_limit_errors` for capacity planning

---

## Anthropic-Specific Considerations

| Anthropic Limit | How LiteLLM Helps |
|-----------------|-------------------|
| **Rate tiers** (Start: 10 RPM / Build: 50 RPM / Scale: 1000 RPM) | Queue + retry with exponential backoff. Distribute across multiple Anthropic orgs if needed. |
| **Token bucket algorithm** | LiteLLM's rate gate aligns with Anthropic's token bucket — no double-throttling. |
| **Spend limits** | Per-workspace in Anthropic console + per-virtual-key in LiteLLM = defense in depth. |
| **Model availability** | Fallback to Bedrock or OpenRouter if Anthropic model is unavailable. |

---

## Integration Touchpoints in ICDEV

| File | Change Required |
|------|----------------|
| `.env.example` | Add `LITELLM_MASTER_KEY`, `LITELLM_STUDENT_KEY` (virtual) |
| `args/llm_config.yaml` | Change `anthropic.base_url` to `http://litellm-proxy:4000` |
| `docker-compose.yml` | Add `litellm-proxy` service |
| `tools/llm/anthropic_provider.py` | None — `base_url` is already parameterized |
| `tools/llm/router.py` | None — provider resolution is config-driven |
| `k8s/` (future) | Add `litellm-deployment.yaml`, `litellm-service.yaml` |

---

## Summary

**The answer is yes** — share one Anthropic API key behind a proxy. For ICDEV's Python/Docker/K8s stack, **LiteLLM Proxy** is the clear winner: same language, Docker Compose native, virtual keys with budgets, Anthropic-first support, and a path to multi-provider fallback. It plugs into ICDEV's existing architecture with minimal changes (a `base_url` config update) and gives you per-student cost control, rate limiting, and audit trails that the raw Anthropic API cannot provide.

---

## Corrections established by the LPX build (2026-07)

This strategy was written before implementation. Two claims above proved
incomplete once the proxy was actually built (the LPX kanban card), and a fourth
product option was missing from the comparison. They are corrected here rather
than edited inline, so the original analysis and the as-built reality both stand.

### 1. "Just a `base_url` config change" was incomplete — six call sites bypassed the router

The Integration Touchpoints table claims `tools/llm/router.py` and
`tools/llm/anthropic_provider.py` need **no** change and that pointing
`anthropic.base_url` at LiteLLM is sufficient. That is true only for traffic that
actually flows through `LLMRouter`. It did not: several call sites constructed a
provider SDK client (or read a provider key env var) **directly**, bypassing the
router — so a pure `base_url` edit would have left those paths reaching the real
provider endpoint with the real key, defeating the abstraction.

The build therefore:
- Added `tools/llm/proxy_gateway.py`, which rewrites a cloud provider's
  `base_url` **and** swaps its `api_key_env` for a virtual key at provider
  construction (opt-in, off by default) — so the redirection is centralised, not
  a per-provider YAML edit.
- **Migrated six bypassing call sites** through `LLMRouter.invoke` in
  `lpx-router-01/02` (`tools/network/routes/ai.py`, `…/topology.py`,
  `…/twin_migration.py`).
- Added a **provider-bypass coherence gate** (`lpx-router-03`,
  `coherence_checker.check_provider_bypass`) that fails on any *new* provider-URL
  literal or provider-key env read outside `tools/llm/`, so the bypass class
  cannot silently regrow. A grandfathered baseline of remaining historical sites
  is tracked explicitly.

### 2. A fourth option was missing — extend the in-house `bedrock_proxy.py`

The comparison evaluated five external products (LiteLLM, Bifrost, Envoy AI GW,
Membrane, LLM API Key Proxy) but omitted the option of **extending ICDEV's
existing `tools/saas/bedrock/bedrock_proxy.py` into a general HTTP proxy**. That
in-house path avoids a new external dependency and keeps the egress surface fully
first-party — relevant in air-gap/CUI contexts where adding a third-party proxy
container is itself a compliance question. It was recorded as the fourth decision
option on the manual gate (`lpx-gate-00`). LiteLLM remained the chosen default,
but the in-house extension is the fallback for constrained deployments.

### 3. What the build actually shipped (scope corrections)

- **Opt-in, off by default.** The proxy never activates unless
  `ICDEV_LLM_PROXY_ENABLED` is set; with it unset, ICDEV runs fully air-gapped
  and byte-identical to before (`lpx-egress-01`, `lpx-vv-01`).
- **Virtual keys are scoped to ICDEV's existing tenancy** (tenant / team / guild
  / user), not an invented "student" unit — per-team for `/gameday`
  (join-by-code, no per-member rows) and per-guild/month for `/academy`.
- **CUI egress gate (`lpx-egress-02`).** The proxy may carry only unclassified
  traffic by default; CUI and above never silently traverse it — a fail-closed,
  invoke-time classification gate keeps them local. Raising the ceiling is an
  explicit ATO-boundary decision.
- **Observability + reconciliation.** Proxy spend/rate metrics surface on the
  Ops Hub LLMOps page (`lpx-obs-01`), and a reconciliation report/gate compares
  proxy accounting against `token_tracker` to catch silent divergence
  (`lpx-obs-02`).

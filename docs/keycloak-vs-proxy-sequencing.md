# Sequencing Decision: Keycloak vs Anthropic Proxy for ICDEV™ /gameday & /academy

## Executive Summary

**Deploy the Anthropic proxy FIRST.** The two systems are independent. The proxy gives immediate, high-value protection (key abstraction, per-student budgets, rate limiting) with a ~2-hour integration. Keycloak is a larger auth infrastructure project that can follow in parallel or after. They do not block each other.

**Exception:** If your primary goal is *per-student cost attribution tied to real identities* and you plan to use LiteLLM Enterprise's JWT-to-Virtual-Key mapping, then Keycloak should be in place first — but this is an enterprise feature path, not the open-source quick-start.

---

## Current State — ICDEV Authentication Architecture

ICDEV already has a multi-layer auth system. Keycloak is **not yet integrated** (zero references in the codebase).

| Layer | File | What It Does |
|-------|------|--------------|
| **Dashboard auth** | `tools/dashboard/auth.py` (841 lines) | Custom JWT + API key auth. Roles: `developer`, `admin`, `pm`, `isso`, `cor`, `co`, `ao`. Session-based with Flask `g.current_user`. |
| **OAuth/OIDC** | `tools/saas/auth/oauth_auth.py` (195 lines) | Generic OIDC client — fetches JWKS, validates JWTs, tenant-aware IdP resolution. **Ready for Keycloak** without code changes. |
| **SAML 2.0** | `tools/saas/auth/saml_auth.py` (1,064 lines) | DoD/CAC/PIV SAML SP — full AuthNRequest, assertion validation, EDIPI/CN/email attribute mapping. |
| **API key auth** | `tools/saas/auth/api_key_auth.py` (129 lines) | Tenant-scoped SHA-256 hashed API keys with expiry, status, scope checks. |
| **Config** | `args/auth_config.yaml` | JWT TTL (15 min access / 7 day refresh), CSRF entropy, API key entropy. |
| **Academy auth** | `apps/forge_academy/auth.py` (69 lines) | Dashboard `g.current_user` contract. `require_org_intel` gates Oracle + Org Readiness to `admin/pm/isso`. |
| **GameDay auth** | `apps/ai_gameday/auth.py` (117 lines) | Dashboard `g.current_user` contract. `login_required` + `require_facilitator` with IL dominance checks. |

**Key insight:** ICDEV's OAuth/OIDC module (`tools/saas/auth/oauth_auth.py`) is already IdP-agnostic. Pointing it at a Keycloak realm is a **configuration change**, not a code change. The heavy lifting (JWKS fetching, JWT validation, tenant resolution) is already done.

---

## What Each System Does (And What It Doesn't)

### Anthropic Proxy (LiteLLM)

| It DOES | It DOES NOT |
|---------|-------------|
| Hide the real Anthropic API key | Authenticate users |
| Issue virtual keys with budgets | Know student identities |
| Enforce RPM/TPM rate limits per key | Integrate with your dashboard roles |
| Log spend per key / team | Handle SAML/OIDC login flows |
| Cache responses | Manage user sessions |
| Route fallback to Bedrock/OpenRouter | Provision/deprovision student accounts |

### Keycloak

| It DOES | It DOES NOT |
|---------|-------------|
| Centralized identity + SSO | Manage LLM API keys or budgets |
| OIDC/SAML IdP for ICDEV dashboard | Route LLM traffic |
| User provisioning / deprovisioning | Cache LLM responses |
| Role/group mapping | Enforce token-level rate limits |
| Password policy, MFA, sessions | Hide Anthropic API keys |

---

## The Sequencing Question — Three Scenarios

### Scenario A: Open-Source Path (RECOMMENDED)

**Order: Proxy first, then Keycloak**

```
Week 1:  LiteLLM Proxy deployed. Virtual keys generated per cohort.
Week 2+: Keycloak deployed. ICDEV OIDC config pointed at Keycloak realm.
         Students now log in via Keycloak → dashboard → proxy virtual key.
```

**Why this works:**
- The proxy runs standalone. It needs a `master_key` (admin) and virtual keys (students). Neither requires Keycloak.
- Virtual keys can be generated via API call or LiteLLM UI. You don't need SSO to create `sk-student-01` with a $10 budget.
- ICDEV's existing dashboard auth (JWT + API keys) continues working. Students log into ICDEV however they do today.
- When Keycloak arrives later, you just change ICDEV's login flow. The proxy doesn't care — it still sees virtual keys.

**Trade-off:** Per-student spend attribution is by virtual key ID (e.g., `sk-academy-cohort-a`), not by real name/email. Good enough for budget control; not ideal for granular billing reports.

---

### Scenario B: Enterprise Path (With LiteLLM Enterprise)

**Order: Keycloak first, then Proxy with JWT-to-Virtual-Key mapping**

```
Week 1-2: Keycloak deployed, realm configured, ICDEV OIDC integration tested.
Week 3+: LiteLLM Enterprise deployed with OIDC JWT auth + SCIM provisioning.
          Student logs in via Keycloak → JWT token → LiteLLM maps to virtual key
          with per-user budget, rate limit, and model access.
```

**Why this is different:**
- LiteLLM Enterprise (not the open-source MIT version) supports **JWT-to-Virtual-Key mapping** and **SCIM provisioning**.
- With this, a student authenticates via Keycloak OIDC → LiteLLM verifies the JWT → maps the `sub` or `email` claim to a pre-created virtual key → enforces per-*user* budgets and rate limits.
- This gives true per-student attribution by identity, not just by key.

**Trade-off:** Requires LiteLLM Enterprise license. SCIM + OIDC auth are enterprise features. Higher operational complexity.

---

### Scenario C: Parallel Track (OPTIMAL if resources allow)

**Order: Both in parallel, proxy goes live first**

```
Team A (DevOps):  Deploy LiteLLM Proxy, configure Anthropic provider,
                  generate cohort virtual keys, set budget alerts.
Team B (Identity): Deploy Keycloak, configure realm, integrate ICDEV
                  OIDC client, test login/logout/role mapping.
Merge:            When Keycloak is ready, update ICDEV login redirect.
                  Proxy already running — no downtime.
```

**Why this is best:**
- Zero dependency between the two workstreams.
- Proxy delivers value immediately (students can't burn the Anthropic budget).
- Keycloak can be tested thoroughly without pressure.
- On merge day, it's a config change in ICDEV's login handler, not a new deployment.

---

## Why Keycloak First Is the Wrong Call (For Now)

| If you do Keycloak first... | What happens |
|-----------------------------|------------|
| Students still see the Anthropic key in `.env` or client-side config | Risk of key leakage, no budget control |
| No rate limiting on LLM calls | One student script can exhaust the org's Anthropic tier |
| No spend attribution | You won't know which cohort burned the budget |
| Keycloak gives you login — but nothing for the LLM layer | The Anthropic key remains exposed |

**Keycloak is about *who* can access ICDEV. The proxy is about *how much* LLM they can consume. These solve different problems.**

---

## Integration Points When Both Are Live

```
Student Browser
      ↓
[Keycloak Login] → OIDC token
      ↓
[ICDEV Dashboard] → validates JWT, sets g.current_user, resolves role
      ↓
[Academy / GameDay] → uses g.current_user.role for route guards
      ↓
[ICDEV LLM Router] → config says: use virtual key for this user/cohort
      ↓
[LiteLLM Proxy] → receives virtual key, looks up budget/rate limit,
                    injects real ANTHROPIC_API_KEY, forwards to Anthropic
      ↓
[Anthropic API] → sees only the proxy's IP and key
```

### Files to update when merging Keycloak + Proxy:

| File | Change |
|------|--------|
| `tools/saas/auth/oauth_auth.py` | Add Keycloak realm to `_jwks_cache`, update issuer whitelist |
| `args/auth_config.yaml` | Add `oidc_issuer_url`, `oidc_client_id`, `oidc_client_secret` |
| `.env.example` | Add `KEYCLOAK_BASE_URL`, `KEYCLOAK_REALM`, `KEYCLOAK_CLIENT_ID`, `KEYCLOAK_CLIENT_SECRET` |
| `tools/dashboard/auth.py` | Add OIDC login handler alongside existing session/API-key auth |
| `args/llm_config.yaml` | Point `anthropic.base_url` to LiteLLM proxy |
| `config/litellm_config.yaml` | Virtual key budgets per cohort/team |

---

## Recommended Sequencing

### Phase 1: Anthropic Proxy (This Week)
- Deploy LiteLLM Proxy in Docker Compose
- Move `ANTHROPIC_API_KEY` from `.env` (client-accessible) to proxy container env only
- Generate 3-5 virtual keys: `sk-academy-cohort-a`, `sk-gameday-facilitators`, `sk-admin`
- Set budgets: $10/cohort key, $50/admin key
- Update `args/llm_config.yaml` → point at proxy
- **Result:** Real Anthropic key is no longer exposed. Students use virtual keys.

### Phase 2: Keycloak Identity (Next Sprint)
- Deploy Keycloak in Docker Compose or K8s
- Configure realm, client, role mappers
- Update ICDEV's OIDC config to point at Keycloak
- Test login flow: Keycloak → ICDEV dashboard → Academy/GameDay
- **Result:** Centralized SSO, MFA, password policy for all ICDEV users.

### Phase 3: Optional — Tie Them Together (Future)
- If you upgrade to LiteLLM Enterprise: enable OIDC JWT auth + SCIM
- Map Keycloak `sub` or `email` claims to LiteLLM virtual keys
- Per-user budgets and spend tracking by real identity
- **Result:** Full identity-aware LLM governance.

---

## Summary

| Question | Answer |
|----------|--------|
| **Should Keycloak come first?** | **No.** The proxy is independent and delivers immediate value. |
| **Do they depend on each other?** | No. Proxy runs without Keycloak. Keycloak runs without the proxy. |
| **What order maximizes value fastest?** | Proxy first (2 hours → protected keys). Keycloak second (days → SSO). |
| **When would Keycloak need to come first?** | Only if using LiteLLM Enterprise JWT-to-Virtual-Key mapping for per-user attribution. |
| **Can they run in parallel?** | **Yes.** Assign to separate team members, merge when both are ready. |

**Bottom line:** Don't delay the Anthropic proxy waiting for Keycloak. The proxy protects your budget *today*. Keycloak improves login experience *next sprint*. Both can coexist — and should — but the proxy has zero prerequisites.

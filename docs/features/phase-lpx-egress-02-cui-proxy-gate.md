# LPX egress-02 — CUI egress gate on the shared LLM proxy

**Classification:** CUI // SP-CTI
**Status:** shipped
**Task:** `lpx-egress-02`

## Problem

The optional shared LLM proxy (LiteLLM, opt-in, off by default) transparently
forwards a *cloud* provider's traffic through a single hosted gateway. That makes
it a **new cloud egress path**. If classified/controlled content could reach it,
the proxy would silently widen the ATO boundary. The compliance stance is that
CUI (and above) must never traverse the shared cloud proxy — it stays local.

## Mechanism

The gate is a small, self-contained addition to `tools/llm/proxy_gateway.py`
plus one invoke-time hook in the router. It composes with, and does not replace,
the existing egress guarantees:

1. **Pure predicate — `proxy_gateway.proxy_egress_classification_block(provider_cfg, classification)`.**
   Returns a refusal reason (else empty string) when *all* of the following hold:
   - the proxy **would** carry this provider (`will_redirect`: proxy enabled or
     local-copy mode, **and** the provider is a redirectable cloud type —
     `anthropic`/`openai`/`gemini`/`azure_openai`). `ollama` (local) and
     `bedrock` (GovCloud/CUI) are never redirected, so they are never gated; and
   - the request's classification exceeds the proxy ceiling.

   Clearance ranking is **fail-closed**: an unknown or garbled label is treated
   as maximally sensitive and blocked (unlike the platform's fail-open default).
   Banner suffixes (`CUI // SP-CTI`, `SECRET//NOFORN`) are tolerated. An absent
   label defaults to CUI (matching `LLMRequest`'s default), so unlabelled traffic
   is treated as controlled.

2. **Configurable ceiling — `ICDEV_LLM_PROXY_MAX_CLASSIFICATION`.**
   Default `UNCLASSIFIED` (order 0): CUI and above are refused. An operator who
   has **accredited the proxy for a higher level** may raise it (e.g. `CUI`); an
   unrecognised value fails closed to the default.

3. **Invoke-time enforcement — `LLMRouter._enforce_routing_policy`.**
   The check runs on every provider call, reading the **live request's**
   classification (which is unavailable at cached-provider construction, so the
   gate cannot live there). A non-empty reason is raised as `ForceLocalViolation`
   — the same exception the existing egress guarantee uses — so the chain falls
   back to a local model exactly like any other egress refusal, at both the
   non-streaming and streaming chokepoints. A gate that fails is not swallowed
   (fail-closed).

4. **Redaction is not bypassed.** Redaction (`_pre_invoke_redaction`, honoring
   `redaction.fail_closed`) already runs in `invoke()` *before* provider
   resolution, so the proxy path inherits it — proxy redirection cannot skip
   egress redaction. This gate is an additional, orthogonal layer.

The ollama-vs-ollama_cloud `api_key_env` distinction (which credential is
presented) is honored transitively: the proxy only ever redirects the cloud
types and always swaps in a virtual key, never a real provider key.

## ATO boundary impact

- **Default posture (ceiling = UNCLASSIFIED): no boundary expansion.** With the
  proxy enabled, CUI+ traffic is refused the proxy path and stays on local /
  accredited GovCloud paths. The proxy carries only unclassified traffic.
- **Raising the ceiling is an explicit accreditation decision.** Setting
  `ICDEV_LLM_PROXY_MAX_CLASSIFICATION` to CUI (or higher) permits that level
  through the proxy and **does** extend the egress boundary to include the proxy
  and its upstream provider. That must be reflected in the SSP/authorization
  boundary and the provider's own authorization before use.
- **Air-gap / default-off deployments are unaffected** — the gate is a no-op when
  the proxy would not carry the call.

## Verification

`tests/test_lpx_egress_cui_gate.py` (13 tests): predicate behavior (CUI blocked,
PUBLIC allowed, unknown/garbled fail-closed, banner suffixes, configurable
ceiling, local/GovCloud carve-out, local-copy mode), and router invoke-time
enforcement (CUI over proxy raises `ForceLocalViolation`; PUBLIC permitted;
no-op when the proxy is off). Existing proxy/air-gap/local-copy suites remain
green (no regression).

# CUI // SP-CTI
# Cortex is Reached Over REST, Never Inherited (ctx-reach-04)

**Status:** documented (this change) · **Project:** `ctx` epic `reach` · **Date:** 2026-08-14

## The one-line rule

**Cortex is a parent-hosted governed service reached over REST with an `icdev_ctx_`
service key. It is NOT a library, and it is NOT copied into child apps, canvases, or
any descendant of this repo.** A consumer that wants Cortex vendors the ~500-line
stdlib-only client (`tools/cortex/client.py`) and points it at an ICDEV host. It does
not vendor Cortex.

This was always true in the code. It was nowhere stated, which is why the question
keeps coming back.

## Measured, not asserted

`tools/builder/child_app_generator.py` decides what a generated child app inherits.
The string `cortex` appears in that file **zero times** (measured 2026-08-14):

```bash
grep -ic cortex tools/builder/child_app_generator.py     # -> 0
```

That single fact settles it, because of how the generator is shaped:

| Structure | File | Semantics |
|---|---|---|
| `DIRECTORY_TREE` | `child_app_generator.py:241` | **ALLOWLIST** of always-on dirs copied into every child. |
| `CONDITIONAL_DIRS` | `child_app_generator.py:301` | Extra dirs, keyed by enabled capability. |
| `TRUST_REFRESH_DIRS` | `child_app_generator.py:293` | Dirs re-copied into already-materialized children on upgrade. |
| `PARENT_ONLY_DIRS` | `child_app_generator.py:184` | Denylist — a belt-and-braces exclusion for dirs that would otherwise match. |

`tools/cortex` is in none of them. Because `DIRECTORY_TREE` is an **allowlist**, absence
is sufficient: `tools/cortex` does not need to appear in `PARENT_ONLY_DIRS` to be
excluded, and adding it there would be misleading — it would imply some other path was
trying to pull it in. Nothing is.

Corollary: `tools/cortex` is also not in `TRUST_REFRESH_DIRS`, so there is no upgrade
path that would retroactively push Cortex into an existing child app either.

The generator source is deliberately left with **no comment mentioning Cortex**, so the
grep above stays a valid measurement rather than a self-hit. The pointer for a reader
sitting in that file lives in the generator's own doc,
[phase-19-agentic-generation.md](phase-19-agentic-generation.md) §4.3.

## Why this is the correct architecture, not an oversight

Cortex is not a helper module that happens to live under `tools/`. It is the governed
seam: every `/cortex/api/v1` call runs the TRUST chain (gateway injection screen,
redaction, grounding, provenance, append-only audit) and resolves identity server-side.

Copying it into a descendant would copy the *shape* of that governance while leaving
behind the things that make it real — the audit tables, the tenant/classification RLS
predicates, the LLM routing config, the retrieval backends, and the operator who
revokes a key. You would get a child app that looks governed and is not. The correct
unit of distribution is a **credential**, not a code copy: a service key can be scoped,
tenant-bound, classification-ceilinged and revoked in one place. A vendored copy of
`tools/cortex` can be none of those things.

The same reasoning is why `agent` mode over REST runs with no tools, and why
`cortex:agent` is never in the default grant — see
[cortex-service-exposure.md](cortex-service-exposure.md).

## The access pattern

### 1. Issue a service key (on the ICDEV host)

```bash
python -m tools.cortex.service_keys create --label myapp --tenant myapp --json
python -m tools.cortex.service_keys create --label myapp --tenant myapp \
    --scopes cortex:search,cortex:ask,cortex:complete --ceiling CUI --json
python -m tools.cortex.service_keys list --json
python -m tools.cortex.service_keys grant --key-id <id> --scopes cortex:reason --json
python -m tools.cortex.service_keys revoke --key-id <id> --json
```

Keys are `icdev_ctx_`-prefixed, SHA-256 at rest, shown in plaintext exactly once, and
revocable. The **key row is authoritative** for identity — `tenant_id` in a request body
is ignored, `classification` is clamped to the key's ceiling, `air_gap` / `fail_closed`
may only be raised by the caller, and `trusted_content` is force-cleared for network
callers. Full contract: `tools/cortex/service_keys.py` module docstring.

Scope defaults matter: `DEFAULT_SCOPES` is `CORTEX_SCOPES` only (the seven read/answer
operations). `cortex:agent`, `cortex:bom`, `cortex:cost_volume`, `cortex:award`,
`cortex:dashboard`, `cortex:win_themes`, `cortex:staffing_matrix`, `cortex:intake` and
the `databridge:*` scopes are granted explicitly or not at all.

### 2. Point a client at the host

```bash
ICDEV_CORTEX_BASE_URL=http://<icdev-host>:5050
ICDEV_CORTEX_API_KEY=icdev_ctx_…          # or an app-specific var, see below
```

```python
from tools.cortex.client import CortexClient   # in-repo
# from tools.integrations.cortex_client import CortexClient   # vendored copy

client = CortexClient(base_url=..., api_key=..., api_key_env="MYAPP_CORTEX_API_KEY")
result = client.ask("How many tasks are blocked?")
```

### 3. The surface

Authenticated with `Authorization: Bearer icdev_ctx_…`:

```
POST {host}/cortex/api/v1/{search,ask,complete,reason,classify,extract,govern}
POST {host}/cortex/api/v1/agent                       (scope cortex:agent)
POST {host}/cortex/api/v1/intake/{session,turn}       (RICOAS intake bridge)
GET  {host}/cortex/api/v1/intake/session/<id>
GET  {host}/cortex/api/v1/health                      (UNAUTHENTICATED — status only)
GET/POST {host}/api/databridge/v1/<connector>/<table> (allowlisted connectors)
```

The canvas at `/cortex` and this machine surface share **one** Flask blueprint —
`register_rest_v1(cortex_bp)` in `tools/cortex/rest_v1.py` attaches the v1 endpoints to
the canvas blueprint. One prefix, one auth path, no second registration.

`domain` is the only caller-supplied context field, and it can only **narrow** access,
never widen it.

## The degradation contract

This is the part callers get wrong, so it is stated in full. Every `CortexClient`
method returns `Optional[dict]` and **NEVER raises**:

| Situation | Return | What the caller should do |
|---|---|---|
| 2xx | parsed JSON dict | Use it. |
| 4xx with a JSON body (400 validation, 401/403 auth/scope/**governance-blocked**, 422 analyst-unanswerable) | that body, with `http_status` set | **Surface it.** Cortex answered; the answer was a refusal. |
| Disabled config, empty `base_url`, missing key, connection refused, DNS failure, timeout, 5xx, malformed JSON | `None` | **Degrade silently.** Cortex is unreachable. |

The distinction is the whole point: `{"blocked": True, "governance": {...}}` is an
**answer** and hiding it defeats the governance it reports, while `None` is an
**outage** and surfacing it as an error trains users to ignore the feature. A blocked
response arrives as a 4xx *body*, not as `None`, precisely so the two can never be
confused.

The same asymmetry shows up inside successful responses. `client.agent(...)` returns
200 with `{"launched": False, "degraded": True, "reason": ...}` when the provider cannot
serve native tool-use — a capability answer, not a fault, and deliberately not `None`.
**Read `launched` first.** `client.bom(...)` has the same shape with `is_a_total`, and
`client.price_cost_volume(...)` with `status: "unpriced" | "partial"`.

Any AI feature built on this client must have a defined behaviour for all three rows of
that table before it ships.

## Why the client is stdlib-only

`tools/cortex/client.py` imports `json`, `os`, `typing`, and `urllib` — nothing else,
and specifically **zero ICDEV imports**. That is a contract, not an accident.

The consumers are standalone apps in **separate git repositories** (compass, idea_lab).
They copy this one file verbatim into `tools/integrations/cortex_client.py` with a
provenance header. If the client imported anything first-party — a logger, a config
loader, a `storage` helper — vendoring it would drag in a dependency subtree, and the
copies would drift into needing the platform. At which point the copy stops being a
client and starts being a partial fork of ICDEV, which is the exact failure this whole
pattern exists to prevent. The stdlib-only rule is what keeps "reached over REST" from
quietly decaying back into "inherited".

The drift it guards against is **latent**: a method added here is simply absent from the
vendored copies, and because no consumer calls it yet, nothing breaks — until someone
tries. Measured 2026-08-02: canonical had 24 public methods, compass's copy 22,
idea_lab's 19, and the gap had gone unnoticed for weeks.

Enforcement:

```bash
python tools/workflow/coherence_checker.py --check vendor_parity --gate
```

Targets are declared in `args/vendor_parity.yaml`. The check compares the **public API**
(classes, functions, method names + parameter names), not bytes — a vendored copy
legitimately differs by its provenance header and line endings. Canonical's public API
must be a subset of each copy's; a copy temporarily *ahead* is fine. A consumer path
that does not resolve (standalone repo not checked out) makes the check SKIP, never
fail, so CI stays green on a runner that has only this repo.

Adding a public method to `client.py` without re-vendoring is what this check catches.
Do that in the same change, or expect the gate.

`tools/cortex/client.py` and `icdev/tools/cortex/client.py` are byte-identical mirrors;
edit both (or run companion sync) — that is the in-repo `mirror_parity` rule, separate
from `vendor_parity`.

## Should any in-repo app consume Cortex? — the decision

**No. Not today, and not by default.** Recorded here so the answer stops being implicit.

State of play, measured 2026-08-14: **zero in-repo `apps/` consume Cortex.** The two
apparent references are not calls:

- `apps/forge_academy/content/tier2/m-cortex-01-unified-ai-layer/` — **curriculum
  content**. It teaches Cortex; it does not invoke it.
- `apps/ai_gameday/constants.py:91-95` — **catalog metadata**. Four rows
  (`cortex.ask/extract/classify/govern`) carrying an `endpoint` and an `mcp_tool` name
  for display. Nothing dispatches from them.

Repo-wide, the only importers of `CortexClient` are `tests/cortex/*`. Every real
consumer is out-of-repo, over the network.

The reasoning:

1. **In-repo code that wants Cortex should call it in-process, not over HTTP.** A module
   inside this repo can `from tools.cortex.api import ask` and get the governed facade
   directly, with the session's own `CortexContext`. Routing that through localhost REST
   would add a hop, a key to rotate, and a second identity path, and would buy nothing —
   the process already has the governance.
2. **`apps/` are demo and content surfaces.** `forge_academy` teaching about Cortex is
   the correct relationship. `ai_gameday` listing it in a catalog is too. Wiring either
   to live Cortex calls would add an operational dependency (a key, a reachable host, a
   degradation path in the UI) to something whose job is to demonstrate.
3. **The REST surface exists for the network boundary**, and the population that needs
   it — separate repos, separate deploy units, separate operators — is entirely
   out-of-repo today.

This is a decision, not a prohibition. An in-repo app **should** consume Cortex over
REST when it genuinely crosses a process boundary: it is deployed separately from the
ICDEV host, or it is a generated child app running elsewhere. That is exactly the
external-consumer case, and it takes the same three steps as compass. If you reach for
that, note it here and add the target to `args/vendor_parity.yaml` if you vendor the
client rather than importing it.

What is **never** correct is copying `tools/cortex` into the app.

## Related

| Doc | Covers |
|---|---|
| [cortex-service-exposure.md](cortex-service-exposure.md) | The service-key/REST exposure itself: auth branch, scope gate, DataBridge feeds, leak guard. |
| [cortex-unified-ai-layer.md](cortex-unified-ai-layer.md) | What Cortex is and what the seven operations do. |
| [cortex-rls-audit.md](cortex-rls-audit.md) | Tenant/classification enforcement behind the surface. |
| [phase-hgx-cx-01-cortex-graph-mode.md](phase-hgx-cx-01-cortex-graph-mode.md) | `agent` mode `graph` — Studio DAG runs over REST. |
| [phase-19-agentic-generation.md](phase-19-agentic-generation.md) | The child app generator: what descendants DO inherit. |

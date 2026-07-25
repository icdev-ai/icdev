# idea_lab ← ICDEV Divergence integration hand-off (dvg-wire-03)

**Status:** disposition / hand-off. The ICDEV side is complete and reusable; the
one code change this describes lands in the **external** repo
`C:\AI\standalone\idea_lab`, which the ICDEV kanban runner cannot build. This
document is the hand-off so that change can be made in idea_lab by its owner. It
is intentionally NOT an ICDEV code change.

## Why a hand-off and not a PR here
`dvg-wire-03` asks idea_lab to gain an opt-in divergence step. idea_lab is a
separate repo with no local/air-gapped LLM path (cloud-only). The correct
architecture reuses ICDEV's divergence over reimplementing the loop in idea_lab,
exactly as idea_lab already reuses ICDEV's Council. So the substance is: confirm
the ICDEV primitive is reusable (done, below) and describe the idea_lab wiring.

## What idea_lab has today
A single funnel with no branch point:

```
intake Q&A → scoring → spec (tools/build/spec_generator.py) → build
```

One user-supplied idea in, one spec out.

## The proven cross-repo pattern to reuse
idea_lab and ICDEV already talk both directions:

- **idea_lab → ICDEV**: idea_lab calls ICDEV's `council_query` MCP tool to
  pressure-test a validated idea.
- **ICDEV → idea_lab**: `tools/govcon/specialist_consult.py::request_council_consult`
  POSTs to idea_lab's `/api/specialist/consult/council`, **fail-closed** through
  `tools.redaction.govcon_sanitizer.GovConSanitizer` before anything leaves the
  process (idea_lab is cloud-only; `_redact` returns `None` and abandons the
  consult rather than sending raw text if the sanitizer cannot run).

Divergence adds a NEW ICDEV MCP tool in the **same direction as `council_query`**.

## ICDEV side — confirmed reusable (already merged)
- `ChainOrchestrator.invoke_divergence(function, request)` — the isolated
  generative fan-out that returns a raw idea pool. `dvg-core-01/02`.
- `tools/quality/divergence_critic.py::score_idea_pool(...)` — the separate
  critic: novelty/viability/fit scores (categorical → Python-composed) + advisory
  trap flags. `dvg-critic-01/02`.
- **`divergence_invoke` MCP tool** (`tools/mcp/gap_handlers.py::handle_divergence_invoke`,
  registered in `tool_registry.py`) — `dvg-wire-04`. Params: `function`, `prompt`,
  optional `system_prompt`, optional `score` (bool → also returns scored ideas +
  `trap_warnings`). Returns `{content, chain_mode, models_used, total_cost_usd,
  trace_id, stop_reason, rounds, scored?, trap_warnings?}` or `{error}`; never
  raises. This is the surface idea_lab calls — identical shape/behavior contract
  to `council_query`.

No new capability is required in ICDEV for idea_lab to integrate.

## idea_lab side — the change to make (in `C:\AI\standalone\idea_lab`)
Add an **opt-in** divergence step **after intake, before spec generation**:

```
intake Q&A → [opt-in divergence] → scoring → spec → build
```

1. **Call ICDEV, don't reimplement.** From the point where idea_lab today has a
   single validated idea, call ICDEV's `divergence_invoke` MCP tool with
   `score: true`, using the same MCP client wiring already used for
   `council_query`. Feed the intake problem statement as `prompt`.
2. **Feed survivors forward.** Use the returned scored pool (ordered by
   composite) to offer the user alternative directions before `spec_generator.py`
   locks in one contract. Surface `trap_warnings` as **advisory** notes at that
   decision point — never an auto-block (matches ICDEV's own posture until
   `dvg-bench-01` measures trap detection).
3. **Keep it opt-in.** Divergence is many model calls / several times the spend
   of a direct answer. Gate it behind an explicit user/flag choice; never make it
   the default path in the funnel.

## CUI / boundary constraint (do not open a new hole)
idea_lab is CLOUD-ONLY. Any idea text that idea_lab sends across the network
boundary must pass through the **same fail-closed sanitizer** idea_lab already
uses for its cloud LLM calls (mirroring `specialist_consult.py::_redact`, which
FAILS CLOSED — abandons the call rather than sending raw text if redaction
cannot run). A divergence call must not become the one path that ships
unsanitized text. Prefer routing to an ICDEV `function` whose ICDEV-side config
is LOCAL-ONLY so the branches stay on local models — divergence inherits that
function's routing and opens no new egress path on the ICDEV side.

## Acceptance (for whoever makes the idea_lab change)
- Divergence step is reachable only when explicitly opted in.
- It calls ICDEV `divergence_invoke` (no divergence loop reimplemented in idea_lab).
- Cross-boundary text is sanitized fail-closed.
- Trap warnings surface as advisory input to the pre-spec decision, not a blocker.

# Reasoned Codegen — CoT/CoD + Adversary Review for Code Generation

> **Classification:** CUI // SP-CTI

## Summary

Wires ICDEV's existing multi-LLM reasoning (`chain_orchestrator` — CoT/CoD) and
adversarial critique (`anvil_critique`) into the code-generation pipelines via one
shared, opt-in, cost-bounded **Generate → Critique → Verify → Repair** wrapper.
No new third-party dependencies; every LLM call routes through `LLMRouter`
(LLM-agnostic, air-gap / no-LLM safe, Apache-2.0 clean).

The `github.com/ai-hero-dev/ai-hero` repo was evaluated for adaptation but is
licensed **CC-BY-NC-SA 4.0** (non-commercial) — incompatible with Apache 2.0, so
**no code was ported**; only generic, independently-known patterns informed the design.

## Components

| Component | Path | Role |
|-----------|------|------|
| Wrapper | `tools/llm/reasoned_codegen.py` | `generate_reasoned_code(...)` — Generate→Critique→Verify→Repair loop with a pluggable verifier. Byte-identical passthrough when disabled. |
| Advisor | `tools/llm/reasoned_codegen_advisor.py` | `recommend(...)` — AI-assisted decision on whether reasoned codegen pays off (hybrid heuristic + optional LLM). |
| Config | `args/llm_config.yaml` → `reasoned_codegen` | Global defaults + per_function (all generation OFF except `code_translation`). Section `enabled:false` = absolute kill-switch. |

## Control flow

1. **Generate** — CoT (`invoke_chain_of_thought`), CoD (`invoke_chain_of_debate`), or plain `invoke`, by config/override.
2. **Critique** (optional) — `anvil_critique` domain critics; `nogo` consensus short-circuits (`stop_reason=veto`).
3. **Verify** — injected verifier (`VerificationResult`); PASS when none supplied.
4. **Repair** — feed verify + critique findings into a fresh call (`<fn>_repair` route if present), up to `max_repair_rounds`; outer `cost_cap_usd`/`token_cap` aborts with `stop_reason=budget`.

## Wired pipelines

| Pipeline | Where | Default | Notes |
|----------|-------|---------|-------|
| Translation | `tools/translation/code_translator.py:_invoke_llm` | **ON** (`mode:cot`) | Per-unit CoT generation; project-level verify+repair stays in Phase 5 (`translation_validator`). |
| ANVIL agentic codegen | `tools/anvil/agentic_runner.py` (`--reasoned auto\|on\|off`) | **OFF** | `auto` consults the advisor; per-turn reasoning only (loop self-validates with ruff/pytest). |

## Bypass (no LLM generation call — verified)

`child_app_generator.py` (scaffold copier), `code_generator.py` (deprecated),
`migration_code_generator.py` (template), AAC scoring (deterministic) — all have
zero `router.invoke` generation calls. Documented in
`docs/security/sandbox-coverage.md` (Gap 15 + bypass table).

## Enable / disable

```bash
# Per-task option on the agentic runner
python tools/anvil/agentic_runner.py --task-id X --task-desc "..." --reasoned auto

# Ask the advisor directly
python tools/llm/reasoned_codegen_advisor.py --function code_generation \
    --spec "add OAuth2 with encrypted tokens and NIST audit" --file-count 5 --json

# Flip a pipeline default
#   args/llm_config.yaml -> reasoned_codegen.per_function.<fn>.enabled: true
# Emergency kill-switch (everywhere):
#   args/llm_config.yaml -> reasoned_codegen.enabled: false
```

## Tests

- `tests/tools/llm/reasoned_codegen_test.py` — passthrough identity, CoT, repair, budget, veto (13).
- `tests/tools/llm/reasoned_codegen_advisor_test.py` — heuristics, no-LLM fallback, LLM refine (8).
- `tests/tools/anvil/agentic_runner_reasoned_test.py` — option/advisor resolution + wrapper routing (6).
- `tests/test_translation_manager.py::TestReasonedCodegenWiring` — translation routing (2).
- `tests/security/test_reasoned_codegen_no_exec.py` — Gap 15 no-exec guardrail (2).

## Sandbox decision

**bypass-documented** (Gap 15) — the wrapper never executes the code it generates;
downstream executors carry their own decisions.

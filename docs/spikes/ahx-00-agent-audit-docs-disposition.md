# CUI // SP-CTI

# AHX-00 — Disposition of the five agent-harness audit documents

**Date:** 2026-07-27
**Sources evaluated** (all in `C:\AI\searches`, excluding `archive/`):

| Doc | Provenance |
|---|---|
| `icdev_agent_harness_audit.md` | LLM audit of the Genesis / ACE / SAG harness stack |
| `icdev_optimization_audit_self_healing.md` | LLM audit; "why ICDEV cannot recover from missing dependencies" |
| `icdev_loop_agent_youtube_adaptation.md` | Transcript analysis — Kyle (Human Layer), "Building Control Loops for the Real World" |
| `icdev_uber_agent_orchestration_adaptation.md` | Transcript analysis — Jay & Sonya (Uber), "Real-World Production Agent Design at Uber Eats" |
| `icdev_phase_4_6_strategic_roadmap.md` | Synthesis of the four above plus "cross-industry best practice" |

Two further files in that directory (`academy_mission_audit.md`, `academy_test_report.md`) are
**out of scope** — the work they describe is unfinished and was excluded by the requester.

**Method:** every claim below was checked against the tree at `C:\ai\icdev` on 2026-07-27 (file:line
where cited). Board and harness state were queried against the **configured PostgreSQL backend**, not
a SQLite fallback.

**Prior art this document defers to (do not re-litigate):** **ADR D391** (disposition of all 35
upstream agentic architectures — 22 already-covered, 7 adopted, 6 rejected), **ADR D384–D390**
(Phase 76 SAG, governing rule: *compose existing primitives, don't rebuild*), **ADR D165** (3-tier
self-healing engine), **D254** (pass@k), **D280/D282** (pluggable tracer, content-tracing opt-in),
the `sag` project card, and `docs/spikes/agx-00-agentic-architectures-adaptation.md`.

---

## 1. Headline verdict

**About one third of the recommended surface is real. The rest is already shipped, already solved
differently, or in direct conflict with the platform's objective.**

This is the second time an external analysis of this repository has produced confident gap claims
that did not survive contact with the code. The `sag` card exists partly to correct the first one,
which called `tools/gateway/` a stub after reading a stale `icdev/` mirror. ADR D391 was then written
explicitly *"so no future session re-analyzes this repo or re-proposes a rejected pattern."* This
document serves the same purpose for these five.

| Doc | Verdict |
|---|---|
| `icdev_agent_harness_audit.md` | **Partly adopt** — 3 of 5 findings real; headline evidence stale but points at a real, sharper defect |
| `icdev_optimization_audit_self_healing.md` | **Adopt the diagnosis, reject the cure** — taxonomy yes, runtime `pip install` refused |
| `icdev_loop_agent_youtube_adaptation.md` | **Partly adopt** — feedback file and backpressure real; the sensor it asks for already exists |
| `icdev_uber_agent_orchestration_adaptation.md` | **Mostly reject** — both P0s already ship |
| `icdev_phase_4_6_strategic_roadmap.md` | **Reject** — ~70–80% re-derives shipped capability |

Carded as a result: **`ahx-`** (built immediately), **`arr-`** and **`clx-`** (registered, gates held).
Nothing else from these documents has a board surface, by design.

---

## 2. Claims verified TRUE — carded

| Claim | Evidence | Card |
|---|---|---|
| `record_outcome` silently succeeds on a zero-row UPDATE | `tools/genesis/harness/eval_harness.py:320` — bare `UPDATE harness_eval … WHERE task_id=%s AND actual_outcome IS NULL`; no `rowcount` inspection, no warning, no audit. Callers cannot distinguish "recorded" from "matched nothing". | `ahx-eval-01` |
| The harness outcome loop is open | Live PG: 129 rows — `codegen` 65 (60 unresolved), `sampled:oracle_triage` 63 (63 unresolved), `oracle_triage` 1. **123/129 have `actual_outcome IS NULL`.** `compute_metrics` (`eval_harness.py:381-394`) counts only `resolved`/`false_positive`/`self_resolved`/`failed`, so precision, recall and ECE derive from ~6 rows, and the adaptive Z-score thresholds built on them are statistically void. | `ahx-eval-02` |
| `harness_eval` has no numbered migration | Schema exists only in `tools/db/schema/pg_consolidated.sql:16411` and `tests/conftest.py:284`. Nothing under `tools/db/migrations/`. | `ahx-eval-03` |
| The capability catalogue advertises tools that do not exist | `context/capabilities/harness.yaml:13,26-27,40-41` advertises five CLI invocations across `maturity_assessor.py`, `trace_analyzer.py`, `exit_criteria_evaluator.py`. Repo-wide grep for all three names: **zero hits**. `tools/harness/` holds only `cli_generator.py` and `mcp_wrapper_generator.py`. Violates the CLAUDE.md guardrail against documenting non-existent commands. | `ahx-doc-01` |
| Per-machine hardcoded memory path | `tools/memory/wiki_tool_query.py:81`, `tools/ace/controller.py:261,318` build `$USERPROFILE/.claude/projects/C--AI-ICDev/memory`. `tools/memory/memory_write.py:225-233` already derives the same slug dynamically from `BASE_DIR` — the correct pattern exists and is simply not reused. | `ahx-path-01` |
| Three divergent self-heal rate limits | `self_heal_analyzer.py:24-25` (0.7/0.3, 3/hr) vs `mcp/knowledge_server.py:266-304` fallback (5/hr) vs `args/heal_constitution.yaml` (`max_per_day: 3`). CLAUDE.md documents 5/hr, matching only the fallback. | `ahx-heal-01` |
| No agent-runtime error taxonomy | `tools/agent_runtime/` (18 modules) has no `error_recovery.py`; no `install_dependency`, `environment_probe`, `safe_packages.yaml` or `SelfHealingToolRunner` anywhere. `agent_loop.py:981-988` collapses every exception to `f"{type(exc).__name__}: {exc}"`. | `arr-tax-01` |
| No control-loop artifacts | No `.icdev/`, no versioned feedback file, no golden-pattern directory; repo-wide grep for `golden_pattern`, `golden_dataset`, `feedback.md` returns nothing. No `control_loop.py`, `Sensor`, `Controller`, `SetPoint`. | `clx-fb-01`, `clx-flow-01`, `clx-gold-01` |

---

## 3. Claims verified FALSE — not carded

| Doc claim | Reality |
|---|---|
| **Uber P0:** "no pass criteria per turn; only `done` vs `truncated`" | `icdev/tools/llm/agent_loop.py:1650` `run_agent_loop_with_rubric(rubric, grader, max_grading_iterations, grader_llm_function, on_grade, harness_task_id)` plus `RubricGrade`, `RubricVerdict`, `RubricLoopResult` and a `grading_attempts` counter. **Pass@K with grader feedback already ships and is already harness-wired.** |
| **Uber P0:** "structured logging is the missing foundation for everything else" | Already present: `agent_loop_checkpoints` (migration 227, written every turn with full `messages_json`, tokens, cost, `parent_session_id`), `agent_evals` (migration 223, `total_tool_calls`/`error_tool_calls`/`tool_error_rate`/`tool_precision`), `AgentLoopResult.trace_id` + OTel (migration 229), pluggable `Tracer` ABC (D280) with content tracing opt-in (D282), and `tools/observability/` (otel/sqlite tracers, `genai_attributes`, `shap/`, `provenance/`). What is genuinely absent is a per-tool-call event table — a much smaller claim than "no logging". |
| **Harness audit:** "`harness_eval` starved — 13 rows, all `oracle_triage`, zero codegen" | Stale; sourced from a 2026-07-17 log. Live: 129 rows including 65 codegen. Decisions are being written correctly. The defect moved to the outcome side (§2). |
| **Harness audit:** implies the agent loop is unguarded | It has a consecutive-all-error circuit breaker, a duplicate-call guard, a stall detector, per-tool timeouts, LLM-call timeouts and hard token/cost budget caps (`agent_loop.py:1023-1189`). Only the *taxonomy* is missing. |
| **Self-healing doc's flagship scenario:** "parse this PDF" fails because `pymupdf` is missing and the agent stalls | `tools/airgap/pdf_fallback.py` already solves this the correct way for this platform: a declared local fallback chain (`pypdf`, then LLaVA via Ollama for scanned pages) that registers itself into the RAG provider chain. Capability degradation, not environment mutation. |
| **Control-loop doc:** "needs a new `code_sensor` deterministic scanner" | `tools/quality/review_loop.py` already is one — it runs `ruff`, `coherence_checker.py` and SIPA (`tools/integrity/pr_gates.assess_changed_files`) over a diff, autofixes what it can and emits a fix brief, explicitly documented as deterministic execution with LLM orchestration outside. Also `tools/code_intelligence/codelens.py` and `tools/mosa/mosa_code_enforcer.py`. |
| **Uber/roadmap:** "needs a Diagnoser abstraction" | Approximately `tools/workflow/self_debug.py` (recurrence-triggered LLM RCA + quarantine) + `tools/genesis/reflexes/heal.py` (pattern DB, adaptive confidence threshold, MAC-style constitution) + `tools/monitor/auto_resolver.py` (3-tier engine, ADR D165). Consolidation may have merit; a new parallel abstraction does not. |
| **Roadmap** layers 1–3, 5–10: simulation, economics, causal, fairness, red-team, explainability, cross-project learning, regulatory | Already shipped. Explainability/XAI: `tools/observability/shap/`, `provenance/`, `/traces`, `/provenance`, `/xai` (Phase 46). Model cards & transparency: `tools/compliance/model_card_generator.py`, MCP `model_card_generate`/`system_card_generate`/`fairness_assess` (Phase 48–49). Red-team: MCP `run_atlas_red_team`, `owasp_asi_assess` (Phase 45/53). Multi-tenancy & throttling: Phase 21 + `ThrottleController`. Economics: `args/llm_config.yaml` `two_tier` + `scanner_functions` already routes cheap-tier vs planner-tier per function; MCP `cost_intelligence_*`. Simulation: `tools/simulation/` (`monte_carlo`, `coa_generator`, `risk_monitor`, `fault_localizer`). Cross-project learning: KG + RAG + GraphRAG + the Genesis knowledge-bridge promoter. |

---

## 4. Rejected on platform grounds — will not be built

### `install_dependency` — runtime `pip install` inside the agent loop

The self-healing document's central recommendation is a tool that lets the agent install Python
packages on demand, gated by an `args/safe_packages.yaml` allowlist. **Refused.**

ICDEV targets air-gapped IL4–IL6. `args/security_gates.yaml` enforces `sbom_max_age_days: 30`,
`min_slsa_level: 2`, and blocks on `sbom_attestation_missing` and `slsa_provenance_missing`.
`tools/airgap/wheel_vendor.py` exists precisely so packages arrive as vetted, vendored wheels. An
agent that mutates its own runtime from PyPI invalidates the SBOM, breaks provenance attestation, and
defeats the air-gap story — to solve a problem the platform already solves better.

**Adopted instead — declare-and-degrade** (`arr-deg-01`): classify the error, name the missing
capability as a structured actionable result, route it to wheel vendoring plus HITL. Never mutate the
environment. The canonical pattern is `tools/airgap/pdf_fallback.py`.

Note the doc's own maturity ladder places "installs its own dependencies" at Level 3 and calls ICDEV
Level 1–2. That ladder is a poor fit for a platform whose value proposition includes a fixed,
attested dependency surface. Declining to climb it is a deliberate posture, not a capability gap.

### LangGraph / LangChain-style orchestration

Re-proposed implicitly by the Uber pipeline recommendation. Formally rejected in **ADR D391**
alongside Cellular Automata, LATS, Voyager-subprocess execution, Mental Loop and Blackboard. Do not
reopen without new evidence about *this* codebase.

---

## 5. What was carded

| Card | Prefix | Status | Scope |
|---|---|---|---|
| AHX — Agent Harness Truth & Measurement | `ahx-` | **Built** | The seven verified findings in §2 that concern measurement |
| ARR — Agent Runtime Resilience (Air-Gap Safe) | `arr-` | Gate held | Error taxonomy, structured tool results, single safe retry, declare-and-degrade, escalation |
| CLX — Control Loop Discipline | `clx-` | Gate held | Versioned feedback file, backpressure, sensor interface over `review_loop.py`, golden patterns |

`arr-` and `clx-` are gated behind `ahx-` deliberately: neither can be evaluated until the harness
can measure outcomes. Their briefs name the existing `sag` card and ADR D384/D391 as mandatory
reading, because both overlap work that is already partly built.

---

## 6. Standing lesson

All five documents share one failure mode: they infer absence from a name-based search. Every false
claim in §3 is a capability that exists under a different name than the author expected —
`run_agent_loop_with_rubric` rather than "pass@K", `review_loop.py` rather than "code_sensor",
`agent_loop_checkpoints` rather than "telemetry.py", `pdf_fallback.py` rather than
"install_dependency".

The corollary for future analyses of this repository: **search by behaviour, not by identifier**, and
check `NOTICE`, `docs/reference/adrs.md` and `docs/spikes/` before writing the word "missing".

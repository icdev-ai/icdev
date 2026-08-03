# CUI // SP-CTI

# DVG-00 — ADHD (parallel divergent ideation) adaptation analysis

**Source:** https://github.com/uditakhourii/adhd (MIT, TypeScript/npm, `adhd-agent`)
**Date:** 2026-07-25
**Verdict:** ADOPT the *method*, REJECT the *packaging*. ~70% of the orchestration
already exists in `tools/llm/chain_orchestrator.py::invoke_council`. The genuinely
new material is a generative frame library, an explicit trap-detection scoring
dimension, and cluster/deepen — plus wiring ideation into three engines that
currently have none.

---

## 1. What the upstream project actually is

A two-phase loop that fights premature convergence in autoregressive reasoning:

1. **Diverge** — N parallel, *fully isolated* agent calls. Each gets the problem
   plus one **cognitive frame** (hardware engineer, regulator, 10-year-old,
   competitor, biology, logistics, game design, markets, inversion, extreme
   budget, speedrunner, ant colony, …; 15 built-in). System prompts **forbid
   evaluation**. Branches never see each other — that isolation is the whole
   point; the README is explicit that serializing the calls collapses the method
   into "one wider thought."
2. **Focus** — a *separate* critic call scores every idea on
   **novelty / viability / fit**, explicitly flags **traps** (seductive-but-broken
   ideas) with explanations, **clusters** ideas by underlying approach, and
   **deepens top-K** into sketches with risks and next steps.

The generator↔critic split is mechanical (separate invocations, opposing system
prompts), not a single unified response.

**Reported benchmark** (6 problems, same model, vs single-shot — author's own
numbers, not independently reproduced here):

| Dimension | ADHD | Single-shot | Ratio |
|---|---|---|---|
| Breadth | 9.00 | 4.83 | 1.9× |
| Novelty | 7.83 | 2.67 | 2.9× |
| Trap detection | 9.50 | 1.83 | **5.2×** |

**Stated cost:** ~10 agent calls per run, 5–10× a direct answer. The author's own
guidance is to use it only at decision points where the obvious answer being wrong
has real consequences.

---

## 2. What ICDEV already has (do NOT rebuild)

| Upstream mechanism | ICDEV equivalent | Assessment |
|---|---|---|
| Parallel isolated multi-perspective calls | `ChainOrchestrator.invoke_council` — 5 advisors, `ThreadPoolExecutor`, independent round 1, anonymized peer review, chairman synthesis | **Already built.** Structurally the same machinery. |
| Multi-model diversity per branch | `router.get_diverse_models(pool_role, n)` — picks across provider families | **Already built.** Upstream has nothing comparable. |
| Budget / timeout / cost caps per run | `_check_module_budget`, `_check_budget`, `cost_cap_usd`, `deadline` | **Already built.** Directly answers the 5–10× cost problem. |
| Telemetry, trace, canvas decisions, reasoning events | `_write_chain_telemetry`, `_publish_reasoning_event`, `_record_canvas_decision` | **Already built.** |
| Debate-to-a-verdict | `invoke_chain_of_debate` — parallel debaters, judge synthesis | Already built, but see §3. |
| Self-consistency sampling | `_cot_self_consistency` — N parallel runs, majority vote | Already built; explicitly *convergent*, the opposite goal. |
| Skill/CLI packaging | `.claude/skills/`, `tools/skills/invoke.py` | Already built. |

**Do not adopt:** the npm package, the `adhd-agent` CLI, the TypeScript library, or
the 50+-agent install shims. We have no npm toolchain, and every orchestration
concern the CLI solves is already solved in `chain_orchestrator.py`.

---

## 3. The real gaps (this is the adaptable part)

### Gap 1 — every existing multi-branch mode is *evaluative*, none is *generative*
- **CoD** `positions` come from `_generate_positions()`: a hardcoded agree↔disagree
  gradient ("Strongly in favor" … "Strongly against"). That is a stance axis on
  **one** proposal already on the table. Debaters also see prior arguments from
  round 2 onward — deliberate for debate, but it reintroduces exactly the anchoring
  ADHD's isolation is designed to remove.
- **Council** `_COUNCIL_ADVISORS` are Contrarian / First Principles / Expansionist /
  Outsider / Executor — pressure-test lenses for a decision, not idea generators.
  The docstring says so: "decision-quality analysis," "pressure-testing a
  high-stakes idea/decision."

Nothing in the stack takes a problem and returns **a wide pool of new candidate
solutions under orthogonal generative lenses**.

### Gap 2 — the frame set is hardcoded Python, not a config artifact
`_COUNCIL_ADVISORS` is a module-level list in `chain_orchestrator.py`. Frames
cannot be added, versioned, A/B'd, or selected per-function without a code change.
Upstream's 15 frames are its actual IP; ours should live in `args/` like every
other behavior knob (FORGE layer separation).

### Gap 3 — no trap detection anywhere
This is the strongest measured upstream result (5.2×) and we have **no** analogue.
`tools/creative/gap_scorer.py` scores `pain_frequency` / `gap_uniqueness` /
`effort_to_impact` — all computed against a DB of *already-known* items, so it
structurally cannot score a novel idea that isn't in the DB, and it never asks
"is this attractive and wrong?" `tools/innovation/triage_engine.py` gates on
compliance frameworks, not on idea soundness.

### Gap 4 — the three engines named in the request have no internal ideation
- **Creative** (`creative_engine.py`): `discover → scan → extract → score → rank →
  generate`. Ideas are *ingested* from competitor repos and scraped pain points.
  `spec_generator.py` is explicitly template-driven ("No LLM required — all
  generation is [deterministic]"). Zero idea generation.
- **Innovation** (`solution_generator.py`): `_build_implementation_blueprint`
  is heuristic dispatch off a resolved "gotcha layer." One blueprint per signal,
  no alternatives considered.
- **Research** (`research_engine.py`): source scanning → dossiers → forecasts.
  Ingestion and synthesis, not ideation.
- **idea_lab** (`C:\AI\standalone\idea_lab`): intake Q&A → scoring → spec → build.
  A single funnel from one user-supplied idea; `tools/build/spec_generator.py`
  contract-fills with optional LLM enrichment. No branch point anywhere.

So all four surfaces produce exactly one candidate and then refine it. That is the
premature-convergence failure mode upstream describes, at pipeline scale rather
than token scale.

### Gap 5 — no cluster / deepen-top-K step
Council's chairman synthesizes to a verdict. Nothing groups a wide pool by
underlying approach and expands survivors into actionable sketches with risks and
next steps.

---

## 4. Recommended adaptation

1. **`args/ideation_frames.yaml`** — versioned frame library. Seed with our own
   domain-relevant lenses (adversary/red-team, accreditor/ATO, sustainment tail,
   air-gap operator, adjacent-industry transplant, inversion, extreme budget,
   biology/logistics analogy, …). Migrate `_COUNCIL_ADVISORS` into it so there is
   one source of truth for perspective sets.
2. **`ChainOrchestrator.invoke_divergence()`** — a fourth chain mode beside
   `cot` / `cod` / `council`. Reuses the existing executor, provider diversity,
   budget caps, telemetry, and degrade-cleanly semantics. The two behavioral
   deltas versus `invoke_council`: (a) branches are **strictly isolated** — no
   peer-review round, no shared history; (b) system prompts are **generative and
   forbid evaluation**.
3. **Critic pass with trap detection** — score `novelty / viability / fit`, plus a
   first-class `trap` flag with a written explanation, then cluster and deepen
   top-K. Trap output must feed the existing gates rather than sit in a report.
4. **Wire into the engines** — a divergence branch point in creative
   `stage_generate`, in innovation `solution_generator` (generate alternatives
   before committing to a blueprint), and an optional idea_lab branch step. All
   **off by default** behind config, because of the 5–10× cost.
5. **Bench it on our own stack** before trusting it. The upstream numbers are the
   author's; adopt the *measurement* (breadth / novelty / trap detection vs
   single-shot at identical model) and re-run it against ICDEV functions.

**Non-goals:** replacing CoD or Council; making divergence a default path; any
npm/TypeScript dependency; unbounded agent fan-out.

---

## 5. Cost and risk

- 5–10× token cost per invocation is the headline risk. Mitigation is already in
  the codebase (`cost_cap_usd`, `_check_module_budget`, `timeout_seconds`,
  `_is_excluded`) — the mode must be opt-in per function, never a default.
- Trap detection is an LLM judgment. It must be advisory input to a gate, not an
  autonomous blocker, until benched.
- Frames are prompt content, not user data — but any divergence run over CUI-bearing
  inputs inherits the existing LOCAL-ONLY routing and redaction obligations.
  Divergence must not become a new egress path (cf. `tools/govcon/specialist_consult.py`,
  which fails closed for exactly this reason).

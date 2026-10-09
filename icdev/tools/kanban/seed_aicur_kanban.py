#!/usr/bin/env python3
# CUI // SP-CTI
"""Seed the AICUR project — AI fundamentals + AI-assisted engineering curriculum.

Backs the 2026-10-05 gap analysis of FORGE Academy and GameDay against a nine-topic
target curriculum (registered as ``aicur`` in ``args/projects.yaml``):

  1a tokens  1b vLLM / multiple models  1c benchmarks  1d agents + RAG
  1e prompt engineering  1f verification against hallucinations
  2a coding harnesses  2b code-verification harnesses  2c vibe coding vs engineering

Covered already: 1a, 1d, 1f. Partial: 1b, 1e, 2b. Absent: 1c, 2a, 2c.

Usage::

    python tools/kanban/seed_aicur_kanban.py            # seed
    python tools/kanban/seed_aicur_kanban.py --json     # machine-readable report
    python tools/kanban/seed_aicur_kanban.py --dry-run  # print, insert nothing
"""

from __future__ import annotations

import argparse
import json
import sys

# The dispatcher hands the description verbatim to a worker with no other context,
# so the invariants for this surface travel with every task.
CONTEXT = """
PLATFORM INVARIANTS (FORGE Academy / GameDay):
- Academy lives at `apps/forge_academy/`, GameDay at `apps/ai_gameday/` + `tools/ttx/`
  + `tools/gameday/`; scenario packs at `scenarios/<slug>/`. `apps/` is MIRRORED under
  `icdev/apps/` -- reconcile with `python tools/dx/mirror_parity.py --fix` before pushing.
- Lesson authoring: `apps/forge_academy/content/tier{N}/<slug>/steps/stepN_<name>.md`
  with frontmatter `ontology_id: icdev:mission:<slug>:step:<N>` and `step_class:
  icdev:Lesson|Lab|Assessment|Reflect`; first `# H1` is the step title. A `.py`-only
  step (no `.md`) is SILENTLY skipped by discovery. Catalogue metadata (title, tier,
  prereqs, xp) goes in `BUILTIN_MISSIONS` in `apps/forge_academy/content_loader.py`.
- GRADED, NOT ACKNOWLEDGED: every new lesson step ships either an item bank
  (`content/item_banks/<slug>.yaml`, >=5 items per step, each answerable from its own
  lesson, validated by `assessment.validate_item_bank`) or a coding lab with a sibling
  `stepN_starter.py` + `stepN_test.py` graded server-side by `grading.grade_step` in
  the `code_runner` sandbox. Labs must run OFFLINE (no live LLM, no network).
- Facts must be current as of authoring and dated in the lesson ("as of <month year>");
  never cite a price or benchmark score without its date and source.
- Both apps are `default_enabled: false`: set `ICDEV_FORGE_ACADEMY_ENABLED=true` /
  `ICDEV_GAMEDAY_ENABLED=true` to exercise them.
- Regression floor: pytest tests/test_aca_*.py tests/test_penta_aca_*.py
  tests/test_penta_gd_*.py tests/test_gameday_*.py -q
- New test files: add a fragment `args/ci_test_files/core.d/<task-id>.txt`.
- Worktree-first; never commit on main.
"""


def _t(
    task_id: str,
    title: str,
    description: str,
    *,
    priority: str = "medium",
    task_type: str = "build",
    depends_on: str | None = None,
) -> dict:
    spec = {
        "id": task_id,
        "title": title,
        "description": description.strip() + "\n" + CONTEXT.rstrip() + "\n",
        "task_type": task_type,
        "priority": priority,
        "status": "backlog",
        "dispatch_source": "aicur_gap_seed",
        "idempotency_key": f"aicur::{task_id}",
    }
    if depends_on:
        spec["depends_on_task_id"] = depends_on
    return spec


TASKS: list[dict] = [
    # ------------------------------------------------------------ fun (1a-1f)
    _t(
        "aicur-fun-01",
        "Academy m01: refresh token/model/price facts + graded token-counting lab (1a)",
        """
Topic 1a (tokens) is covered and graded in m01 but its facts are dated.

DO:
- Update `content/tier1/m01-llm-fundamentals/steps/step2_token_economics.md` and
  `step4_context_window.md`: replace GPT-3.5 / Claude 3.7 / 2025 prices with current,
  dated examples; cover input vs output vs cached-input pricing, reasoning/thinking
  tokens, context windows, and why tokenizers differ across model families.
- Also refresh `tier2/m-sre-ai-01-llm-observability/step-1.md:23` price table.
- Add a lab step (step6) with `step6_starter.py` + `step6_test.py`: learner computes
  token cost for a request given a price table and token counts, and trims a prompt
  to fit a context budget. Offline -- use a deterministic toy tokenizer in the starter;
  reference `tools/llm/context_budget.py` for the real budgeting seam.
- Extend `content/item_banks/m01-llm-fundamentals.yaml` for any changed facts.

DONE WHEN: m01 loads 6 steps; step6 grades pass/fail server-side; item bank validates.
""",
        priority="medium",
    ),
    _t(
        "aicur-fun-02",
        "Academy m12: Serving many models -- vLLM, inference servers, routing (1b)",
        """
Topic 1b is partial: only a selection-table row in
`tier2/m-devops-aiml-05-deploy/steps/step1_deploy.md:8-14` and the router intro in
`m01 step5_llm_router.md`. Nothing explains how vLLM works or serving multiple models.

DO: new Tier-1 mission `m12-model-serving` (content/tier1/m12-model-serving/), add to
`BUILTIN_MISSIONS` with prereq m01. Steps:
1. Lesson: inference-server anatomy -- prefill vs decode, KV cache, PagedAttention,
   continuous batching, quantization (AWQ/GPTQ/FP8), throughput vs latency.
2. Lesson: vLLM in practice -- OpenAI-compatible endpoint, serving several models /
   LoRA adapters, tensor parallelism, vLLM vs Ollama vs TGI vs SGLang vs managed APIs,
   air-gap considerations.
3. Lab: multi-model routing. Learner writes a fallback-chain resolver over a provided
   YAML shaped like `args/llm_config.yaml` (providers `vllm`, `mistral_vllm`, `ollama`;
   `routing:` section) choosing a model by function + health + budget. Test asserts
   chain resolution offline. Point to `tools/llm/router.py`, `routing_policy.py`,
   `provider_health.py`, `cost_budget.py` as the real seams.
4. Item bank covering steps 1-2.
""",
        priority="high",
    ),
    _t(
        "aicur-fun-03",
        "Academy m13: Reading benchmarks and building your own evals (1c)",
        """
Topic 1c is effectively absent: one table row in
`tier2/m-swe-aiml-03-safety-eval/steps/step1_safety_eval.md:29`.

DO: new Tier-1 mission `m13-benchmarks-evals`, prereq m01. Steps:
1. Lesson: what common benchmarks measure and their limits -- MMLU/MMLU-Pro, GPQA,
   HumanEval/MBPP, SWE-bench Verified, LiveCodeBench, Terminal-Bench, Chatbot Arena
   (Elo), agentic and RAG benchmarks; pass@k; contamination, saturation, vendor-
   reported vs independently reproduced, harness/scaffold effects on agent scores.
2. Lesson: why your own eval beats a leaderboard -- golden sets, LLM-as-judge and its
   biases, regression evals in CI, cost/latency as eval axes.
3. Lab: implement pass@k and a tiny accuracy eval over a fixed fixture of model
   outputs; test asserts the numbers. Reference `tools/llm/eval_runner.py`,
   `tools/evaluation/agent_benchmark.py`, `tools/rag/crag_evaluator.py`.
4. Item bank for steps 1-2 (include "what does this score NOT tell you" items).
""",
        priority="high",
    ),
    _t(
        "aicur-fun-04",
        "Academy m02: restore missing structured-output lesson + system prompts, CoT, tool prompting (1e)",
        """
Topic 1e is thin. `content/tier1/m02-prompt-engineering/steps/` has `step4_starter.py`
and `step4_test.py` but NO `step4_*.md`, so step 4 never loads (verified 2026-10-05);
there is also no step 3.

DO:
- Write `step3_system_prompts.md` (role/system prompts, delimiting untrusted input,
  instruction hierarchy, prompt injection awareness -- link m-secops-ai-01).
- Write `step4_structured_output.md` matching the existing step4 starter/test.
- Add `step5_reasoning_and_tools.md`: chain-of-thought / extended thinking, when not to
  use it, tool/function-calling prompts, prompt versioning via
  `tools/llm/prompt_registry.py`.
- Create `content/item_banks/m02-prompt-engineering.yaml`.

DONE WHEN: m02 loads 5 steps, step4 lab graded, item bank validates.
""",
        priority="high",
    ),
    _t(
        "aicur-fun-05",
        "Academy item banks for m03 (RAG) and m04 (agents) (1d)",
        """
Agents and RAG content is strong but ungraded: only m01 has an item bank.

DO: create `content/item_banks/m03-rag-basics.yaml` and `m04-first-agent.yaml`,
>=5 items per lesson step, every item answerable from its own lesson text. Include
items on retrieval failure modes, chunking, grounding vs. retrieval, the agent
control loop, tool selection, stopping conditions.

DONE WHEN: both banks pass `assessment.validate_item_bank`; m03/m04 steps report a
graded verdict, not "acknowledged".
""",
        priority="medium",
    ),
    # ------------------------------------------------------------ eng (2a-2c)
    _t(
        "aicur-eng-01",
        "Academy AI-Assisted Engineering 01: coding harnesses and how to use them (2a)",
        """
Topic 2a is absent (only passing mentions in m06 and m-ciso-01).

DO: new Tier-1 mission `m-aie-01-coding-harnesses` (content/tier1/). Steps:
1. Lesson: the harness landscape -- autocomplete (Copilot), IDE agents (Cursor,
   Windsurf, Copilot agent mode), terminal agents (Claude Code, Codex CLI, Aider,
   Gemini CLI); what a harness adds over a raw model (tools, context, permissions,
   memory). Date the landscape.
2. Lesson: driving one well -- instruction files (CLAUDE.md, AGENTS.md,
   `.cursor/rules`, `.github/copilot-instructions.md`), MCP servers, hooks and
   permission modes, plan-then-act, subagents, git worktrees for parallel agents,
   context hygiene.
3. Lab: given a project description, produce instruction files for three harnesses.
   Test validates required sections. Reference `tools/dx/ai_platforms.py` (registry of
   10 platforms) and `tools/dx/instruction_generator.py`.
4. Item bank.
""",
        priority="high",
    ),
    _t(
        "aicur-eng-02",
        "Academy AI-Assisted Engineering 02: verification harnesses -- TDD, red-first, linters, CI (2b)",
        """
Topic 2b is partial (`m-readiness-02/step3_ci_gate.md`, m-secops-01 Bandit). No TDD.

DO: new Tier-1 mission `m-aie-02-verification-harnesses`, prereq m-aie-01. Steps:
1. Lesson: why AI code needs a harness -- tests as the spec, RED -> GREEN -> REFACTOR,
   a test that passes on the buggy code is not a test (ICDEV's
   `tools/ci/red_first_gate.py`), linters/type checkers/SAST (ruff, mypy, bandit),
   pre-commit hooks, CI required checks, sandboxed execution.
2. Lab (graded, the core): learner writes a pytest test for a provided function. The
   stepN_test.py runs the learner's test against a BUGGY and a FIXED implementation
   and passes only if it FAILS on buggy and PASSES on fixed. Must work inside the
   `code_runner` sandbox allowlist -- check `apps/forge_academy/code_runner.py`.
3. Lesson + item bank: wiring the loop -- letting the agent run tests, never letting it
   edit the test to pass, `|| true` and swallowed exceptions as gate neutralisers.
""",
        priority="high",
        depends_on="aicur-eng-01",
    ),
    _t(
        "aicur-eng-03",
        "Academy AI-Assisted Engineering 03: vibe coding vs engineering -- best practices and anti-patterns (2c)",
        """
Topic 2c is absent.

DO: new Tier-1 mission `m-aie-03-vibe-vs-engineering`. Steps:
1. Lesson: the spectrum -- when vibe coding is fine (throwaway prototypes, spikes,
   personal scripts) and when it is not (production, security, regulated/CUI systems);
   the Karpathy principles in `hardprompts/karpathy_principles.md`.
2. Lesson: anti-patterns with real examples drawn from ICDEV's own guardrails in
   CLAUDE.md -- hallucinated APIs and packages (slopsquatting), tests edited to pass,
   `|| true`, `except Exception: pass` that reports success while persisting nothing,
   sprawling unreviewed diffs, secrets pasted into prompts, trusting "done" without
   evidence, hardcoded model IDs.
3. Reflect step with a graded question set: spot-the-defect in short AI-generated
   snippets (multiple choice, server-side key).
4. Item bank.
""",
        priority="high",
    ),
    _t(
        "aicur-eng-04",
        "Academy AI-Assisted Engineering capstone + learning-path wiring",
        """
DO:
- Capstone mission `m-aie-04-capstone`: learner takes a small vibe-coded module
  (provided, with 3 planted defects) to production grade -- writes discriminating tests,
  fixes defects, adds an instruction file. Graded by stepN_test.py.
- Register m-aie-01..04, m12, m13 in `BUILTIN_MISSIONS` with prereqs and roles so
  `GET /api/academy/learning-path` recommends them (blueprint.py ~1498).
- Feature doc `docs/features/phase-aicur-ai-curriculum.md` with the 9-topic coverage
  matrix showing each topic's mission(s).
- Playwright: `/e2e:forge_academy` covers one new mission end-to-end.
""",
        priority="medium",
        depends_on="aicur-eng-03",
    ),
    # ------------------------------------------------------------ gd
    _t(
        "aicur-gd-01",
        "GameDay pack vibe_to_verified: ship AI-written code safely (1a-1c, 2b, 2c)",
        """
GameDay has nothing on coding with AI, routing, or benchmarks.

DO: new pack `scenarios/vibe_to_verified/` (auto-discovered by
`tools/ttx/scenario_loader.py`): scenario.yaml, injects/, rubrics/, personas/.
Injects:
1. AI-generated PR review -- find the hallucinated dependency, swallowed exception and
   non-discriminating test (2c). `context_note` holds the answer key.
2. Red-first sprint -- write a test that fails on the bug, then fix (2b). Prefer a
   deterministic scorer: new `inject_type` branch in `tools/ttx/ai_scorer.py`
   following the `aadc_design_challenge` pattern (~line 251), running the submitted
   test against buggy/fixed code via the sandbox.
3. Routing outage -- primary vLLM endpoint down, token budget halved; configure a
   fallback chain and justify cost/latency (1a/1b).
4. Benchmark claim -- vendor claims a SWE-bench score; design a counter-eval and make
   the adopt/reject call (1c).
Link roles' `academy_missions` to m12, m13, m-aie-01..03.
Tests: scenario loads and validates; deterministic scorer red-first tested.
""",
        priority="medium",
    ),
    _t(
        "aicur-gd-02",
        "GameDay forge_ascent: add a harness-comparison inject (2a)",
        """
DO: add an inject to `scenarios/forge_ascent/` where a team solves the same small task
with two different coding harnesses (or two configurations of one: with and without an
instruction file / plan mode) and submits diffs, test results and a short comparison.
Rubric scores correctness, test evidence, and the quality of the harness comparison.
Wire it into forge_ascent's scenario.yaml `injects[]` with a rubric file.
""",
        priority="low",
    ),
    # ------------------------------------------------------------ fix
    _t(
        "aicur-fix-01",
        "Academy credential gate queries non-existent ttx_receipts table",
        """
`apps/forge_academy/db.py:1925` runs `SELECT COUNT(*) FROM ttx_receipts WHERE
player_id=... AND status='submitted'`. No migration or DDL creates `ttx_receipts`
(grep 2026-10-05). The exception is swallowed, the count is 0, so the GameDay
credential gate (`constants.py` gameday_scenarios_min / gameday_top_percentile) can
never be met.

DO: count completed GameDay participation from the tables that exist
(`ttx_responses` / `ttx_scores` / `ttx_registrations` in `apps/ai_gameday/db.py`,
joined via `academy_username` stored by `apps/ai_gameday/registration.py`). Stop
swallowing the error silently. Red-first test.
""",
        priority="medium",
        task_type="fix",
    ),
    _t(
        "aicur-fix-02",
        "Wire award_gameday_xp / get_gameday_seed_bonus to a real caller",
        """
`apps/forge_academy/gamification.py:186` `award_gameday_xp` and `:239`
`get_gameday_seed_bonus` have no caller in apps/ or tools/ -- declared, never consumed.

DO: call `award_gameday_xp` when a TTX session's leaderboard is finalised
(`tools/ttx/leaderboard.py` / engine end-of-session) for players linked to an Academy
username; consume the seed bonus where teams are seeded, or delete it. Test both.
""",
        priority="medium",
        task_type="fix",
        depends_on="aicur-fix-01",
    ),
    _t(
        "aicur-fix-03",
        "Academy: .py-only step folders are silently skipped -- warn loudly or add lessons",
        """
Discovery scans only `.md`, so these folders hold starter/test code that never loads:
m02 step4 (handled in aicur-fun-04), dataops-03/04, devops-03/04/05, secops-03/04,
swe-03/04, netops-05.

DO: make `content_loader.discover_missions` log a warning per `.py`-only step and add a
test that enumerates them against a shrink-only allowlist so the set cannot grow.
Do not author the missing lessons here.
""",
        priority="low",
        task_type="fix",
    ),
    _t(
        "aicur-fix-04",
        "GameDay League runs only cyber scenarios; interagency pack missing injects",
        """
- `tools/gameday/game_master.py:53-62` iterates only `CYBER_SCENARIOS`; the 14 AI-ops
  League scenarios in `tools/gameday/constants.py:154-313` (ace, rg, gc, dr, cx, di,
  nt, of, fz, gr) are registered and tested but never played. Let the game master
  select from both sets (config in `args/gameday_teams.yaml`).
- `scenarios/interagency/scenario.yaml` references 5 `injects/*.yaml` that do not
  exist. Author them or remove the pack from discovery -- decide and document.
Tests for both.
""",
        priority="low",
        task_type="fix",
    ),
]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Seed AICUR curriculum-gap tasks")
    ap.add_argument("--json", action="store_true", help="JSON report to stdout")
    ap.add_argument("--dry-run", action="store_true", help="Print, insert nothing")
    args = ap.parse_args(argv)

    if args.dry_run:
        print(json.dumps({
            "dry_run": True,
            "count": len(TASKS),
            "tasks": [{"id": t["id"], "title": t["title"],
                       "depends_on": t.get("depends_on_task_id")} for t in TASKS],
        }, indent=2))
        return 0

    from tools.kanban.task_factory import create_tasks

    created = create_tasks(TASKS)
    report = {
        "created": created,
        "created_count": len(created),
        "submitted_count": len(TASKS),
        "skipped_existing": [t["id"] for t in TASKS if t["id"] not in created],
    }
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(f"Seeded {len(created)}/{len(TASKS)} AICUR tasks")
        for tid in created:
            print(f"  + {tid}")
        if report["skipped_existing"]:
            print("  (already present: " + ", ".join(report["skipped_existing"]) + ")")
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
# CUI // SP-CTI
"""Seed the HGX (Harness Agent Parity and Graph Runtime) card onto the board.

HGX promotes ICDEV from a platform that *orchestrates* agent harnesses into one
that *is* one, and then adds the graph layer on top of the runtime that already
exists.

Ground rule for every task: **extend the existing surface, do not add a parallel
one** (``docs/spikes/dwo-00-superplane-workflow-adaptation.md`` line 116). The
graph-engine proposals in ``plans/agent-runtime-implementation-plan.md`` and the
four ``C:\\AI\\searches`` docs are wrong on inventory — Studio, the gateway and
kanban already ship most of what they ask for. Do NOT build
``tools/workflow/graph_engine.py``, a second kanban board, or a second messaging
ingress.

This card is **MANUAL-ONLY**. ``hgx-gate-00`` is held ``in_progress`` and every
epic head declares ``depends_on_task_id`` on it, because a held gate alone does
not actually hold — ``depends_on_task_id`` is the real gate. Release with::

    python tools/kanban/cli.py --set-status hgx-gate-00 done

Run it as a MODULE, from the repo (or worktree) root::

    python -m tools.kanban.seed_hgx_kanban            # seed
    python -m tools.kanban.seed_hgx_kanban --json     # machine-readable report
    python -m tools.kanban.seed_hgx_kanban --dry-run  # print, insert nothing

Invoking it by path (``python tools/kanban/seed_hgx_kanban.py``) puts the
script's own directory on ``sys.path`` instead of the repo root, so ``tools.*``
either fails to import or — worse, from inside a worktree — resolves to the
SHARED checkout and seeds against the wrong database.
"""

from __future__ import annotations

import argparse
import json
import sys

GATE = "hgx-gate-00"


def _t(
    task_id: str,
    title: str,
    description: str,
    *,
    depends_on: str | None = None,
    priority: str = "medium",
    task_type: str = "build",
    status: str = "backlog",
    acceptance: str | None = None,
) -> dict:
    spec: dict = {
        "id": task_id,
        "title": title,
        "description": description.strip(),
        "task_type": task_type,
        "priority": priority,
        "status": status,
    }
    if depends_on:
        spec["depends_on_task_id"] = depends_on
    if acceptance:
        spec["acceptance_criteria"] = acceptance.strip()
    return spec


_CONTEXT = """
Card: HGX — Harness Agent Parity and Graph Runtime. MANUAL-ONLY, gated on
hgx-gate-00. Open any PR as --draft (pr_watcher auto-merges green kanban/*).

GROUND RULE: extend the existing surface. Studio's workflow_runner.py is already
a durable DAG runtime with human gates, restart-safe resume and per-node tool
authorization (gate MCP-WF-001). tools/gateway/adapters already ships 9 channels
behind an 8-gate security chain. tools/kanban already has atomic claim/lease.
Do NOT build a second engine, board or ingress.

BINDING ACCEPTANCE CRITERIA on every task in this card:
  LLM-agnostic  — no model IDs in Python (route by llm_function through
                  LLMRouter); catch AgentLoopUnsupported and degrade rather than
                  fail; native tool-use only, never a provider-specific payload;
                  provider-only features (prompt caching, streaming) must be
                  optional and self-degrading.
  OS-agnostic   — encoding="utf-8" AND newline="" on every read and write of a
                  file an agent edits; no shell (list argv, shell=False,
                  sys.executable, no grep/sed/find); pathlib everywhere and repo
                  root from __file__ not os.getcwd(); explicit two-sided
                  platform branches (see tools/llm/cross_process_lease.py);
                  threads not asyncio (D36); shutil.which for executables.
Mirror every changed tools/ module to icdev/tools/. Note tools/llm/agent_loop.py
is a re-export shim — edit the icdev/ copy.
"""


TASKS: list[dict] = [
    # ══════════════════════════════════════════════════════════════════
    # GATE
    # ══════════════════════════════════════════════════════════════════
    _t(
        GATE,
        "HGX HOLD GATE — do not dispatch until released",
        """
This card touches the safety hooks, the executor chain and the approval path.
It must not be built unattended.

Every epic head declares depends_on_task_id on this task, because a held gate
alone does not hold — the runner reads depends_on_task_id, not gate status.

Release, once a human is driving:
    python tools/kanban/cli.py --set-status hgx-gate-00 done
""",
        priority="critical",
        task_type="chore",
        status="in_progress",
    ),

    # ══════════════════════════════════════════════════════════════════
    # EXEC — owned executor parity behind the AgentAdapter seam
    # ══════════════════════════════════════════════════════════════════
    _t(
        "hgx-exec-01",
        "Equip the owned build agent and fix its newline round-trip",
        f"""{_CONTEXT}
PROBLEM (two, in the same file)

1. `tools/genesis/rubric_build_tools.py::build_worktree_toolset` exposes exactly
   five tools — read_file, list_files, write_file, patch_file, done. No shell, no
   search, no test execution, no git. The agent cannot run the tests it is graded
   on by make_pipeline_grader, cannot grep for a symbol, cannot read a diff.

2. BLOCKING OS DEFECT. `_read`/`_patch` call `read_text(encoding="utf-8")` —
   universal-newline translation is ON, so CRLF arrives as \\n. `_write`/`_patch`
   call `write_text(encoding="utf-8")` with NO `newline=""`, so on Windows Python
   translates \\n back to \\r\\n. Net effect on Windows: patching one line
   rewrites the WHOLE FILE to CRLF. `git diff` shows every line changed, the
   grader's `_git_changed_files` reports a whole-file rewrite, and a reviewer sees
   pure noise. On Linux the same edit is a clean one-line diff. The owned
   executor therefore produces materially different output depending on host OS,
   which by itself invalidates the hgx-exec-04 parity benchmark.

BUILD
1. Fix the newline round-trip first, on its own commit so it is attributable:
   read with `open(path, encoding="utf-8", newline="")` and write the same way,
   so Python performs no translation in either direction and a file keeps the
   endings it already had.
2. Add `run_command` — reuse the handler in
   `tools/agent_runtime/mutating_tools.py`, which already binds the
   `tools/skills/invoke.py` allowlist (`python tools/`, `python -m tools`,
   `python -c`). That allowlist is portable *because* it invokes Python; do not
   widen it to arbitrary shell.
3. Add `grep_files` / `search_files` — reuse the implementations in
   `tools/ace/agent_tools.py`. They must be pure Python. Do NOT shell out to
   `grep`, which does not exist on Windows.
4. Add a read-only `git_diff` invoked with a list argv and `shell=False`.
5. Set `is_read_only` correctly on each new schema — the agent loop partitions on
   that flag to decide what runs concurrently (agent_loop.py:1196-1203), so a
   mutating tool marked read-only would be dispatched in parallel.
6. Gate the mutating tools through `safety.build_safety_gate`.

WHY IT MATTERS
This is the toolset the whole Phase-H thesis rests on: ICDEV's only owned
file-editing executor. `args/agent_adapters.yaml` currently says in a comment
"Prefer claude_cli for anything that actually needs to edit files" — this task is
the first step in making that comment untrue.
""",
        depends_on=GATE,
        priority="critical",
        acceptance="""
- A file with LF endings and a file with CRLF endings each survive a patch_file
  round-trip with ONLY the intended line changed, asserted via
  `git diff --numstat`, on BOTH windows-latest and ubuntu-latest.
- run_command, grep_files, search_files and git_diff are present in
  build_worktree_toolset with correct is_read_only flags.
- No handler invokes a shell or a POSIX-only binary; every subprocess uses a list
  argv with shell=False.
- tests/genesis/test_rubric_build_tools.py extended and green.
""",
    ),
    _t(
        "hgx-exec-02",
        "Add a local_agent AgentAdapter wrapping the owned rubric loop",
        f"""{_CONTEXT}
PROBLEM
`tools/agents/adapter_base.py` (OPT-71) defines the right seam — an AgentAdapter
wraps a full agent *session* (multi-turn, tool calls, completion detection), one
layer above tools/llm/*_provider.py. `registry.pick_default()` has a complete
precedence chain driven by `args/agent_adapters.yaml`. It has ZERO consumers.

Worse, the adapter that exists for local execution,
`tools/agents/adapters/local_llm_router.py` (103 lines), calls
`router.invoke("code_generation", request)` — a SINGLE-SHOT completion, not an
agent loop. So the abstraction's own local path cannot edit files either.

BUILD
`tools/agents/adapters/local_agent.py` implementing the AgentAdapter protocol by
wrapping `icdev.tools.llm.agent_loop.run_agent_loop_with_rubric` with the toolset
from hgx-exec-01 and `tools/workflow/pipeline_grader.make_pipeline_grader`.
Register it in `args/agent_adapters.yaml` `enabled_adapters`.

It MUST catch `AgentLoopUnsupported` (raised for provider_name == "cli" and for
any model with supports_tools false, agent_loop.py:428-455) and degrade with a
recorded reason rather than failing the run — ACE already models this correctly
in `coworker_thread._run_agent_loop`, which falls back to step mode with an audit
row. Copy that shape.

Do not touch `local_llm_router` — it remains correct for research/plan tasks that
only need prose.
""",
        depends_on="hgx-exec-01",
        priority="critical",
        acceptance="""
- local_agent satisfies the AgentAdapter protocol and is returned by
  pick_default() when ICDEV_AGENT_ADAPTER=local_agent.
- Given a non-tool-capable model or the CLI bridge, it degrades with a recorded
  reason and does not raise.
- No model ID appears in the module; every call routes by llm_function.
""",
    ),
    _t(
        "hgx-exec-03",
        "Route kanban dispatch through tools/agents/pick_default()",
        f"""{_CONTEXT}
PROBLEM
`tools/genesis/reflexes/kanban.py::_dispatch_to_claude` hand-rolls executor
selection, and `tools/agents/adapters/claude_cli.py::invoke` is a SECOND,
independent implementation of the same `claude --dangerously-skip-permissions
--max-turns` shellout. Only the kanban one carries the production hardening: env
tagging so the stop hook attributes commits, a stdin temp-file to dodge the
Windows 32767-char command-line limit, model override, and degradation tracking.

BUILD
1. Move the hardened kanban implementation behind the AgentAdapter protocol and
   delete the thin duplicate in `tools/agents/adapters/claude_cli.py`.
2. Route `_dispatch_to_claude` through `tools/agents/registry.pick_default()`.
3. Register `local_agent` as a supported value in the executor chain in
   `args/strategos_config.yaml` (currently only claude_cli, gitlab,
   github_actions, ollama_local are supported — the owned loop is not even a
   legal chain entry).
4. Executable discovery via `shutil.which`, which resolves .exe/.cmd/.bat through
   PATHEXT on Windows. The current `~/.local/bin/claude` fallback is
   POSIX-flavoured; keep it only as a secondary probe.

DO NOT change the default executor. claude_cli stays primary — see hgx-exec-04.
""",
        depends_on="hgx-exec-02",
        priority="high",
        acceptance="""
- Exactly one claude-CLI shellout implementation remains in the tree.
- pick_default() has a real caller; ICDEV_AGENT_ADAPTER overrides it.
- args/strategos_config.yaml accepts local_agent as a chain entry.
- Default resolution is unchanged: with the claude CLI present, claude_cli is
  still selected for build/fix/deploy/test.
""",
    ),
    _t(
        "hgx-exec-04",
        "Benchmark owned-executor parity against claude_cli (do not flip)",
        f"""{_CONTEXT}
PROBLEM
`_dispatch_via_rubric_loop` is gated off (`KANBAN_RUBRIC_LOOP`, default OFF),
is not a supported chain value, and is documented nowhere outside two TSR test
reports. It has 13 unit tests and ZERO parity evidence. Nobody knows whether
ICDEV's own agent can actually build a task.

BUILD
`tests/genesis/test_executor_parity.py` — a fixed corpus of ~10 already-merged
kanban tasks (pick ones whose diffs are self-contained), replayed through both
executors in disposable worktrees and scored by
`tools/workflow/pipeline_grader.make_pipeline_grader`. Report gate-pass rate,
wall-clock and token cost per executor. Publish the numbers in
`docs/features/hgx-executor-parity.md`.

EXPLICIT NON-GOAL — DO NOT FLIP THE DEFAULT.
`claude_cli` remains primary. `KANBAN_RUBRIC_LOOP` stays off. This task exists to
produce the data and to give the platform a working answer when the CLI is
absent; the flip is a separate, later, human decision made against these numbers.
Do not reorder the chain in this task.

BLOCKED ON hgx-guard-02: flipping or even recommending the owned executor before
headless guardrail parity would move autonomous builds onto the WEAKER guardrail
set (2 of 8 checks).
""",
        depends_on="hgx-exec-03",
        priority="high",
        task_type="test",
        acceptance="""
- A reproducible benchmark exists and runs on both OSes.
- docs/features/hgx-executor-parity.md records gate-pass rate, wall-clock and
  cost for both executors over the same corpus.
- args/strategos_config.yaml fallback_chain order is UNCHANGED and
  KANBAN_RUBRIC_LOOP still defaults off.
""",
    ),

    # ══════════════════════════════════════════════════════════════════
    # CTXW — long-horizon context and interruptibility
    # ══════════════════════════════════════════════════════════════════
    _t(
        "hgx-ctxw-01",
        "Count tool traffic in the token estimator and use the real context window",
        f"""{_CONTEXT}
PROBLEM — this is the concrete cause of the "long-horizon degradation" the source
docs only theorize about, and it is two bugs compounding.

1. `_estimate_message_tokens` (icdev/tools/llm/agent_loop.py:249-258) counts ONLY
   `block["text"]` for list-content messages. A `tool_use` block (which carries
   `input`) and a `tool_result` block (which nests text under `content`) each
   contribute ZERO. In a tool-heavy run — i.e. every real build — the dominant
   content is invisible to the compaction trigger. Token estimation itself is
   `len(text) // 4` (`:242-246`); there is no tiktoken in the repo.

2. Compaction fires when the estimate exceeds a STATIC
   `context_window_tokens: 64000` from args/llm_config.yaml. Meanwhile
   `tools/llm/context_budget.py` is a complete per-model facility —
   `model_windows()`, `context_window_for()`, and crucially
   `floor_window_for_function()`, which takes the MINIMUM window across a routed
   fallback chain (the only bound that stays correct when a run falls back
   mid-flight from a 200k model to a 32k one). `agent_loop.py` imports it ZERO
   times. The YAML declares 200000 for Claude models and 1000000 for Gemini.

BUILD
1. Count `tool_use.input` and `tool_result.content` in `_estimate_message_tokens`.
2. Resolve the trigger threshold from
   `context_budget.floor_window_for_function(llm_function)`, keeping the config
   value as the floor when the chain declares no window.
3. Keep it LLM-agnostic: never branch on a model family; the chain-minimum is the
   whole point.
""",
        depends_on=GATE,
        priority="critical",
        acceptance="""
- A conversation of N tool_use/tool_result blocks with no text blocks produces a
  non-zero token estimate proportional to the tool payloads.
- With a chain routing to a 200k model, compaction does not fire at 64k; with a
  chain whose minimum is 32k, it fires at 32k.
- No model ID or family name appears in the changed code.
- tests/test_context_budget_wiring.py added and green.
""",
    ),
    _t(
        "hgx-ctxw-02",
        "Make compaction failure and budget blocks first-class outcomes",
        f"""{_CONTEXT}
PROBLEM
Two failures are currently misreported as generic execution errors, which makes
them undiagnosable from a transcript.

1. `_maybe_compress_messages` (agent_loop.py:418-420) swallows a compressor
   failure with a WARNING and returns the messages UNCHANGED. The loop then calls
   the provider with an oversized context, the provider rejects it, and the run
   surfaces as `error_during_execution` — indistinguishable from a network fault.

2. `router.invoke()` raises `ModuleBudgetExceededError` pre-invoke; the loop
   catches it at `:1159` and also reports `error_during_execution`. A budget block
   reads as a crash.

Separately, the budget hard-stops at `:1409`/`:1427` are checked AFTER the full
tool sweep of a turn, so a run can overshoot by a whole turn plus all its tool
output. And the per-agent `BudgetExceededError` path in router.py:2362-2376 is
dead for loop traffic because the loop never sets `request.agent_id`.

BUILD
1. Add distinct `result_subtype`/`truncation_reason` values for compaction failure
   and for a module/agent budget block, alongside the existing 13 subtypes in
   `ResultSubtype`.
2. Thread `agent_id` onto the LLMRequest the loop builds so the per-agent budget
   gate becomes live.
3. Evaluate the token/cost hard-stop before dispatching a turn's tools as well as
   after, so the overshoot is bounded by one LLM call rather than one full turn.
""",
        depends_on="hgx-ctxw-01",
        priority="high",
        acceptance="""
- A forced compressor exception yields a compaction-specific truncation_reason,
  not error_during_execution.
- A module-budget block yields a budget-specific subtype.
- agent_id is present on requests the loop issues, and the per-agent budget gate
  fires in a test.
""",
    ),
    _t(
        "hgx-ctxw-03",
        "Make a running turn interruptible",
        f"""{_CONTEXT}
PROBLEM
There is no way to stop a running turn.
- `stop_event` is read only at turn boundaries (agent_loop.py:1050, :1469).
- Started ThreadPoolExecutor futures cannot be cancelled, and the `with executor`
  block calls shutdown(wait=True), so loop exit BLOCKS until every abandoned tool
  thread finishes.
- `AgentRuntime._stop` (runtime.py:115) is passed to the loop as stop_event but is
  never `.set()` anywhere — there is no `AgentRuntime.stop()`.
- In `cli.loop()` the turn body is wrapped in `except Exception`, which does not
  catch KeyboardInterrupt (a BaseException) — so Ctrl-C during a turn kills the
  whole process rather than the turn.

BUILD
1. `AgentRuntime.stop()` that sets `_stop`.
2. A SIGINT handler in `cli.loop()` that sets the event instead of killing the
   process, using `signal.signal(signal.SIGINT, handler)` — which exists on both
   Windows and POSIX. Do NOT use SIGBREAK, SIGKILL, process groups, or
   `loop.add_signal_handler` (Unix-only). Windows delivers KeyboardInterrupt to
   the main thread only, so the handler must set the threading.Event rather than
   assume a worker sees the exception.
3. Pass the cancellation token into long-running handlers (dispatch.py already
   injects stop_event into any handler whose signature accepts it) and document
   that handlers are expected to poll it.
""",
        depends_on="hgx-ctxw-02",
        priority="high",
        acceptance="""
- Ctrl-C during a turn returns to the REPL prompt with the turn marked stopped;
  the process survives. Verified on Windows and Linux.
- AgentRuntime.stop() causes the loop to exit at the next boundary with
  truncation_reason=stop_event.
""",
    ),

    # ══════════════════════════════════════════════════════════════════
    # GUARD — headless guardrail parity, declared read-only
    # ══════════════════════════════════════════════════════════════════
    _t(
        "hgx-guard-01",
        "Extract the pre-tool safety checks into one shared module",
        f"""{_CONTEXT}
PROBLEM
`.claude/hooks/pre_tool_use.py::main()` runs EIGHT blocking checks: .env read
blocking, dangerous rm, append-only table writes, direct sqlite3.connect(),
D-ORCH-8 file access tiers, unmerged-branch-deletion safety, worktree path
enforcement, review-loop precommit.

`tools/airgap/hook_compat.run_pre_tool_check` — the function SAG and every
non-Claude-Code orchestrator actually calls — implements TWO: git-danger patterns
and append-only tables. (It also has one check Claude Code does not: git danger
patterns. So neither path is a superset.)

Net: an agent running OUTSIDE Claude Code is materially LESS guarded than one
inside it. For an IL5/IL6 platform that is exactly backwards.

BUILD
`tools/hooks/shared_checks.py` holding all eight checks as pure functions over
(tool_name, tool_input) -> Optional[reason]. Both `.claude/hooks/pre_tool_use.py`
and `tools/airgap/hook_compat.py` import and call it, so the two paths cannot
drift again. This task is the extraction only — behaviour must be byte-identical
on both sides afterwards.

Repo root must be resolved from __file__, never os.getcwd() — these checks run
from worktrees, which is precisely the cwd hazard CLAUDE.md documents, and any
intentional bypass needs an `# rls-bypass:` annotation.
""",
        depends_on=GATE,
        priority="critical",
        acceptance="""
- tools/hooks/shared_checks.py exists; both hook paths call it.
- The Claude Code hook blocks exactly what it blocked before (no behaviour change
  in this task).
- No check reads os.getcwd().
""",
    ),
    _t(
        "hgx-guard-02",
        "Bring the headless path to full guardrail parity",
        f"""{_CONTEXT}
PROBLEM (continues hgx-guard-01)
Beyond the six missing checks, `run_pre_tool_check` SHORT-CIRCUITS to allowed for
any tool whose name is not in ("Bash","bash","shell","sql","Write","Edit") —
so an unrecognised mutating tool is waved through unscanned.

Also: only PreToolUse has a headless analogue at all. PostToolUse, Stop,
SubagentStop, PreCompact and UserPromptSubmit have none, even though the agent
loop already exposes matching hook slots (PreToolUseHook, PostToolUseHook,
StopHook, TurnCallback) and calls on_pre_tool_use at :1219/:1254.

BUILD
1. Wire all eight shared checks into `run_pre_tool_check`.
2. Remove the six-name short-circuit; an unknown tool must be scanned, not
   allowed. Keep read-only tools cheap.
3. Wire the loop's existing on_post_tool_use / on_stop slots to headless
   equivalents of the corresponding Claude Code hooks.
4. Keep both blocks audited to `hook_events` as they are today.

THIS TASK BLOCKS hgx-exec-04. Recommending the owned executor before parity here
would move autonomous builds onto the weaker guardrail set.
""",
        depends_on="hgx-guard-01",
        priority="critical",
        acceptance="""
- A parity test asserts the headless path blocks every case the Claude Code hook
  blocks, driven off the shared module so the two cannot diverge.
- An unrecognised mutating tool name is scanned rather than allowed.
- on_post_tool_use and on_stop have headless implementations.
""",
    ),
    _t(
        "hgx-guard-03",
        "Declare read_only on tools instead of guessing it from the name",
        f"""{_CONTEXT}
PROBLEM
`tools/agent_runtime/discovery.py:380-386` decides whether a tool is read-only by
matching its NAME against `_READ_ONLY_PREFIXES` (`_guess_read_only`). That flag is
what the agent loop partitions on to decide which tool calls run CONCURRENTLY
(agent_loop.py:1196-1203). A mutating tool whose name happens to start with a
read-only prefix is therefore dispatched in parallel with others. The docstring
acknowledges the heuristic and defers to the safety layer — but the safety layer
runs AFTER the parallel partition is chosen.

BUILD
`tools/mcp/tool_registry.py` already carries hand-authored schemas for all 463
tools with zero missing descriptions or input schemas. Add an explicit
`read_only` boolean there. Make `_guess_read_only()` the fallback for undeclared
tools ONLY, and log every fallback so the remaining gap is measurable.

Start with the tools that can mutate: anything in the deploy/apply/delete/heal
families must be declared explicitly regardless of name.
""",
        depends_on="hgx-guard-02",
        priority="high",
        acceptance="""
- read_only is declared for every tool that can mutate state.
- _guess_read_only is only reached for undeclared tools and logs when it is.
- A test asserts no declared-mutating tool lands in the parallel partition.
""",
    ),

    # ══════════════════════════════════════════════════════════════════
    # PORT — LLM- and OS-agnostic enforcement
    # ══════════════════════════════════════════════════════════════════
    _t(
        "hgx-port-01",
        "Remove the five hardcoded model IDs from the kanban reflex",
        f"""{_CONTEXT}
PROBLEM
`tools/genesis/reflexes/kanban.py` pins `model="claude-haiku-4-5-20251001"` in
FIVE places — lines 449, 522, 739, 8139, 8239 (timeout-hint extraction,
gap-subject extraction, resume-at parsing, and two more). CLAUDE.md forbids this
outright: "LLM config via .env, never hardcode model IDs in Python". On a
non-Anthropic or air-gapped deployment those calls fail and the surrounding
`except` swallows it, so the feature degrades SILENTLY.

BUILD
1. Declare one or more routed functions in `args/llm_config.yaml` (e.g.
   `kanban_nlp_extract`) with an appropriate cheap-tier chain, and replace each
   literal with a routed call so the deployment's own chain serves it.
2. Add a check — coherence rule or test — that fails on a literal
   `claude-*` / `gpt-*` / `gemini-*` model string anywhere in `tools/`, excluding
   `args/` and the provider modules that legitimately name models.
""",
        depends_on=GATE,
        priority="critical",
        task_type="chore",
        acceptance="""
- Zero literal model IDs remain in tools/genesis/reflexes/kanban.py.
- tests/test_no_hardcoded_model_ids.py passes and would fail if one returned.
- The five call sites still work when the router is pointed at Ollama.
""",
    ),
    _t(
        "hgx-port-02",
        "Add a windows-latest CI job for the portability-sensitive suite",
        f"""{_CONTEXT}
PROBLEM
All nine jobs in `.github/workflows/icdev-ci.yml` are `runs-on: ubuntu-latest`.
ICDEV is developed on Windows and tested only on Linux, so every OS-portability
defect is STRUCTURALLY INVISIBLE to the pipeline. That is exactly how the
rubric_build_tools newline bug (hgx-exec-01) survived: it cannot fail on Linux.

BUILD
1. A `windows-latest` job running the portability-sensitive subset:
   tests/genesis/test_rubric_build_tools.py, tests/test_agent_loop*.py,
   tests/studio/, and the new executor tests. Keep it non-required at first so it
   cannot block merges while its own flakiness is characterised, then promote it.
2. `tests/test_toolset_portability.py` — every handler in the build toolset runs
   with shell=False and a list argv; no POSIX-only binary appears in any command.
3. Preserve the path isolation the workflow already has: workflow-level
   `defaults.run.working-directory` and absolute `PYTHONPATH:
   ${{{{ github.workspace }}}}`, so coherence/RLS checks resolve against the
   canonical checkout root.
""",
        depends_on="hgx-port-01",
        priority="critical",
        task_type="test",
        acceptance="""
- A windows-latest job exists and runs the named subset.
- test_toolset_portability.py passes on both OSes.
- Deliberately breaking the newline fix from hgx-exec-01 turns the Windows job
  red and the Linux job green.
""",
    ),

    # ══════════════════════════════════════════════════════════════════
    # SESS — project context and skill visibility
    # ══════════════════════════════════════════════════════════════════
    _t(
        "hgx-sess-01",
        "Load project context (CLAUDE.md / AGENTS.md / MEMORY.md) at session start",
        f"""{_CONTEXT}
PROBLEM
The SAG runtime never reads the project's own instructions. Grep across
tools/agent_runtime/ and tools/llm/ finds 12 hits for CLAUDE.md — ALL of them use
it as a repo-root SENTINEL FILENAME for an upward directory walk. No file is ever
opened and put in a prompt. There is no AGENTS.md handling and no
memory/MEMORY.md handling at all. `tools/project/session_context_builder.py`
exists but its only callers emit its command line into generated markdown for a
human to run, plus one SDK subprocess shell-out.

What the agent actually gets is the static 6-line `_DEFAULT_SYSTEM_PROMPT`
(runtime.py:37-43), profile facts, and top-5 hybrid-memory hits.

BUILD
Load CLAUDE.md, AGENTS.md and memory/MEMORY.md into the system prompt at session
start, reusing `session_context_builder.py` rather than re-implementing it.
Budget the result against the real context window from hgx-ctxw-01 — this is a
large block and must not crowd out the conversation on a small local model.
Cache per session; invalidate on /new.
""",
        depends_on=GATE,
        priority="high",
        acceptance="""
- A fresh `icdev chat` session's system prompt contains project instructions.
- The block is truncated against floor_window_for_function, not a constant.
- With a 32k-window chain the block degrades rather than consuming the window.
""",
    ),
    _t(
        "hgx-sess-02",
        "Make skills visible to the model with progressive disclosure",
        f"""{_CONTEXT}
PROBLEM
Skills are invisible to the agent. There is no skill tool in any bundle in
args/agent_toolsets.yaml, none in builtin_tools.py, none in
tools/ace/agent_tools.py. Nothing injects skill names or descriptions into any
prompt. The `/skills` slash command prints names to the HUMAN operator. There is
no progressive disclosure because there is no disclosure at all — the whole
skills lifecycle (propose -> HITL approve -> write SKILL.md -> curate) produces
artifacts the model never sees.

Second defect: `skills_lifecycle.record_use()` has ZERO callers, so `use_count`
never increments and `last_activity_at` is only ever set at promotion time. The
curator archives on 30 days of "idle" — grading on a field nothing writes, so
every promoted auto-skill becomes archive-eligible 30 days after promotion
regardless of how often it was used.

BUILD
1. A `skills` tool exposing name + description only (cheap, always available),
   with a second call that loads a named skill's body on demand — progressive
   disclosure, so the prompt cost is bounded by what the agent actually opens.
2. Wire `record_use()` from the body-load path so the curator finally has real
   usage data.
3. Reuse `tools/skills/registry.py::load_registry()` and its committed cache;
   do not re-parse .agents/skills/ at turn time.
""",
        depends_on="hgx-sess-01",
        priority="high",
        acceptance="""
- An agent can list skills and load one body within a turn.
- record_use() increments use_count on body load.
- The name+description listing costs a bounded number of tokens independent of
  skill body size.
""",
    ),
    _t(
        "hgx-sess-03",
        "Fix qa_agent — its trust tier denies the tool it depends on",
        f"""{_CONTEXT}
PROBLEM — live defect.
`args/ace/roles/qa_agent.yaml` declares `trust_tier: yellow` and lists `run_tool`
in `agent_tools`. But `coworker_thread._trust_pre_hook` (:653-663) blocks
`_WRITE_EXEC_TOOLS = {{"write_file", "run_tool"}}` unless trust_tier is green.
So EVERY run_tool call qa_agent makes returns "Permission denied: 'run_tool'
requires green trust tier" — and its entire `icdev_tools` list is
`python tools/testing/...` commands that can only be invoked through run_tool.
The role cannot do its job.

Context: only 2 of 90 roles in args/ace/roles/ use `mode: agent` at all
(agent_developer, qa_agent); the other 88 default to fixed step mode.

BUILD
Decide and implement ONE of:
  (a) promote qa_agent to green — justified if its tools are read-only test
      execution, but note green also unlocks write_file; or
  (b) drop run_tool from agent_tools and give it a narrower, explicitly
      read-only test-execution tool that yellow may call.
Option (b) is preferred: it keeps the trust ladder meaningful.

Then add a startup validation that a role never declares a tool its trust tier
cannot call — this class of bug should be caught at load, not at runtime.
""",
        depends_on="hgx-sess-02",
        priority="high",
        task_type="bug",
        acceptance="""
- qa_agent can execute its declared test commands.
- A role declaring a tool its trust_tier forbids fails validation at load with a
  clear message.
- The trust ladder still blocks write/exec for non-green roles.
""",
    ),

    # ══════════════════════════════════════════════════════════════════
    # FED — wire the external MCP client
    # ══════════════════════════════════════════════════════════════════
    _t(
        "hgx-fed-01",
        "Wire the existing external MCP client into tool discovery and dispatch",
        f"""{_CONTEXT}
PROBLEM
ICDEV cannot consume tools from an external MCP server — although a complete
client already exists and is tested. `tools/mcp_client/` ships StdioTransport
(subprocess, warm process, JSON-RPC, initialize handshake) and HttpTransport
(bearer auth from env refs, destination checked by tools.http.egress_guard,
FAILS CLOSED if the guard cannot be imported); `ExternalToolRegistry` with
`ext__<server>__<tool>` namespacing, a per-server allowlist, a classification
ceiling enforced BEFORE dialing, an air-gap interlock that returns zero servers
regardless of config, and `sanitize.py` treating remote tool descriptions as
attacker-controlled prompt text. Stdlib-only by design, for air-gapped
procurement.

It has ZERO consumers outside tests/test_mcp_client.py:
- discovery.py names only three sources (MCP TOOL_REGISTRY, builtins, @tool).
- dispatch.py handles source in ("builtin","decorated",<mcp default>) — no ext__.
- No bundle in args/agent_toolsets.yaml lists an ext__ tool.
- args/external_mcp_servers.yaml is `enabled: false`, `servers: []`.

BUILD — this is WIRING, not building. Do not rewrite the client.
1. An `external` source in `discovery.build_registry()`.
2. An `ext__` branch in `dispatch.py`.
3. Both behind the existing `enabled: false` config, so nothing changes for any
   current deployment until an operator opts in.
4. Keep every control the module already has. In particular the sanitizer must
   run on descriptions before they reach a prompt, and the classification ceiling
   must be checked before the transport dials.
""",
        depends_on=GATE,
        priority="high",
        acceptance="""
- With enabled:false (the default) tool discovery is byte-identical to today.
- With a stub server enabled, its tools appear namespaced ext__<server>__<tool>
  and are callable through the normal dispatch path.
- A tool outside the per-server allowlist is refused; the classification ceiling
  is checked before dialing; air-gap returns zero servers.
""",
    ),

    # ══════════════════════════════════════════════════════════════════
    # OBS — observability and reflex dispatch
    # ══════════════════════════════════════════════════════════════════
    _t(
        "hgx-obs-01",
        "Make SAG runs observable",
        f"""{_CONTEXT}
PROBLEM
`tools/agent_runtime/` contains ZERO references to invocation_recorder, tracer,
span or trace_id. A SAG tool call is not recorded in `runtime_invocations` unless
it happens to route through the MCP unified server, and the agent loop emits no
spans of its own (it does carry a `correlation_id`, set at :976, so downstream
router spans can be joined).

Nothing is replayable either, and that is structural: `invocation_recorder`
stores argument KEY NAMES only, never values (a deliberate privacy choice, see
its :34-38), and `base_server.py:382` stores a result HASH, not the result.
`agent_loop_sessions` lives in a canvas-scoped DB with no join key to
runtime_invocations beyond an ambient session_id env var.

BUILD
1. Call `invocation_recorder.record()` from `dispatch.py` so SAG tool calls land
   in runtime_invocations alongside MCP ones, with surface="agent".
2. Emit a span per turn from the loop, keyed by the correlation_id it already
   carries, so a run joins to the router spans beneath it.
3. Argument values and tool results: record them ONLY behind an explicit,
   off-by-default flag, with the existing redaction applied. The current
   key-names-only design is a deliberate privacy decision — replay must be an
   opt-in widening, never a silent one.
""",
        depends_on=GATE,
        priority="high",
        acceptance="""
- `icdev runtime top --surface agent` shows SAG tool calls.
- A run's spans join to its correlation_id.
- With the flag off, no argument value or tool result is persisted anywhere.
""",
    ),
    _t(
        "hgx-obs-02",
        "Reconcile reflex dispatch — 34 reflex files never run",
        f"""{_CONTEXT}
PROBLEM
The self-improvement loop is largely not dispatched, in three ways:

1. `gepa_optimizer`, `reflexion_loop` and `evolution` are in
   `daemon.REFLEX_NAMES` but ABSENT from the `reflexes:` block of
   `args/genesis_config.yaml`, so they never run — despite gepa_optimizer's own
   docstring claiming "Runs every 24 hours via the genesis daemon".
2. ORANGE-tier reflexes return `{{"status": "awaiting_human_approval"}}` BEFORE
   importing the module (daemon.py:544-546), so `evolve` and `experiment` never
   execute their mutation code at all. Today they produce nothing, which reads
   identically to "ran and found nothing".
3. Drift: 121 reflex files, 87 names in REFLEX_NAMES, 82 in config. 34 files are
   never dispatched; 14 config entries are not in REFLEX_NAMES and so never
   dispatch either — including `quality`, `failure_triage` and `oracle_triage`.
   daemon.py:133 already documents one such case in a comment.

BUILD
1. A test asserting REFLEX_NAMES and the config `reflexes:` block agree — the
   same class of guard `tests/test_migration_version_uniqueness.py` provides for
   migrations. Grandfather the current gap explicitly if it cannot all be fixed
   at once, but do not leave it silent.
2. Add gepa_optimizer, reflexion_loop and evolution to config.
3. Decide explicitly what an ORANGE reflex should do: staging a reviewable
   proposal is useful; returning before import is not. Implement the decision.
""",
        depends_on="hgx-obs-01",
        priority="high",
        task_type="bug",
        acceptance="""
- A test fails if a name is in REFLEX_NAMES but not config, or vice versa,
  outside an explicit grandfather list.
- gepa_optimizer, reflexion_loop and evolution are dispatched on their cadence.
- ORANGE reflexes produce a reviewable artifact rather than an early return.
""",
    ),

    # ══════════════════════════════════════════════════════════════════
    # PAR / COND / AGENT — the graph runtime, inside Studio
    # ══════════════════════════════════════════════════════════════════
    _t(
        "hgx-par-01",
        "Parallel DAG dispatch in the Studio workflow runner",
        f"""{_CONTEXT}
PROBLEM
`tools/studio/workflow_runner.py::_resolve_dag` (:120-125) returns
`list(TopologicalSorter(graph).static_order())` — a FLATTENED linear list — and
`_worker` (:767) walks it with `for i, step in enumerate(ordered_steps)`. So
templates authored as fan-out execute serially. Example:
`args/workflow_templates/ai_ml_transformation.yaml:51-73` has coa_a/coa_b/coa_c
all depending on the same two steps and then a join — a textbook diamond, run one
at a time.

BUILD — mirror the proven implementation, do not invent one.
`tools/agent/team_orchestrator.py::execute_workflow` (:486-600) already does
wave-parallel DAG execution: `TopologicalSorter.prepare()` + `get_ready()` +
`done()` inside a ThreadPoolExecutor with `as_completed`, fan-in context built
from completed dependencies, `_block_downstream()` recursive cancellation, a
per-wave collaboration mailbox, and a global timeout. Decisions D36 (threads, not
asyncio — this is also why it is portable) and D40 (graphlib).

1. Replace static_order() with the prepare/get_ready/done loop + a bounded pool.
2. Concurrency from a `max_parallel:` key on the template, DEFAULT 1, so all 61
   existing templates (args/workflow_templates 42 + context/workflow_templates 19)
   stay byte-for-byte sequential.
3. Barrier/fan-in falls out of graphlib for free — a join is just a step with
   several depends_on. Do not add a barrier primitive.

CARE
- The per-run SSE `queue.Queue(maxsize=500)` and the `index`/`total` fields assume
  ordered emission. Emit a monotonic sequence number rather than list position.
- A `node_type: human` gate inside a parallel wave must park ONLY its own branch,
  not the pool. `_await_gate` blocks its thread by design.
- Step records are keyed by step_run_id, so DB writes are already safe.
""",
        depends_on=GATE,
        priority="high",
        acceptance="""
- Running all 61 templates before and after with max_parallel unset produces an
  IDENTICAL executed step order (capture and diff it).
- With max_parallel: 3, a diamond template's three independent branches overlap
  in time and the join waits for all three.
- A human gate inside a wave parks its branch; sibling branches continue.
- tests/studio/test_workflow_parallel.py added and green on both OSes.
""",
    ),
    _t(
        "hgx-cond-01",
        "Conditional edges and downstream cancellation",
        f"""{_CONTEXT}
PROBLEM
There are no conditional edges. Grepping workflow_runner.py for
condition/when/branch finds only docstring prose; `depends_on` is unconditional.
A failed non-approval step sets `overall_ok = False` and execution CONTINUES
(:860-878) — there is no route to a remediation step. Only a rejected or
timed-out human gate breaks the loop.

BUILD
1. An optional `when:` on a step, evaluated against the PREDECESSOR's recorded
   result. REUSE `tools/studio/automation_builder.py` CONDITION_OPERATORS and
   `evaluate_conditions` — the same DSL `studio_workflow_triggers.filter_json`
   already uses. The repo deliberately has no second rules DSL; do not add one.
2. A step whose `when` is false records the EXISTING `skipped` status with a
   reason (the status and the reason field both already exist).
3. Add `when` to `tools/studio/template_linter.py` validation and
   VALID_NODE_TYPES-adjacent schema docs.
4. Port `_block_downstream()` from team_orchestrator.py:882 so a failed required
   step cancels its descendants instead of letting them run against a broken
   precondition.

This is what enables fail -> remediation branch, which is the routing the source
docs ask for.
""",
        depends_on="hgx-par-01",
        priority="high",
        acceptance="""
- A step with a false `when` is skipped with a reason; its dependents still
  evaluate their own conditions.
- A failed required step cancels its descendants rather than running them.
- Templates with no `when` key behave exactly as before.
- tests/studio/test_workflow_conditional.py added and green.
""",
    ),
    _t(
        "hgx-agent-01",
        "Add node_type: agent to the Studio workflow runner",
        f"""{_CONTEXT}
PROBLEM
Studio steps can be a subprocess (`node_type: tool`), a registry tool
(`node_type: mcp`) or a gate (`human`/`approval`). Nothing runs an agent loop as
a node. `template_linter.VALID_NODE_TYPES` is exactly
{{"tool","human","approval","mcp"}}.

BUILD
`tools/studio/executors/agent_executor.py`, mirroring
`tools/studio/executors/mcp_executor.py` exactly: a subprocess taking
--step-id / --project-id / --run-id / --json, reading and writing run_memory.
It invokes `icdev.tools.llm.agent_loop.run_agent_loop` with the toolset from
hgx-exec-01 and a per-step `agent_tools:` allowlist resolved through
`tools/agent_runtime/toolsets.py::resolve_bundles` (which already hard-filters
the registry) and gated by `tools/agent_runtime/approval_gate.py`.

`_exec_step` (workflow_runner.py:310-336) already dispatches on node_type with a
clean seam — add a branch, do not restructure it.

Must take an `llm_function`, never a model name, and must catch
AgentLoopUnsupported and degrade with a recorded step reason rather than failing
the run.
""",
        depends_on="hgx-cond-01",
        priority="high",
        acceptance="""
- A template with node_type: agent runs the loop, writes artifacts to run_memory
  and records a normal step row.
- The step is limited to its declared agent_tools bundle.
- A non-tool-capable model degrades the step with a reason; the run continues.
""",
    ),
    _t(
        "hgx-agent-02",
        "Authorize agent-node tools with a default-deny gate",
        f"""{_CONTEXT}
PROBLEM / PRIOR ART
The source docs call per-node tool isolation the "#1 P0 security gap". It is
already implemented for mcp nodes: `mcp_workflow_tools` in
args/security_gates.yaml:1739 — `default: deny`, 16 read-only `allowed`, 13
`requires_approval` that dispatch ONLY after an approved `node_type: human` gate
in the same run, plus caller IL and role checks, NIST AC-3/AC-6/AU-2/AU-12/CM-5,
gate MCP-WF-001, every attempt written to append-only `studio_mcp_dispatch_audit`
with a params_sha256. That IS the Video-3 refund example.

BUILD
The same treatment for agent nodes: an `agent_workflow_tools` section modelled on
`mcp_workflow_tools`, `default: deny`, with its own gate id and block_on reasons,
auditing to studio_mcp_dispatch_audit. An agent node's tools must be a subset of
what the gate allows for that caller's IL and roles, and a mutating tool must
require an approved human gate in the same run exactly as mcp does.

Do NOT invent a parallel authorization mechanism — extend the existing one.
""",
        depends_on="hgx-agent-01",
        priority="high",
        acceptance="""
- An agent node cannot call a tool outside its allowlist; the refusal is audited.
- A mutating tool in an agent node blocks until its human gate is approved.
- A caller whose IL is below the tool's declared ceiling is refused.
""",
    ),

    # ══════════════════════════════════════════════════════════════════
    # CX — Cortex graph mode and headless entry
    # ══════════════════════════════════════════════════════════════════
    _t(
        "hgx-cx-01",
        "Add cortex.agent(mode='graph') and validate the mode argument",
        f"""{_CONTEXT}
PROBLEM — one feature and one latent bug.
`tools/cortex/api.py::agent()` accepts mode "auto" | "team" | "single". There is
no graph mode. And the dispatch is a single boolean at :869:
    use_team = mode == "team" or (mode == "auto" and bool(roles))
so ANY unrecognised mode — including "graph" — SILENTLY falls through to
single-agent execution. By contrast `reason()` validates its mode against
_REASON_MODES and raises ValueError (:635-639).

BUILD
1. Add `mode="graph"` and a `graph: Optional[dict]` parameter dispatching to
   `tools.studio.workflow_runner.start_run(...)`, returning a CortexResult whose
   data carries run_id, like team mode returns instance_id.
2. FIX THE BUG: validate mode against an _AGENT_MODES set and raise ValueError on
   an unknown value, matching reason().
3. The facade must keep its `__cortex_governed__` stamp —
   tests/cortex/test_api_governed.py asserts the facade set and the stamp. Assert
   the stamp, never facade object identity (importlib.reload mints new objects).
""",
        depends_on="hgx-agent-02",
        priority="high",
        acceptance="""
- cortex.agent(mode="graph", graph={...}) starts a Studio run and returns run_id.
- cortex.agent(mode="nonsense") raises ValueError instead of running a single
  agent.
- tests/cortex/test_api_governed.py still passes.
""",
    ),
    _t(
        "hgx-cx-02",
        "Expose the agent facade over REST and the client SDK",
        f"""{_CONTEXT}
PROBLEM
`tools/cortex/rest_v1.py` registers 15 endpoints; there is NO `/agent` endpoint —
the agent facade is reachable only in-process and through the MCP tool
`cortex_agent_launch`. `tools/cortex/client.py` has no `.agent()` method and no
`.reason()` method either (reason has an endpoint but no client method).

BUILD
1. `POST /cortex/api/v1/agent` following the existing `_cortex_api` decorator and
   `_governed` helper, with the same error mapping (GovernanceBlockedError -> 403,
   CortexAnalystError -> 422).
2. `.agent()` and `.reason()` on CortexClient, stdlib-only like the rest of it.
3. Extend `tools/cortex/intent_router.py` with a graph-shaped signal (sequential
   steps with conditions, explicit parallelism, named approval gates, a named
   workflow template). Keep requires_confirm=True for the agent intent — a graph
   launch must still be confirmed in chat.
""",
        depends_on="hgx-cx-01",
        priority="medium",
        acceptance="""
- POST /cortex/api/v1/agent launches team, single and graph modes with the same
  auth and scope enforcement as the other endpoints.
- CortexClient.agent() and .reason() work against a live dashboard.
- A graph-shaped message routes to the agent intent with requires_confirm true.
""",
    ),
    _t(
        "hgx-cx-03",
        "Make graph runs startable headlessly (CLI + write MCP tools)",
        f"""{_CONTEXT}
PROBLEM
A graph run cannot be started without the dashboard. `workflow_runner.py` has NO
CLI — no argparse, no __main__ block (the "if __name__" strings at :1306/:1359
are inside the generate_python_script code emitter). And of the four studio_*
MCP tools (studio_list_workflows, studio_tool_catalog, studio_list_templates,
studio_init_db) NONE starts or resumes a run.

For a harness agent this is the difference between a UI feature and a capability
an agent — or an air-gapped cron job — can invoke.

BUILD
1. An argparse CLI on workflow_runner.py: --start / --resume / --status, --json,
   consistent with the other tools/ CLIs.
2. `studio_run_start`, `studio_run_status`, `studio_run_resume` MCP tools
   registered in tools/mcp/tool_registry.py and gap_handlers.py.
3. Add the new tools to args/security_gates.yaml mcp_workflow_tools with the
   right tier — starting a run is state-changing.
4. Register the CLI commands in docs/reference/commands.md, and only after the
   file is committed (coherence_checker.check_doc_command_paths gates this).
""",
        depends_on="hgx-cx-02",
        priority="medium",
        acceptance="""
- `python tools/studio/workflow_runner.py --start <workflow_id> --json` starts a
  run and prints the run_id.
- studio_run_start/_status/_resume are callable through the MCP gateway and are
  authorized by the gate.
- coherence_checker --all --gate passes (documented commands exist).
""",
    ),

    # ══════════════════════════════════════════════════════════════════
    # GOAL — standing goals
    # ══════════════════════════════════════════════════════════════════
    _t(
        "hgx-goal-01",
        "Standing goals — module and migration",
        f"""{_CONTEXT}
PROBLEM
Standing goals are the ONE capability in the source docs that genuinely does not
exist anywhere: `grep -rl standing_goal tools/ args/` returns nothing, there is
no goal table in init_icdev_db.py or any migration, and commands.py has no /goal.

BUILD
`tools/agent_runtime/standing_goals.py` — a `StandingGoal` dataclass, a
`GoalStatus` enum (pending/active/paused/blocked/completed/cancelled) and a
`GoalManager` with create/get/list_active/list_for_context/activate/pause/
complete/block/cancel/update_progress/delete.

MIGRATION — scaffold it, never hand-number:
    python tools/db/migrate.py --create "sag_standing_goals"
That allocates a 14-digit UTC version directory. The 3-digit sequence is CLOSED
(tests/test_migration_version_uniqueness.py freezes LEGACY_VERSION_CEILING=341);
a hand-numbered 292_*.sql would be silently shadowed AND fail that test.

Follow the sag_* conventions already in the tree: all DB access via
get_connection() so RLS applies, tenant_id and user_id columns, a self-creating
_ensure_schema() (these tables are intentionally absent from conftest's
MINIMAL_ICDEV_SCHEMA), and degrade silently when the table is missing.
""",
        depends_on=GATE,
        priority="medium",
        acceptance="""
- Full CRUD + lifecycle transitions with invalid transitions rejected.
- Migration created via migrate.py --create with a timestamp version.
- With the table dropped, GoalManager degrades rather than raising.
""",
    ),
    _t(
        "hgx-goal-02",
        "/goal commands and system-prompt injection",
        f"""{_CONTEXT}
BUILD
1. `/goal` subcommands in `tools/agent_runtime/commands.py` — create, status,
   list, pause, resume, complete, block, cancel, clear. The registry there is
   data-driven (REGISTRY at :325-341) and unknown commands are absorbed rather
   than forwarded to the LLM, so add the handler and the /help text together.
2. Inject active goals into `runtime._effective_system_prompt()` in a compact
   form, capped (default 5) so a long goal list cannot crowd the window, and
   cache-invalidated on any /goal mutation.
3. Budget the injected block against the real context window from hgx-ctxw-01.

Also refresh the stale module docstring in commands.py while you are there: it
still describes /memory and /rollback as stubs and omits /skill, /search and
/snapshot.
""",
        depends_on="hgx-goal-01",
        priority="medium",
        acceptance="""
- /goal create then a new turn shows the goal in the system prompt.
- The cap holds with more goals than the limit.
- A mutation invalidates the cache within the same session.
- /help lists the goal commands; the docstring matches the registry.
""",
    ),
    _t(
        "hgx-goal-03",
        "Surface standing goals in chat context status",
        f"""{_CONTEXT}
BUILD
Add standing-goal state to the chat context payload via
`tools/dashboard/chat_manager.py::get_context_status()`, so a conversation shows
its active goals and their progress. Degrade silently when the goals table is
absent — chat must not break because an optional subsystem is not migrated.
""",
        depends_on="hgx-goal-02",
        priority="medium",
        acceptance="""
- get_context_status returns standing_goals for a context that has them.
- With the table absent the key is omitted and chat is unaffected.
""",
    ),

    # ══════════════════════════════════════════════════════════════════
    # EVAL / GOV / CFG / TMPL / DOC / VV
    # ══════════════════════════════════════════════════════════════════
    _t(
        "hgx-eval-01",
        "Per-node harness evaluation",
        f"""{_CONTEXT}
PROBLEM
`harness_eval` has exactly nine columns (id, task_id, reflex, decision,
confidence, metadata_json, actual_outcome, resolved_at, created_at) and
correlates by task_id only. There is no ALTER TABLE harness_eval anywhere. So a
graph node's decision cannot be scored independently, and the meta-harness cannot
tell a strong node from a weak one.

BUILD
1. A timestamp migration (migrate.py --create) adding NULLABLE run_id, node_id,
   node_type and edge_condition. Nullable keeps every existing query working.
2. `record_graph_node_decision()` beside the existing `record_decision()`.
3. Key `_AnomalyDetector` per node_type so a high-performing node is not
   penalised by a low-performing one's drift.

CRITICAL: mirror the columns into BOTH `tests/conftest.py::MINIMAL_ICDEV_SCHEMA`
and `tools/db/schema/pg_consolidated.sql`. A migration alone leaves the test
schema and every fresh PG install behind — that omission breaks only fresh
installs, which is the hardest failure mode to notice.
""",
        depends_on="hgx-agent-02",
        priority="medium",
        acceptance="""
- Existing harness_eval queries are unaffected (columns are nullable).
- Per-node precision/recall computable for a graph run.
- conftest and pg_consolidated.sql carry the new columns; a fresh PG bootstrap
  has them.
""",
    ),
    _t(
        "hgx-gov-01",
        "Per-node governance profiles",
        f"""{_CONTEXT}
PROBLEM
`tools/cortex/governance.py` GATE_ORDER (:79-87) is a hardcoded module tuple and
the pipeline is straight-line code. The only per-call dials are retrieval,
attach, ctx.fail_closed and ctx.trusted_content. A graph node doing internal
diligence pays the same seven gates as one emitting a customer-facing artifact.

BUILD
A `governance.profiles` block in `args/cortex_config.yaml` naming gate subsets;
`GovernancePipeline` resolves a profile; each Studio agent node may name one.
Default profile = all gates, so behaviour is unchanged for every existing caller.

NON-NEGOTIABLE: `output_redaction` and `provenance` remain non-skippable in EVERY
profile. They are the egress guarantee and the NIST-AU audit row respectively —
a profile that could drop them would turn a latency optimisation into a
compliance hole.
""",
        depends_on="hgx-eval-01",
        priority="medium",
        acceptance="""
- A node naming a minimal profile skips only the permitted gates.
- No profile can disable output_redaction or provenance; attempting it is a
  config error at load.
- Callers that name no profile behave exactly as today.
""",
    ),
    _t(
        "hgx-cfg-01",
        "Runtime config surface and component registration",
        f"""{_CONTEXT}
PROBLEM
There is no `args/agent_runtime.yaml` and no `tools/agent_runtime/config.py`.
Configuration is roughly twelve environment variables plus six unrelated YAML
files. And SAG is absent from `args/component_registry.yaml` entirely, so it is
invisible to the enable/disable CLI, nav, RBAC metadata and the coherence checks
that read the registry.

BUILD
1. `args/agent_runtime.yaml` + `AgentRuntimeConfig` loaded at AgentRuntime
   construction, with per-subsystem toggles. Existing env vars keep working and
   keep winning — this adds a layer, it does not replace one.
2. Register SAG in args/component_registry.yaml as `kind: core_extension`. Mirror
   registry changes to the root tools/ copy per CLAUDE.md.
""",
        depends_on="hgx-goal-03",
        priority="medium",
        acceptance="""
- AgentRuntime reads the config; env vars still override it.
- `icdev status` / `icdev list` show the runtime component.
- coherence_checker --all --gate passes.
""",
    ),
    _t(
        "hgx-tmpl-01",
        "Multi-angle review as a workflow template",
        f"""{_CONTEXT}
CONTEXT
This is the "orchestrator skill" the first source video describes — one skill
above the rest that fans review out to agents in isolated context windows and
synthesises the findings. Once hgx-par-01, hgx-cond-01 and hgx-agent-01 land it
needs ZERO new Python.

BUILD
`context/workflow_templates/multi_angle_review.yaml` — a diamond: one fan-out
step, then N parallel `node_type: agent` nodes each carrying a different review
lens (correctness, security, compliance, simplification) with its OWN tool
allowlist, then a single barrier synthesis node that depends on all of them.

The per-lens allowlists are the point: a security lens gets the scanners, a
simplification lens gets read-only tools. That is per-node capability scoping
doing real work, not a demo.
""",
        depends_on="hgx-gov-01",
        priority="medium",
        acceptance="""
- The template validates against template_linter.
- Running it fans out N agent nodes concurrently and the synthesis node waits for
  all of them.
- Each lens is confined to its declared allowlist.
- No new Python module was required.
""",
    ),
    _t(
        "hgx-doc-01",
        "Graph execution chat extension (031, not 040)",
        f"""{_CONTEXT}
BUILD
`tools/extensions/builtins/031_graph_execution_chat.py`, following the proven
`030_workflow_loop_chat.py` pattern: a `chat_message_after` hook injecting graph
run status every N turns with an advisory cooldown (030 uses
ADVISORY_COOLDOWN_TURNS = 8).

NOTE — the source docs specify 040. **040 is already taken** by
`040_bayesian_learning_chat.py`. Load order is a lexicographic sort of the
filename (extension_manager.py:381), so the number determines precedence. Free
slots: 031-039, 082-089, 091+.

Surface: which nodes are done, which are running, what a barrier is waiting for,
and which gate needs approval — plus the CLI command to act on it.
""",
        depends_on="hgx-tmpl-01",
        priority="medium",
        acceptance="""
- The extension loads in the documented order and does not shadow 040.
- A running graph produces a status advisory in chat, rate-limited by cooldown.
- With no active run it injects nothing.
""",
    ),
    _t(
        "hgx-doc-02",
        "Design-rule documentation and manifest refresh",
        f"""{_CONTEXT}
BUILD
1. `docs/patterns/loop-vs-graph-decision-tree.md` (the directory does not exist
   yet). Capture the rule the source videos actually agree on: if the steps are
   known ahead of time use a graph; if the task is open-ended start with a plain
   agent loop and only add structure when it earns its place; do not force a
   graph onto exploratory work; graphs cost materially more tokens because
   several agents run at once.
2. Refresh the stale docs found during this card's research:
   - `tools/agent_runtime/commands.py` module docstring — still calls /memory and
     /rollback stubs, omits /skill, /search, /snapshot.
   - `tools/manifest/standalone-agent-runtime.md` — same staleness.
   - `tools/manifest/icdev-studio-low-code-no-code-platform.md` — document
     max_parallel, `when`, and node_type agent.
3. Feature docs for the phases under docs/features/.
4. Run `python tools/dx/companion.py --sync --write --json` afterwards. Note it
   syncs skills, not guardrails, and it DELETES hand-authored AGENTS.md sections
   — check the diff before committing.
""",
        depends_on="hgx-doc-01",
        priority="medium",
        task_type="chore",
        acceptance="""
- The decision-tree doc exists and is referenced from the manifest.
- No manifest or docstring still describes a shipped command as a stub.
- Every documented command's file exists (coherence check_doc_command_paths).
""",
    ),
    _t(
        "hgx-vv-01",
        "End-to-end verification and portability proof",
        f"""{_CONTEXT}
VERIFY — the whole card, not one slice.

1. Backward compatibility: run all 61 templates before and after par/cond and
   diff the executed step order. With max_parallel unset the sequences must be
   IDENTICAL.
2. Regression net: `pytest tests/test_dwo_*.py tests/studio tests/test_agent_loop*.py`
   (381 DWO + 143 agent-loop tests). KNOWN pre-existing pollution:
   tests/test_workflow_hitl_engine.py fails ~20 tests when run alongside
   tests/studio and passes alone — verify against clean origin/main before
   blaming a change on this card.
3. Portability: the windows-latest job from hgx-port-02 is green, and the
   LF/CRLF round-trip test passes on both OSes.
4. Security: `python tools/integrity/engine.py --gate`;
   `python -m bandit -r tools/ --severity-level medium`; an agent node cannot
   reach a tool outside its allowlist; a requires_approval tool still blocks
   until its human gate is approved.
5. Gates: `ruff check .`;
   `python tools/workflow/coherence_checker.py --all --fix --gate`;
   `python tools/dx/companion.py --sync --write --json`.
6. E2E: chat -> intent router -> confirm -> graph run -> per-node SSE -> approval
   gate -> result delivery. Playwright over /studio/workflows; screenshots to
   playwright/screenshots/. Existing skill: e2e:dwo_workflow.
7. Confirm every changed tools/ module is mirrored to icdev/tools/.
""",
        depends_on="hgx-doc-02",
        priority="high",
        task_type="test",
        acceptance="""
- All of the above run and their results are recorded on the task.
- Any failure is either fixed or explicitly attributed to pre-existing state with
  evidence from clean origin/main.
""",
    ),
]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Seed the HGX kanban card")
    parser.add_argument("--json", action="store_true", help="machine-readable report")
    parser.add_argument("--dry-run", action="store_true", help="print, insert nothing")
    args = parser.parse_args(argv)

    if args.dry_run:
        if args.json:
            print(json.dumps(TASKS, indent=2))
        else:
            for t in TASKS:
                dep = t.get("depends_on_task_id", "-")
                print(f"  {t['id']:<16} [{t['status']:<11}] dep={dep:<14} {t['title']}")
            print(f"\n{len(TASKS)} tasks (not inserted)")
        return 0

    from tools.kanban.task_factory import create_tasks

    created = create_tasks(TASKS)
    report = {
        "created": created,
        "created_count": len(created),
        "submitted_count": len(TASKS),
        "skipped_existing": [t["id"] for t in TASKS if t["id"] not in created],
        "gated": True,
        "gate": GATE,
        "release": f"python tools/kanban/cli.py --set-status {GATE} done",
    }
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(f"Seeded {len(created)}/{len(TASKS)} HGX tasks")
        for tid in created:
            print(f"  + {tid}")
        if report["skipped_existing"]:
            print("  (already present: " + ", ".join(report["skipped_existing"]) + ")")
        print(f"\nMANUAL-ONLY. Release with:\n  {report['release']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

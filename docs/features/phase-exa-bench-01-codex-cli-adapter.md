# CUI // SP-CTI

# exa-bench-01 — the Codex CLI adapter

**Card:** EXA — External Adoption · **Epic:** BENCH — real harness adapters, then a
capability matrix
**Status:** shipped · **Date:** 2026-08-12

## Why

omnigent's value proposition is *many harnesses behind one seam*. ICDEV already has
the seam: `tools/agents/adapter_base.py` is a clean 5-method Protocol
(`available` / `prepare_prompt` / `invoke` / `detect_completion` /
`parse_response`) with a registry, `args/agent_adapters.yaml`, and real consumers
in `tools/genesis/reflexes/kanban.py`.

What it lacked was harnesses. Before this task the inventory was:

| Adapter | State |
|---|---|
| `claude_cli` | real — `spawn()` + `invoke()`, JSON envelope parsed |
| `local_agent` | real — rubric-gated, edits files, out of `fallback_order` |
| `local_llm_router` | real — prose only, single-shot, cannot edit files |
| `codex_cli` | **stub** — `invoke()` raised `NotInstalledError`, commented out of `enabled_adapters` |
| `copilot_cli` | stub — `available()` is `shutil.which("gh")` |

A stub is the card's own thesis in miniature: a *declared* capability with zero
consumption. This task makes `codex_cli` real.

## What shipped

`tools/agents/adapters/codex_cli.py` implements all five Protocol methods against
the OpenAI Codex CLI's non-interactive `codex exec` mode, reusing the three
answers `claude_cli` already worked out the hard way.

### 1. PATHEXT-aware discovery

```python
resolve_codex_cli(is_windows: bool | None = None) -> str | None
```

`$ICDEV_CODEX_CLI` (an explicit path *or* a bare name) → `shutil.which("codex")` →
`shutil.which("openai-codex")` (the legacy npm package) → a `~/.local/bin`
secondary probe tried with **each PATHEXT suffix**. The bare, suffix-less name
never exists on Windows; on the claude side, missing that quarantined 25 kanban
tasks as "no executor available" on 2026-08-01 before it was traced.

`is_windows` is an explicit parameter rather than an `os.name` read at the call
site, so **both** platform branches are exercised from either OS's test run.
Forcing the branch by patching `os.name` instead makes `pathlib` hand out a
`WindowsPath` on Linux, which raises on construction — that is not a hypothetical,
it is what the first draft of the test did and what the Linux container run
caught.

### 2. The prompt goes in over stdin

`codex exec … -` reads the prompt from stdin. It is written to a temp file with
`encoding="utf-8", newline=""` and piped in, because a real task prompt is far
past the Windows 32767-char command-line limit (WinError 206). `shell=False`,
`pathlib` throughout, and the file is unlinked in a `finally`.

### 3. The JSONL envelope is parsed

Codex has renamed its `--json` events across releases, so parsing matches on
fragments and handles **both** shapes:

* older — `{"msg": {"type": "agent_message", …}}`, `task_complete`,
  `exec_command_begin`/`_end`, `token_count`
* newer — `{"type": "item.completed", "item": {"item_type": …}}`,
  `turn.completed`, `thread.started`

Parsing is best-effort by design: a CLI build that printed plain text degrades to
treating stdout as the answer rather than losing it. Two details are deliberate:

* **`item.completed` is not task completion.** A bare `"complete" in type`
  substring test would call the run finished at the first assistant message.
  Completion matches task/turn/thread-level prefixes only.
* **`exec_command_begin` + `exec_command_end` is one tool call, not two.**

### Where it deliberately differs from `claude_cli`

**`structured` omits what Codex does not report.** `claude_cli` fills
`total_cost_usd` and `duration_api_ms` from its envelope. Codex reports neither,
so those keys are **absent, not zero** — a cross-adapter comparison has to be able
to tell "not reported" from "free". Token counts follow the same rule: present
only when the CLI actually emitted them.

**No `spawn()`.** `claude_cli.spawn()` exists because the kanban runner owns its
own poll/kill loop. Nothing dispatches Codex that way, and adding a second
execution mode with no consumer would be inventing a capability that
exa-bench-03's probe is supposed to *measure*.

## HGX acceptance criteria

**LLM-agnostic.** No model id appears in the module. The model is the operator's
choice — `session.metadata['model_id']` or `$ICDEV_CODEX_MODEL` — passed through
as `--model`; with neither set the CLI uses its own configured default. A test
AST-scans the module for a string bound to a model selector.

`AgentLoopUnsupported` is **not** caught here, and that is not an omission: it is
raised by `icdev.tools.llm.agent_loop` when a resolved provider cannot serve
native tool use, and this adapter runs a subprocess rather than that loop, so the
exception cannot reach it. Catching an exception that cannot be raised is dead
code that reads as coverage. The analogous degradation for a shellout is
implemented instead: `invoke()` **never raises for a backend failure**. An unknown
flag, a refused model, a non-zero exit and a timeout all come back as an
`AgentResult` with `completed=False` and the CLI's own stderr in `error`. Only a
genuinely absent CLI raises `NotInstalledError`, which is the Protocol's contract.

**OS-agnostic.** `pathlib` throughout, `encoding="utf-8"` and `newline=""` on
every handle, `shell=False`, no literal path separator, and the single platform
branch is two-sided and parameterised. Every optional CLI flag (`--sandbox`,
`--skip-git-repo-check`, `extra_args`) is opt-out-able through session metadata,
because the Codex surface has moved across releases and an operator on an older
build must be able to adapt without a code change.

## Selection: enabled, but nothing routes to it

`args/agent_adapters.yaml` moves `codex_cli` out of the commented-out block into
`enabled_adapters`, and **deliberately leaves it out of `fallback_order` and
`per_task_type_preference`** — the same treatment `local_agent` gets. It is
reachable via `ICDEV_AGENT_ADAPTER=codex_cli`; nothing routes to it by default
until the exa-bench-03 capability probe has measured it. Two tests pin this: one
asserts it is absent from `fallback_order`, one asserts selection for an existing
consumer is unmoved when it is enabled.

The packaged default at `icdev/data/args/agent_adapters.yaml` is **not** changed:
that file seeds newly scaffolded projects, and an unmeasured adapter does not
belong in a new project's default.

## Verification

`tests/test_codex_cli_adapter.py` — 51 tests, hermetic (no `codex` binary is ever
spawned; resolution is patched and `subprocess.run` is a recorder).

```
Windows 11 / Python 3.13   51 passed in 0.74s
Linux    / Python 3.11     51 passed in 4.38s   (docker python:3.11-slim)
```

Added to **both** CI allowlists — `args/ci_test_files/core.txt` (ubuntu) and
`args/ci_test_files/windows.txt` — because the acceptance criterion is a
portability claim, and a portability claim proven on one OS is not proven.

## Follow-on

* **exa-bench-02** — the same treatment for `copilot_cli`.
* **exa-bench-03** — the capability probe. It reports declared-versus-actual per
  adapter (streaming, tool-calling, sub-agents, interruption, sandbox
  passthrough, context budget) and decides when `codex_cli` joins
  `fallback_order`. Honest inputs for it here: no `spawn()`, no streaming
  surfaced to callers, sandbox passed through as `--sandbox`, cost not reported.
* **exa-bench-04** — the separate, larger question: `claude_cli` invokes Claude
  Code with `--dangerously-skip-permissions`.

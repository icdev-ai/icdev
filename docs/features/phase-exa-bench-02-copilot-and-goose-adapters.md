# CUI // SP-CTI

# exa-bench-02 — the Copilot CLI adapter, and Goose

**Card:** EXA — External Adoption · **Epic:** BENCH — real harness adapters, then a
capability matrix
**Status:** shipped · **Date:** 2026-08-12

## Why

omnigent's value proposition is *many harnesses behind one seam*. ICDEV already has
the seam — `tools/agents/adapter_base.py` is a clean 5-method Protocol with a
registry, `args/agent_adapters.yaml`, and real consumers in
`tools/genesis/reflexes/kanban.py`. What it lacked was harnesses. exa-bench-01
made `codex_cli` real; this task does `copilot_cli` and adds the harness both
omnigent and buzz integrate and ICDEV had no adapter for at all: **Goose**.

| Adapter | Before exa-bench-02 | After |
|---|---|---|
| `claude_cli` | real — `spawn()` + `invoke()`, JSON envelope | unchanged |
| `local_agent` | real — rubric-gated, edits files | unchanged |
| `local_llm_router` | real — prose only, single-shot | unchanged |
| `codex_cli` | exa-bench-01's deliverable (separate branch) | unchanged here |
| `copilot_cli` | **stub — could never report available** | real |
| `goose_cli` | **did not exist** | real |

## The bug, and why it survived

```python
def available(self) -> bool:
    return False and (shutil.which("gh") is not None)
```

`False and …` short-circuits, so the right-hand side was never evaluated. The
adapter was DECLARED — registered in `registry._ensure_loaded`, listed in the tool
manifest — and inert. It is the card's own thesis in miniature.

**Both halves are wrong, and the second is the interesting one.** `shutil.which("gh")`
is not a probe for this harness. As of `gh` 2.86 `gh copilot` is a *launcher*: if
the Copilot CLI is not installed, **it downloads it**. So `gh` being present means
"this host could go and fetch a harness", which is not what `available()` asks —
and the Protocol is explicit that the check is cheap and local. Had the
short-circuit simply been deleted, `pick_default()` would have started handing back
an adapter whose first act is a network install.

It survived because it is invisible to ordinary testing: on a runner with no
Copilot CLI, "correctly reports absent" and "hardcoded to absent" produce the same
output. Only an assertion about the *code* separates them, which is why the gate
below is an AST check.

## What shipped

### 1. `tools/agents/adapters/copilot_cli.py`

All five Protocol methods against the Copilot CLI's programmatic mode.

**Discovery.** `$ICDEV_COPILOT_CLI` (an explicit path *or* a bare name) →
`shutil.which("copilot")` (PATHEXT-aware, which is what the npm `@github/copilot`
shim needs) → `~/.local/bin` → **the directory `gh copilot` downloads the CLI to**
(`%LOCALAPPDATA%/GitHub CLI/copilot` on Windows, `$XDG_DATA_HOME/gh/copilot` or
`~/.local/share/gh/copilot` elsewhere), each tried with every PATHEXT suffix and
handled both as a directory and as the binary itself, because which of the two
`gh` writes has moved across releases. A binary `gh` *already fetched* is
installed and counts; `gh` alone does not.

**The prompt goes in over stdin**, from a temp file opened with
`encoding="utf-8", newline=""`. `-p`/`--prompt` is deliberately **not** used: the
vendor documents that piped input is *ignored* when `-p` is given, so passing both
would silently truncate a real task to whatever fit inside the Windows 32767-char
argv limit.

**Auto-approval is off by default.** `--allow-all-tools` removes the confirmation
prompt for every tool. `claude_cli` passes the analogous
`--dangerously-skip-permissions` unconditionally, and **exa-bench-04 exists because
that is an open question, not a settled answer** — so a new adapter must not add a
second instance of it. It is opt-in per session (`metadata['allow_all_tools']`) or
per host (`$ICDEV_COPILOT_ALLOW_ALL`), and the narrower `--allow-tool`,
`--deny-tool` and `--add-dir` knobs pass through so an operator can grant exactly
what a task needs. This is *not* the same choice as `codex_cli`'s
`--sandbox workspace-write` default: that is a **confinement** (what the agent may
touch); this would be the removal of a **confirmation** (whether anyone is asked).

`--no-ask-user` **is** on by default — stdin holds the prompt, so an agent that
paused for clarification would read EOF and hang until the session timeout.
`--share-gist` is deliberately not wired: it publishes the transcript as a GitHub
gist, and an adapter that can be handed a CUI prompt does not get a
one-metadata-key path to publishing it. `--secret-env-vars` *is* wired, since
redacting credential values out of agent output is squarely aligned with the
repo's redaction posture.

**Programmatic mode is plain text — there is no JSON envelope.** So there are no
token counts, no cost, no tool-call list and no diff. Those keys are **absent
rather than zero**, and `structured['machine_readable'] is False` tells the
exa-bench-03 probe which of "the harness does not report it" and "this adapter did
not parse it" it is looking at. `parse_response` does **not** mine a fenced
```` ```diff ```` block out of prose: that would be a patch *described*, not a
patch applied, and would give the comparison a column copilot has not earned.

### 2. `tools/agents/adapters/goose_cli.py`

The task said to add a Goose adapter *if the CLI can be resolved on this platform*.
It can — `goose 1.28.0` at `~/.local/bin/goose.EXE` — so this adapter was written
against a CLI that actually runs, and the parts a stub would guess at are
**measured**. The envelope in the docstring and in the test fixture is the verbatim
stdout of a live `goose run --output-format json`:

```json
{"messages": [{"id": null, "role": "user", "created": 1786535179,
               "content": [{"type": "text", "text": "..."}]},
              {"id": "b989f2c0-…", "role": "assistant",
               "content": [{"type": "text", "text": "OK"}]}],
 "metadata": {"total_tokens": 6, "status": "completed"}}
```

Command: `goose run --no-session --quiet --output-format json --max-turns N -i -`,
with instructions piped from a temp file. Goose gives two things its siblings do
not: `--max-turns` maps straight onto `AgentSession.max_turns`, and `--no-session`
is the vendor's own "useful for automated runs" switch, so a dispatched task leaves
no session file behind.

Three findings from running it, each of which is a line of code:

1. **The banner is on stdout.** Goose prints its ASCII banner and `goose is ready`
   *before* the JSON, so a parser that assumes stdout starts with `{` gets nothing.
   `--quiet` suppresses it and is the default here, but the parser scans for the
   first *balanced* JSON object anyway — brace counting is string-aware, because
   agent text routinely contains braces.
2. **A misconfigured Goose panics.** With no model configured it aborts with a Rust
   panic on stderr, **exit 101**, and nothing on stdout. That is a reported
   `completed=False`, never a raise.
3. **`tool_calls` can honestly be zero while tools ran.** With a provider that
   executes tools inside its own loop (`--provider claude-code`), Goose never sees
   a tool request, so none appear in `messages`. exa-bench-03 should read the count
   as "tool calls Goose *mediated*", not "tool calls that happened".

`--system` exists — more than `codex_cli` or `claude_cli` offer — and is
deliberately unused: it puts the text on argv, and system-plus-task is exactly the
size that trips WinError 206. Both go over stdin; `extra_args` is the escape hatch.
An unknown `status` is reported **verbatim** rather than forced into a vocabulary
this adapter would have invented; only `completed` means complete.

### 3. `tests/test_agent_adapter_no_inert_stubs.py` — the criterion becomes a gate

"No new always-unavailable stubs are introduced" is an acceptance criterion, and a
criterion with nothing behind it is itself a declared-but-unconsumed capability. So
it is a test. For every adapter in `enabled_adapters`:

* `available()` may not contain a **constant inside a boolean operator** (the
  short-circuit), and
* may not be **constant-valued** — every path yielding the same literal means the
  answer is baked in. A bare `return False` *as one guarded branch among several*
  is fine: `local_agent` returns `False` three ways and `True` once and every one
  depends on the host. That distinction is not cosmetic — the first draft of this
  rule failed `local_agent`, and the rule was wrong, not the adapter.
* `invoke()` may not be an unconditional raise.

Scoping it to `enabled_adapters` needs **no grandfather list** and maintains
itself: an adapter joins the gate the moment someone enables it, and a module that
is still openly a stub can sit in the tree commented out of the config — which is
what `codex_cli` does until exa-bench-01 lands. The gate carries **positive
controls** in both directions (it must reject the historical stub *and* accept
`local_agent`'s shape), because a rule that quietly stopped matching anything would
otherwise look exactly like a clean run.

## Cursor and Aider: the decision, recorded

The task named Goose, Cursor and Aider as absent from the tree. Goose resolved and
was implemented. **Cursor (`cursor-agent`) and Aider (`aider`) resolve on neither
PATH nor `~/.local/bin` on this platform, and no adapter for either was added.**

That is the deliberate outcome, not an omission. Writing one now would mean
guessing a flag surface, a completion signal and an output schema for a CLI that
cannot be run to check any of them — and shipping the result as a module whose
`available()` returns False on every host in the fleet. That is precisely the
declared-but-inert shape this task removed, and the new gate would have to be
weakened to let it in. The bar for adding either is the one Goose met: **the CLI
resolves somewhere we can run it, so the envelope can be measured rather than
guessed.** Aider is the closer of the two — it has a stable `--message` /
`--yes-always` non-interactive surface — and is the natural next candidate once a
host has it.

## Acceptance criteria

| Criterion | Where |
|---|---|
| `copilot_cli` implements the Protocol with a correct `available()` | `tools/agents/adapters/copilot_cli.py`; `test_the_short_circuit_stub_is_gone`, `test_gh_on_path_does_not_count_as_the_harness_being_installed`, `test_a_cli_gh_already_downloaded_does_count` |
| a Goose adapter is added **or** its absence documented | added — `tools/agents/adapters/goose_cli.py`, verified live against goose 1.28.0 |
| no new always-unavailable stubs | `tests/test_agent_adapter_no_inert_stubs.py`, with positive controls; Cursor/Aider recorded above instead of stubbed |
| LLM-agnostic | no model id **or provider name** in either module — AST-scanned per module. Model and provider come from `metadata` or `$ICDEV_{COPILOT,GOOSE}_MODEL` / `$ICDEV_GOOSE_PROVIDER`; with neither set each CLI uses its own default. `invoke()` never raises for a backend failure — unknown flag, refused model, missing token, non-zero exit, Rust panic and timeout all return `completed=False` with the CLI's own stderr. Only a genuinely absent CLI raises, which is the Protocol's contract |
| OS-agnostic | `pathlib` throughout, `encoding="utf-8"` + `newline=""` on every handle, `shell=False`, no literal path separator, and the single platform branch is two-sided and parameterised (`is_windows`) so **both** sides run from either OS |

`is_windows` is an explicit parameter rather than an `os.name` read at the call
site for the reason exa-bench-01 found the hard way: patching `os.name` to force
the branch makes `pathlib` hand out a `WindowsPath` on Linux, which raises on
construction.

## Verification

`tests/test_copilot_cli_adapter.py` (52), `tests/test_goose_cli_adapter.py` (55),
`tests/test_agent_adapter_no_inert_stubs.py` (19) — **126 tests**, hermetic: no
`copilot` or `goose` binary is ever spawned, resolution is patched or a file is
planted in `tmp_path`, and `subprocess.run` is a recorder.

```
Windows 11 / Python 3.13   126 passed in 1.79s
Linux    / Python 3.11     126 passed in 14.36s   (docker python:3.11-slim)
```

Added to **both** CI allowlists — `args/ci_test_files/core.txt` (ubuntu) and
`args/ci_test_files/windows.txt` — because the acceptance criterion is a
portability claim, and a portability claim proven on one OS is not proven.

Beyond the hermetic suite, `goose_cli` was driven **end to end against the real
CLI** — the adapter built the argv, spawned goose, and parsed the result:

```
resolve_goose_cli():  C:/Users/…/.local/bin/goose.EXE
ADAPTER.available():  True
argv:  goose.EXE run --no-session --quiet --no-profile --output-format json
       --max-turns 3 --provider … --model … -i -
completed: True   exit_code: 0   output: 'OK'
structured: {"status": "completed", "task_complete": true, "is_error": false,
             "messages": 2, "turns": 1, "tool_calls": 0, "total_tokens": 6,
             "final_message": "OK"}
```

The same run is the proof of the copilot fix from the other direction:
`registry.detect_available()` returned `['claude_cli', 'local_llm_router',
'local_agent', 'goose_cli']` on a host that **has `gh` installed** — `copilot_cli`
correctly reports absent, which under the old stub it also did, for the wrong
reason and on every host.

## Selection: enabled, but nothing routes to them

`args/agent_adapters.yaml` moves `copilot_cli` out of the commented-out block and
adds `goose_cli`, and **deliberately leaves both out of `fallback_order` and
`per_task_type_preference`** — the same treatment `local_agent` and `codex_cli`
get. Both are reachable via `ICDEV_AGENT_ADAPTER=<name>`; nothing routes to them by
default until the exa-bench-03 capability probe has measured them. Tests pin this:
absent from `fallback_order`, and selection for an existing consumer is unmoved.

The packaged default at `icdev/data/args/agent_adapters.yaml` is **not** changed:
that file seeds newly scaffolded projects, and an unmeasured adapter does not
belong in a new project's default.

## Honest inputs for exa-bench-03

| | `copilot_cli` | `goose_cli` |
|---|---|---|
| machine-readable envelope | **no** — plain text | yes — `--output-format json` |
| token counts | not reported | `total_tokens` only (no in/out split) |
| cost | not reported | not reported |
| tool calls surfaced | no | count of Goose-*mediated* requests; zero when the provider runs its own loop |
| diff surfaced | no | no |
| streaming surfaced to callers | no | no (`stream-json` exists, unused) |
| sub-agents | not exposed | not exposed (`summon` extension, unused) |
| interruption / `spawn()` | no | no |
| turn budget honoured | no flag for it | yes — `--max-turns` |
| native system prompt | no — prepended | yes (`--system`), unused: argv limit |
| sandbox / permission passthrough | `--allow-tool` / `--deny-tool` / `--add-dir`, auto-approval **off** by default | via Goose's own extension config |

## Follow-on

* **exa-bench-03** — the capability probe. The table above is its input.
* **exa-bench-04** — `claude_cli` invokes Claude Code with
  `--dangerously-skip-permissions`. `copilot_cli` deliberately does not follow it.
* **Aider** — the next adapter candidate, once a host has the CLI to measure.

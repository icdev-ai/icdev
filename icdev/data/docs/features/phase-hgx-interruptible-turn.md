# Phase HGX — Interruptible Turns

**Task:** `hgx-ctxw-03`. **Card:** HGX — Harness Agent Parity and Graph Runtime
(`args/projects.yaml`, MANUAL-ONLY, gated on `hgx-gate-00`).
**Governing rule:** extend the existing surface — no second loop, no second
cancellation mechanism. The token already existed; nothing set it and nothing
watched it closely enough.

## The problem

There was no way to stop a running turn. Four separate defects, each of which
alone was enough to make Ctrl-C useless:

1. **`stop_event` was read only at turn boundaries** (`agent_loop.py`, top and
   bottom of the turn body). A turn that had already started its LLM call or its
   tools could not be stopped until that work finished on its own — up to
   `llm_call_timeout_seconds` (120s) plus `tool_timeout_seconds` (30s) per tool.
2. **Started futures cannot be cancelled, and teardown re-blocked on them.**
   `Future.result(timeout=…)` is one uninterruptible sleep, and the loop's
   `with ThreadPoolExecutor(...) as executor` block calls
   `shutdown(wait=True)` on the way out — so even a boundary-detected stop
   blocked until every abandoned tool thread finished.
3. **`AgentRuntime._stop` was never set by anything.** It was constructed, handed
   to `run_agent_loop` as its `stop_event`, and then referenced nowhere else.
   There was no `AgentRuntime.stop()`.
4. **The REPL's turn body was wrapped in `except Exception`.**
   `KeyboardInterrupt` is a `BaseException`, so it sailed straight past — Ctrl-C
   during a turn killed the whole process, losing the session with it.

## What changed

### 1. The loop cancels mid-wait, not just at boundaries

`icdev/tools/llm/agent_loop.py`:

- **`_await_future(fut, timeout, stop_event)`** replaces every
  `fut.result(timeout=…)` in the turn body — the LLM call, the parallel
  read-only tools, and the timed sequential tools. It waits in
  `_STOP_POLL_SECONDS` (0.2s) slices and raises `AgentLoopStopped` as soon as
  the token is set. The caller's real timeout is still honoured exactly: the
  deadline is computed once, and a slice expiring is distinguished from the
  deadline elapsing.
- **`AgentLoopStopped`** is internal. Every wait site catches it and converts it
  into the existing clean exit — `truncated=False`,
  `result_subtype=error_stop_event`, `truncation_reason="stop_event"`. It is
  never propagated to a caller.
- **`_tool_executor(max_workers, stop_event)`** replaces the bare
  `with ThreadPoolExecutor(...)`. On a cancelled run it calls
  `shutdown(wait=False, cancel_futures=True)`, so leaving the loop does not
  rejoin the threads the cancellation just abandoned.
- **A sequential tool queued behind a stop is never started.** This is the
  cheapest boundary and the most valuable one — it is where a mutating tool
  would otherwise have run.
- **Every abandoned or skipped call still gets a `tool_result`**
  (`_STOPPED_TOOL_RESULT`). An unanswered `tool_use` block is a protocol error
  on resume, so a stopped turn would otherwise poison the session it was trying
  to preserve.
- **A third boundary check**, after the turn's checkpoint save and *before* the
  budget and circuit-breaker checks. Order matters: abandoned calls are recorded
  as errors, so without this a stop with `max_consecutive_errors=1` would be
  reported to the operator as `error_consecutive_tool_failures` — "your tools
  are broken" instead of "you pressed Ctrl-C".

`stop_event=None` takes none of this: `_await_future` short-circuits to a plain
`fut.result(timeout=…)` and the executor shuts down with `wait=True` as before.

### 2. `AgentRuntime` can be stopped

`icdev/tools/agent_runtime/runtime.py`:

| Member | Purpose |
|---|---|
| `stop()` | Set the token. Thread- and signal-safe — it only sets a `threading.Event`. |
| `clear_stop()` | Re-arm for the next turn. |
| `stopping` | Is a stop pending? |
| `turn_active` | Is a turn executing? (the SIGINT handler's discriminator) |
| `stop_event` | The token itself — the same object the loop and every tool handler receive. |

`clear_stop()` is deliberately **not** called at the start of `run_turn`. A
caller that does `runtime.stop()` then `runtime.run_turn(...)` must get a turn
that exits at its first boundary, not one that silently ignores the stop; the
REPL clears the token after each turn instead.

`stream_turn` checks the token at each streamed chunk and records whatever
arrived before the stop.

### 3. Ctrl-C stops the turn, not the process

`install_interrupt_handler(runtime, output_fn)` is a context manager wrapped
around the REPL body (and around `icdev chat -q`). It uses
`signal.signal(signal.SIGINT, handler)` — **the one signal API that behaves the
same on Windows and POSIX**. No `SIGBREAK`, no `SIGKILL`, no process groups, no
`loop.add_signal_handler` (Unix-only). Windows delivers the interrupt to the
**main thread only**, which is exactly why the handler's job is to set a
`threading.Event` rather than to count on a worker seeing an exception.

Three cases:

| When | What happens |
|---|---|
| No turn running | Re-raise `KeyboardInterrupt`. Ctrl-C at the prompt still means "leave the REPL" — unchanged. |
| First Ctrl-C during a turn | Set the token, print `^C stopping the current turn... (Ctrl-C again to force)`. The turn unwinds at its next boundary. |
| Second Ctrl-C, same turn | Re-raise, escalating past a handler that is ignoring the token. `_repl` catches it, so even this returns to the prompt. |

It degrades to a no-op when `signal.signal` raises — which is the normal case
off the main thread, i.e. an embedded or test-driven REPL — and restores the
previous disposition on the way out, so embedding the REPL leaves no handler
behind.

The REPL now catches `KeyboardInterrupt` explicitly (`except Exception` never
could), reports a stopped turn with its partial content, and re-arms the token.
`icdev chat -q` gets the same treatment and exits `130` (128 + SIGINT) with a
`"stopped": true` field in `--json` output.

### 4. Handlers are told to poll the token

`dispatch.py` already injected `stop_event` into any handler whose signature
declares it; what was missing was the *contract*. The agent-loop module
docstring, the `stop_event` argument docs and the dispatch module docstring now
state it plainly: **a handler that declares `stop_event` is expected to poll
it** — in any loop, before each subprocess launch, and between phases of a long
job.

This is cooperative by necessity. Python cannot kill a thread. The loop stops
*waiting* on a handler the moment the token fires, so a rogue handler can no
longer hang a turn — but it does keep a worker thread (and can delay process
exit) until it finishes on its own.

The two mutating built-ins were declaring the token and discarding it
(`del stop_event`). Both now check it before they start: `write_file` will not
keep mutating the tree after a stop, and `run_command` will not launch another
child process. `run_command` cannot interrupt a child once `subprocess.run` owns
it — that call is bounded by its own timeout — and says so.

## Verification

`tests/test_agent_loop_cancellation.py` (9) and
`tests/agent_runtime/test_interrupt.py` (20). Both run green on **Windows**
(Python 3.14) and **Linux** (Docker, `python:3.12-slim`).

The wall-clock tests are the ones that matter: a handler that blocks for 30s and
ignores the token, cancelled from another thread, must let `run_agent_loop`
return in under 10s — timed across the whole call so it covers executor teardown
as well as the wait. Reverting `agent_loop.py` to its pre-change state makes
that test hang on `fut.result(timeout=30)` until the suite's own timeout fires,
which is the regression in one line.

`tests/agent_runtime/test_interrupt.py::TestAcceptanceCriterionOne` drives the
real `AgentRuntime.loop` through a real `run_agent_loop` turn against a blocking
provider and delivers a genuine SIGINT from another thread.

Both acceptance criteria were also confirmed against a **separate process** with
a real OS-delivered console interrupt — `CTRL_C_EVENT` to a
`CREATE_NEW_PROCESS_GROUP` child on Windows, `SIGINT` on Linux. In both cases
the child printed `Turn stopped.`, returned to the `icdev>` prompt, accepted
`/exit` and exited `0`:

| Platform | SIGINT → turn reported stopped | Provider call length | Exit code |
|---|---|---|---|
| Windows 11 | 0.17s | 8s | 0 |
| Linux (container) | 0.21s | 8s | 0 |

## Known limits

- **An abandoned thread is abandoned, not killed.** `concurrent.futures`
  registers an atexit hook that joins its workers, so a handler ignoring the
  token can still delay *process* exit long after it stopped delaying the
  *turn*. This is why the polling contract is documented rather than optional.
- **`run_agent_loop(..., llm_call_timeout_seconds=0)`** calls `router.invoke`
  inline rather than through the executor, and that call is not interruptible.
  The shipped default is 120s (`args/llm_config.yaml`
  `agent_loop.budgets.llm_call_timeout_seconds`), so the interruptible path is
  the one every caller gets unless it explicitly opts out of timeouts.
- **`run_command` cannot interrupt a running child**, only decline to start a
  new one. Making the child killable means teaching
  `tools/skills/invoke.py::run_command` to use `Popen` + poll, which is a wider
  blast radius than this task and belongs to whoever needs it.

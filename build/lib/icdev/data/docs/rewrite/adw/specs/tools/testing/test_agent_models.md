# Spec: `tools/testing/test_agent_models.py`

_OPT-75 Phase 1 clean-room spec._

## Purpose

A small parallel CLI that pings every Claude Code model tier (`opus`,
`sonnet`, `haiku`) with a fixed test prompt, captures the response,
and reports a single overall pass/fail. Used by the testing framework
to confirm the model wiring before running a real workflow.

## CLI

```
python tools/testing/test_agent_models.py
```

Exit codes: `0` if every model returns success, `1` otherwise.

## Constants

* `MODELS = ["opus", "sonnet", "haiku"]` — preserved verbatim.
* `TEST_PROMPT` — a multi-line literal asking the model to confirm
  receipt, name itself, and say "Test successful!". The exact wording
  is preserved so historical comparisons of recorded outputs still
  match.

## Public functions

### `test_model(model: str, run_id: str) -> tuple[bool, str]`
Build an `AgentPromptRequest` for the model with the test prompt and a
per-model output file under
`<repo>/agents/<run_id>/agent_test_<model>.jsonl`. Call
`tools.ci.modules.agent.prompt_claude_code(request)`. Return
`(True, "<model>: Success")` on `response.success`, or
`(False, "<model>: <error>")` otherwise. Any exception is converted
into a `(False, "<model>: Exception: <text>")` result.

### `main()`
Generate a fresh `run_id` via `make_run_id()`, run the three models in
parallel via `ThreadPoolExecutor(max_workers=len(MODELS))`, collect
the results, print a summary block, and exit `0` or `1`.

## Forbidden

* No DB writes.
* No network calls except the underlying Claude CLI invocation.

## Acceptance

Tests cover:

* `MODELS` and `TEST_PROMPT` constants exposed at module level.
* `test_model` returns the success tuple on a happy response.
* `test_model` converts a failed response into the expected tuple.
* `test_model` swallows exceptions raised by `prompt_claude_code`.
* `main` exits 0 when every model succeeds (mocked).
* `main` exits 1 when at least one model fails (mocked).

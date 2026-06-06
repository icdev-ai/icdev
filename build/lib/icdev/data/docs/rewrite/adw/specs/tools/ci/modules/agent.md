# Spec: `tools/ci/modules/agent.py`

_OPT-75 Phase 1 clean-room spec._

## Purpose

A thin wrapper around the Claude Code CLI that the workflow scripts use
to invoke slash-command "templates". Picks the appropriate model per
slash command, writes the rendered prompt to the per-run agent
directory, delegates to the robust executor under
`tools.agent.agent_executor` (with retry, token tracking, audit) when
available, and falls back to a direct subprocess invocation when it
isn't.

## Module-level constants

### `BOT_IDENTIFIER = "[ICDEV™-BOT]"`
Prefix used by `format_issue_message` to mark agent-authored comments
so the workflow doesn't loop on its own posts.

### `SLASH_COMMAND_MODEL_MAP: dict[str, str]`
Maps each known slash command to a Claude model tier
(`"haiku" | "sonnet" | "opus"`). Must contain at least:

* Classification (haiku): `/classify_issue`, `/classify_workflow`,
  `/generate_branch_name`
* Planning (sonnet/opus): `/icdev-init`, `/icdev-build`,
  `/icdev-comply`, `/icdev-deploy`, `/bug`, `/feature`, `/chore`,
  `/patch`
* Testing (sonnet): `/icdev-test`, `/test`, `/test_e2e`,
  `/resolve_failed_test`, `/resolve_failed_e2e_test`
* Review/security: `/icdev-review`, `/review`, `/icdev-secure`,
  `/icdev-status`, `/icdev-monitor`, `/icdev-knowledge`
* Documentation: `/document`
* Git ops: `/commit`, `/pull_request`
* `/implement` → opus

The exact mapping is preserved verbatim — callers don't depend on the
table directly but it's behavioural ABI.

## Public functions

### `prompt_claude_code(request: AgentPromptRequest) -> AgentPromptResponse`

1. Read the configured timeout via `_get_timeout()` (which reads
   `args/cicd_config.yaml` for `cicd.executor.default_timeout_seconds`
   plus per-command overrides; defaults to 300s).
2. Try to import the robust executor
   (`tools.agent.agent_executor.execute_agent` +
   `tools.agent.agent_models.AgentPromptRequest`). If available, map
   the CI request to the robust request and call `execute_agent(...,
   max_retries=3)`. Map the robust response back to
   `AgentPromptResponse(output, success, session_id, duration_ms)`.
3. On `ImportError`, fall through to a direct subprocess invocation:
   * `claude_path = os.getenv("CLAUDE_CODE_PATH", "claude")`
   * Use a scrubbed env from `tools.testing.utils.get_safe_subprocess_env()`.
   * Build `[claude_path, "-p", prompt, "--model", request.model,
     "--output-format", "stream-json", "--verbose"]`.
   * Resolve the output file (default `<repo>/.tmp/agent_output.jsonl`)
     and ensure its parent exists.
   * Run the subprocess with `stdin=DEVNULL`, redirecting stdout to the
     output file. On `TimeoutExpired` return a failure response with a
     "timed out" message; on `FileNotFoundError` return a failure with
     a "Claude Code CLI not found" message; on any other exception
     return a generic failure with the exception text.
   * Parse the JSONL output file looking for the `{"type":"result"}`
     record; extract `result`, `session_id`, `is_error`. The response's
     `success` is `not is_error and returncode == 0`.

### `execute_template(request: AgentTemplateRequest) -> AgentPromptResponse`

1. Look up the model: `model = SLASH_COMMAND_MODEL_MAP.get(slash, request.model)`.
2. Build the prompt: `slash_command + " " + " ".join(args)` (no trailing
   space when args is empty).
3. `_ensure_agent_dir(run_id, agent_name)` returns
   `<repo>/agents/<run_id>/<agent_name>/` and creates the `prompts`
   subdirectory.
4. Write the prompt to
   `<agent_dir>/prompts/<command-with-dashes-replaced-by-underscores>.txt`.
5. Construct an `AgentPromptRequest(prompt, agent_name, model,
   output_file=<agent_dir>/raw_output.jsonl, project_dir=".")`.
6. Call `prompt_claude_code(...)`.
7. After invocation, attempt to convert the JSONL output file to a
   sibling `raw_output.json` (a JSON array of every line). All
   exceptions during this conversion are swallowed.
8. Return the response unchanged.

## `_get_timeout(slash_command="") -> int`

Read `args/cicd_config.yaml`. The relevant block is:

```yaml
cicd:
  executor:
    default_timeout_seconds: 300
    timeout_overrides:
      "/icdev-build": 1800
      ...
```

Returns the per-command override when present, the default otherwise.
On any exception (file missing, parse error) returns 300.

## `_ensure_agent_dir(run_id, agent_name) -> Path`

Creates and returns `<repo>/agents/<run_id>/<agent_name>/`. Also
creates the `prompts/` subdirectory if needed.

## Forbidden

* No DB writes (the robust executor handles audit/persistence; this
  module only delegates).
* No `print()` for production output. Use a logger.
* No network calls outside of the underlying `claude` subprocess.

## Acceptance

Tests cover:

* `BOT_IDENTIFIER` exposed and equal to the literal `"[ICDEV™-BOT]"`.
* `SLASH_COMMAND_MODEL_MAP` contains every documented entry with the
  documented tier.
* `_get_timeout` returns 300 by default and honors overrides when
  present.
* `_ensure_agent_dir` creates the directory tree.
* `execute_template` writes the prompt file under
  `prompts/<safe_name>.txt` and calls `prompt_claude_code` with the
  expected `AgentPromptRequest` (model resolved, project_dir=".").
* `execute_template` swallows the JSONL→JSON conversion exception.
* `prompt_claude_code` happy path through robust executor (mocked).
* `prompt_claude_code` falls back on `ImportError` and parses the
  result line correctly (mocked subprocess).

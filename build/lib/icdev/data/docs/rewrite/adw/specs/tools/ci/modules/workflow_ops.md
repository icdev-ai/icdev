# Spec: `tools/ci/modules/workflow_ops.py`

_OPT-75 Phase 1 clean-room spec._

## Purpose

A small library of helpers shared by every `tools/ci/workflows/icdev_*.py`
script. Knows how to call the Claude Code agent for issue
classification, branch naming, plan building, plan implementation, and
commit-message generation. Plus a couple of utilities (`ensure_run_id`,
`find_existing_branch_for_issue`, `format_issue_message`,
`extract_icdev_info`).

## Module-level constants

* `AGENT_PLANNER = "icdev_planner"`
* `AGENT_IMPLEMENTOR = "icdev_implementor"`
* `AGENT_CLASSIFIER = "issue_classifier"`
* `AGENT_BRANCH_GENERATOR = "branch_generator"`
* `AGENT_PR_CREATOR = "pr_creator"`
* `AVAILABLE_ICDEV_WORKFLOWS = [...]` (list of slash-command names that
  are valid `icdev_*` workflows). Must contain at least: `icdev_plan`,
  `icdev_build`, `icdev_test`, `icdev_review`, `icdev_comply`,
  `icdev_secure`, `icdev_deploy`, `icdev_document`, `icdev_patch`,
  `icdev_plan_build`, `icdev_plan_build_test`,
  `icdev_plan_build_test_review`, `icdev_sdlc`.

## Public functions

### `format_issue_message(run_id, agent_name, message, session_id=None) -> str`
Returns a string in the form
`"<BOT_IDENTIFIER> <run_id>_<agent_name>[_<session_id>]: <message>"`.
The bot identifier comes from `tools.ci.modules.agent.BOT_IDENTIFIER`.

### `extract_icdev_info(text, temp_run_id) -> tuple[str | None, str | None]`
Calls the `icdev_classifier` agent with `/classify_workflow`. Parses
the response as JSON, expects an `icdev_slash_command` field, strips
the leading `/`, validates against `AVAILABLE_ICDEV_WORKFLOWS`, and
returns `(workflow_command, run_id)` or `(None, None)`. Any exception
during parsing or invocation returns `(None, None)`.

### `classify_issue(issue_json, run_id, logger) -> tuple[str | None, str | None]`
Invokes the `issue_classifier` agent with `/classify_issue`. Parses
the output for one of `/chore`, `/bug`, `/feature`, `/patch`, or `0`.
Returns the matched command, or `(None, error_message)` for invalid /
zero / agent-failure cases.

### `generate_branch_name(issue_json, issue_class, run_id, logger) -> tuple[str | None, str | None]`
Invokes the `branch_generator` agent with `/generate_branch_name`.
Returns `(stripped_output, None)` on success, `(None, error)` on agent
failure.

### `build_plan(issue_json, command, run_id, logger) -> AgentPromptResponse`
Invokes the planner agent with `command` as the slash command. Returns
the raw `AgentPromptResponse`.

### `implement_plan(plan_file, run_id, logger, agent_name=None) -> AgentPromptResponse`
Invokes `agent_name` (default: `AGENT_IMPLEMENTOR`) with `/implement
<plan_file>`. Returns the raw response.

### `create_commit(agent_name, issue_json, issue_class, run_id, logger) -> tuple[str | None, str | None]`
Invokes a transient agent named `f"{agent_name}_committer"` with
`/commit <agent_name> <issue_type> <issue_json>`. Returns
`(stripped_message, None)` or `(None, error)`.

### `create_pull_request(branch_name, issue_json, state, logger) -> tuple[str | None, str | None]`
Invokes the `pr_creator` agent with `/pull_request`. Pulls
`plan_file` and `run_id` out of `state`. Returns `(stripped_url, None)`
or `(None, error)`.

### `ensure_run_id(issue_number, run_id=None, logger=None) -> str`
* If `run_id` is provided, attempt `ICDevState.load(run_id)`. If the
  loaded state's `run_id` matches the requested one, log "found
  existing" and return as-is. Otherwise create a new `ICDevState`,
  set `run_id` and `issue_number`, save, and return.
* If `run_id` is None, generate a new id via
  `tools.testing.utils.make_run_id()`, create the state, save, and
  return.

### `find_existing_branch_for_issue(issue_number, run_id=None) -> str | None`
Run `git branch -a` from `PROJECT_ROOT`. For each branch, strip the
`* ` and `remotes/origin/` prefixes, then look for the substring
`-issue-<issue_number>-`. If `run_id` is provided, additionally
require `-icdev-<run_id>-`. Return the first match or `None`.

## Forbidden

* No DB writes.
* No `print()` for production output **unless** the function is the
  legacy `extract_icdev_info` path that is preserved for compatibility
  (it has historically printed via `print()` because it has no logger
  argument). Acceptable to migrate it to a module-level logger.
* No network calls.

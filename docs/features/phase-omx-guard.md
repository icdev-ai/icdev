# CUI // SP-CTI

# OMX guard: ICDEV's PreToolUse guard inside opencode (omx-guard-01)

## What shipped

| Piece | Path | Role |
|---|---|---|
| Plugin | `icdev/data/harness_plugins/opencode/icdev-guard.ts` (packaged) | opencode `tool.execute.before`: hands every tool call to the bridge and **throws** on deny with the check's reason |
| Bridge | `tools/hooks/harness_guard.py` | `python -m tools.hooks.harness_guard --harness opencode`: maps opencode's call (lowercase tool, camelCase args) onto ICDEV's `{tool_name, tool_input}`, calls `tools/airgap/hook_compat.py::run_pre_tool_check`, prints one JSON verdict |
| Installer | `icdev harness install-guard opencode [--project DIR\|--global]` (`tools/cli/harness.py`) | copies the plugin to `<DIR>/.opencode/plugin/` or `~/.config/opencode/plugin/` (`$XDG_CONFIG_HOME` honoured), with this interpreter and ICDEV root filled in |
| Adapter | `tools/agents/adapters/opencode_cli.py` | `invoke()` installs the plugin into the run's directory (idempotent) and **refuses to run** if it cannot; `verify_guard()` is a live probe |
| Capability | `args/agent_capabilities.yaml` | `opencode_cli.guard_wired: true`, confirmed by `capability_matrix.py --gate` (`actual: present`, method `behavioral`) |

No check was copied. The checks live once in `tools/hooks/shared_checks.py`;
`run_pre_tool_check` gained only an optional `skip=` argument so the bridge can
honour the hook's per-check kill switches.

## Behaviour

* **Deny** — the plugin throws `ICDEV guard: BLOCKED: …`; opencode records the
  tool call as an error and the run continues (omx-spike-01 measured this).
* **Fail open, logged** — a bridge that cannot be started, exits non-zero,
  times out (`ICDEV_GUARD_TIMEOUT_MS`, default 30 s) or prints no verdict allows
  the call, the same as `.claude/hooks/pre_tool_use.py` `main()`. The plugin
  logs it to stderr and `.opencode/icdev-guard.log`. A guard that raises inside
  the bridge also fails open with the exception in the reason.
* **Kill switches** are the hook's own and are read from the hook's
  `CHECK_KILL_SWITCHES` table by AST, never copied: `ICDEV_<CHECK>_GUARD=0`
  skips one check; `ICDEV_PRETOOLUSE_ENFORCE=0` turns a refusal into an
  advisory allow (`advisory: true`). `run_pre_tool_check` has no `=monitor`
  mode, so none was added; `network_egress` and `agent_rules` already ship
  monitor-only.
* **`--pure` drops the plugin** (omx-spike-01); `build_argv` refuses it.

## The mapping is load-bearing

Every check keys on Claude Code's spelling. `run_pre_tool_check("bash", …)`
with opencode's lowercase name lets a recursive root `rm` through, because
`check_dangerous_rm` only examines `Bash`. `tests/hooks/test_harness_guard.py::test_mapping_is_load_bearing`
pins both halves.

## Verification

* `tests/hooks/test_harness_guard.py`: the bridge **denies** a dangerous rm, a
  `.env` read, an append-only `UPDATE audit_trail` and a `.env` edit (all in
  opencode's spelling); it **allows** `git status`. It also covers kill
  switches, advisory mode, fail-open and the stdin→stdout CLI round trip.
  Gated in `args/ci_test_files/core.d/omx-guard-01.txt`.
* `python tools/agents/capability_matrix.py --gate` exits 0. opencode_cli
  `guard_wired` is `declared: true, actual: present, verdict: confirmed`, and
  the evidence is `verify_guard()` running the bridge subprocess on a known-bad
  `bash` call and getting `BLOCKED: Dangerous rm command detected and prevented`.
* End to end, outside CI: the **installed** plugin was imported with bun (opencode
  is not on this host) and its `tool.execute.before` was called directly. It
  threw on the rm and on the `.env` read and allowed `git status`. With
  `ICDEV_PYTHON=no-such-python` it failed open and wrote the log line.

## Fire-rate survey (before arming)

`python tools/hooks/fire_rate_survey.py --json`, run 2026-10-10 against this
branch: Claude Code transcripts under `~/.claude/projects`, **30-day window,
4,273 transcripts, 556 sessions, 25,141 tool calls** (Bash 19,824, Edit 1,366,
Write 1,107, Read 1,008). opencode runs the **same checks** through
`run_pre_tool_check`, so these per-check rates apply to it. The opencode
transcripts are not a corpus yet. `hook_events` was again unusable: 0 of 5,000
sampled rows carry an operand.

| Check | Mode | Fired | Rate | Sessions | Distinct operands |
|---|---|---:|---:|---:|---:|
| `env_file_access` | replayed | 1 | 0.004% | 1 | 1 |
| `dangerous_rm` | replayed | 4 | 0.016% | 3 | 4 |
| `git_danger` | replayed | 40 | 0.159% | 9 | 40 |
| `append_only_write` | replayed | 1 | 0.004% | 1 | 1 |
| `direct_sqlite_usage` | replayed | 1 | 0.004% | 1 | 1 |
| `file_access_tiers` | replayed | 70 | 0.278% | 44 | 67 |
| `write_outside_worktree` | replayed | 50 | 0.199% | 19 | 41 |
| `branch_deletion` | trigger_only | 9 | 0.036% | 4 | 9 |
| `worktree_path` | replayed | 101 | 0.402% | 13 | 101 |
| `gh_pr_merge_bypass` | trigger_only | 158 | 0.628% | 20 | 158 |
| `network_egress` | replayed | 41 | 0.163% | 16 | 38 |
| `agent_rules` | trigger_only | 0 | 0.000% | 0 | 0 |
| `review_loop_precommit` | trigger_only | 490 | 1.949% | 170 | 487 |

How to read it:

* **Every replayed check fires on under 0.5% of calls.** The worst is
  `worktree_path` at 0.40%. That is far below the 4.86% that held back the
  original exa-bench-05 arming, and these checks are already enforcing in every
  Claude Code session on this host, so arming them for opencode adds no rate
  that has not already been run. On this evidence the guard is armed at its
  defaults.
* **`trigger_only` rows are upper bounds.** They count calls that *reached*
  the check's expensive part, not refusals. `review_loop_precommit` (1.95%) is
  warn-only by default. `gh_pr_merge_bypass` refuses only a kanban-linked PR.
* `network_egress` is replayed with enforcement forced on; at its default it
  is monitor-only and refuses nothing.
* `write_outside_worktree` and `worktree_path` depend on where opencode runs.
  An opencode session started outside an ICDEV worktree root will hit them.
  That is the boundary doing its job, and the per-check switches above are the
  sanctioned way to stand one down.

## Not in this slice

* Pi's extension uses the same bridge (omx-guard-02). Its tool and argument
  mapping (`path`, `edits[]`, a separate `powershell` tool) is not in
  `TOOL_NAMES` yet, so `pi_cli.guard_wired` stays `false`.
* Before dispatching autonomously, the runner should refuse any adapter whose
  `guard_wired` is not `present` (omx-guard-03).

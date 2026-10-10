# CUI // SP-CTI

# OMX guard: ICDEV's PreToolUse guard inside opencode and Pi (omx-guard-01, -03)

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

## Pi (omx-guard-03)

Pi is a fully supported peer of opencode (operator, 2026-10-10), so it has the
same guard, through the **same** bridge.

| Piece | Path | Role |
|---|---|---|
| Extension | `icdev/data/harness_plugins/pi/icdev-guard.ts` (packaged) | Pi `tool_call` handler: hands every tool call to `python -m tools.hooks.harness_guard --harness pi` and returns `{block: true, reason}` on deny |
| Mapping | `tools/hooks/harness_guard.py` (`TOOL_NAMES["pi"]`, `ARG_KEYS["pi"]`, `_join_pi_edits`) | the only Pi-specific Python: `path` -> `file_path`; `edits[{oldText,newText}]` -> `old_string`/`new_string` with **every** edit joined; `find` -> `Glob`, `ls` -> `LS`, `powershell` -> `Bash` |
| Installer | `icdev harness install-guard pi [--project DIR\|--global]` | `<DIR>/.pi/extensions/icdev-guard.ts` or `$PI_CODING_AGENT_DIR/extensions/` (default `~/.pi/agent/extensions/`) |
| Adapter | `tools/agents/adapters/pi_cli.py` | `invoke()` installs the extension into the run's directory and passes it with an explicit `-e` (project extensions load only in a trusted project; `-e` always, and `--no-extensions` does not drop it), and **refuses to run** if it cannot install it; `verify_guard()` is `harness_guard.live_probe("pi", …)` |
| Capability | `args/agent_capabilities.yaml` | `pi_cli.guard_wired: true`, confirmed by `capability_matrix.py --gate` (`actual: present`, method `behavioral`) |

`live_probe()` is now the one body of both adapters' `verify_guard()`;
opencode's copy of it was removed rather than duplicated.

**Seam: GO, as the spike recorded.** omx-spike-01 (findings, "Pi") measured
that a `tool_call` handler returning `{block: true}` prevents the call,
including nested calls a tool makes through `ctx.executeTool`. No fallback seam
was needed.

**Fail open, deliberately.** The spike's Pi extension failed CLOSED. This one
fails open, logged, to match `pre_tool_use.main()` and the opencode plugin.
Pi treats a handler that **throws** as a block, so the extension catches every
error and turns it into an allow; it does not rely on Pi for this. The log goes
to stderr and `.pi/icdev-guard.log`. Kill switches are the hook's own
(`ICDEV_PRETOOLUSE_ENFORCE`, `ICDEV_<CHECK>_GUARD`), read by the bridge.

### Verification

* `tests/hooks/test_harness_guard_pi.py` (gated in
  `args/ci_test_files/core.d/omx-guard-03.txt`) sends Pi-shaped calls through
  the bridge. It **denies** a root `rm` through `bash` and through
  `powershell`, a `.env` read, write and multi-edit, an append-only
  `UPDATE audit_trail`, and a `sqlite3.connect` hidden in the **second** edit
  of an `edits[]` array. It **allows** `git status`, a README read and `ls`.
  It also covers kill switches, advisory mode, fail-open, the stdin->stdout
  round trip, the installer (project and global), the adapter passing `-e`
  and refusing to run unguarded, and `capability_matrix` reporting
  `pi_cli guard_wired = present`.
* `python tools/agents/capability_matrix.py --gate` exits 0, and `pi_cli`
  `guard_wired` is `declared: true, actual: present, verdict: confirmed`. The
  evidence is `verify_guard()` running the bridge subprocess on a known-bad Pi
  `bash` call and getting `BLOCKED: Dangerous rm command detected and prevented`.
* End to end, with **real Pi 1.1.0** on this host (Windows) and an isolated
  `PI_CODING_AGENT_DIR`: the installed extension was passed with `-e` and the
  model was asked to read a fixture `.env`, then run `git --version`. The read
  ended `isError: true` with
  `ICDEV guard: BLOCKED: Access to .env files is prohibited…`, `git --version`
  ran, and the run settled `aborted: false`, exit 0. Separately, the installed
  file was loaded with bun and driven directly. It blocked the `rm`, the `.env`
  read and the `.env` edit, and allowed `git status`. With
  `ICDEV_PYTHON=no-such-python` it allowed and wrote the FAILED OPEN log line.

### Fire rate

The checks are shared, so **guard-01's survey above applies unchanged.** Pi
runs the same `run_pre_tool_check` and the same checks at the same defaults.
This task changed no check, so nothing new was armed and the survey was not
re-run. What Pi adds is the input **shape**. The Pi-shape replay is the
fixture set above: guard-01's deny/allow fixtures, re-expressed in Pi's
spelling, plus three that exist only in Pi (`powershell`, `edits[]` with more
than one entry, `write` with `path`). Every deny fixture is refused and every
allow fixture is allowed, so the mapping neither loses a refusal nor adds one.
Pi transcripts are not a corpus yet. When they are, add them as a
`fire_rate_survey.py` source rather than replaying `hook_events`, which holds
no operands.

### Residual gaps

* **PowerShell-dialect deletes are not caught.** Pi's Windows `powershell`
  tool is mapped to `Bash`, so every shell check sees its command, and a POSIX
  `rm -rf` sent through it is refused (tested). `check_dangerous_rm` parses
  POSIX syntax only, so `Remove-Item -Recurse -Force` passes, as the spike
  measured. The fix is a PowerShell-dialect check in `shared_checks.py`,
  written once for every harness, with a fire-rate survey before it is armed.
  It is a separate card, not a Pi-specific copy.
* **A double load was not measured.** In a **trusted** project (`--approve`),
  Pi could load `.pi/extensions/icdev-guard.ts` and the explicit `-e` copy
  both, which would call the bridge twice. The verdict is the same and only
  latency doubles. `pi_cli` does not pass `--approve` by default.

## Not in this slice

* Before dispatching autonomously, the runner should refuse any adapter whose
  `guard_wired` is not `present` (omx-guard-02).

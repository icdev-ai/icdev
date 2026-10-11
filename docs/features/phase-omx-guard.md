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

## Guarded shell -- layer 2 (omx-guard-04)

L1 is a native pre-tool hook, so it covers only a harness that has one. L2
guards what every coding agent does through the OS rather than through a
plugin API: it **runs a shell**. The kanban runner puts a directory of shims in
front of `$SHELL` and `PATH` for every spawned harness. A `bash -c <cmd>` the
harness spawns lands on the shim, which asks the **same bridge**
(`harness_guard.decide` -> `run_pre_tool_check` -> `shared_checks`) and then
either execs the real shell with the argv unchanged or exits **126** with the
BLOCKED reason on stderr. No check was copied.

| Piece | Path | Role |
|---|---|---|
| Shim | `tools/hooks/guard_shell.py` (mirrored to `icdev/tools/hooks/`) | `shim_main()`: reads the command (`-c` in any option cluster such as `-lc`, `-ic`, `-euc`; a script path; or stdin), decides, then `os.execv`s the real shell or refuses |
| Shims on disk | `~/.cache/icdev/guard-shell/<hash>/{bash,sh,dash,zsh,login/<shell>}` | `#!/bin/sh` one-liners that exec `guard_shell_entry.py`. Per-user and per-(interpreter, root), never a shared plantable `/tmp` path. A shim is written only for a shell that exists |
| Runner wiring | `tools/genesis/reflexes/kanban.py::_agent_session` | `metadata["env"] = guard_shell.wiring()["env"]`: `PATH` prefixed, `SHELL` -> `login/<shell>`, the real shells recorded. Every adapter already merges `metadata["env"]` into its spawn env, so there is **no per-harness code** |
| Measurement | `args/guard_shell_resolution.yaml`, `python -m tools.hooks.guard_shell --measure -- <harness argv>` | how each harness finds its shell, from a live run |
| Capability | `guard_shell` in `capability_matrix.py` + `args/agent_capabilities.yaml` | probe-measured per adapter (below) |

### What it reads as "the command"

* `-c <text>` -- the text exactly as given, with backslash-newline removed
  first, the way the shell removes it (`cat \<newline>.env` is `cat .env`).
* **`eval` payloads.** Measured, not hypothetical. Claude Code sends
  `bash -c "source <snapshot> ... && eval 'cat .env' < /dev/null && pwd -P >| <tmp>"`.
  `check_env_file_access` reads `'cat .env'` as **one quoted token**. The first
  real end-to-end run on 2026-10-11 was **allowed**: Claude printed the fixture
  `.env`. `eval` is a builtin, so unlike a nested `bash -c` it never comes back
  through a PATH shim. The shim therefore also decides each `eval` argument,
  nested ones included, using `shared_checks`' own `command_segments` and
  `shell_tokens`. That is input extraction, not a second check. L1 never had
  this gap, because Claude's own hook sees `cat .env` before Claude wraps it.
* A script path: the file's contents.
* stdin, **line by line**. A harness may hold a persistent shell open on a pipe
  and wait for each command's output, so reading to EOF would deadlock it.
  Each line (continuations joined) is checked, then forwarded. A refused line
  is dropped with its reason on stderr and the next line still runs, so a
  completion sentinel keeps working. Exit is 126 if anything was refused.
* An interactive shell on a TTY cannot be pre-read. It is passed through, and
  the gap is logged.

### Which harnesses L2 actually covers (measured 2026-10-11)

Live run of each real harness on the Omarchy (Linux) host, with a recording
shim behind `$SHELL` and recording shims PATH-prefixed for bash/sh/dash/zsh,
asked to run `echo <marker>`:

| Adapter | Harness | Finds its shell via | `guard_shell` |
|---|---|---|---|
| `claude_cli` | Claude Code 2.1.296 | `$SHELL` | **present** (confirmed) |
| `opencode_cli` | opencode 1.18.35 | `$SHELL` (re-measured with `--measure`: `shell_env`, 1 shim call) | **present** (confirmed) |
| `codex_cli` | codex-cli 0.162.1 | **absolute path**: the marker ran, 0 shim calls | absent: **not covered by L2** |
| `pi_cli` | pi 1.1.0 | **`/bin/bash` absolute**: Pi's RPC `bash` (same executable as the model's tool, per Pi's docs) ran with 0 shim calls | absent: **not covered by L2** (L1 extension covers it) |
| `copilot_cli` | Copilot CLI 1.0.95 | not measured: not logged in on the probe host | unconfirmed |
| `goose_cli` | -- | not installed on Linux; Windows is unsupported | unconfirmed |
| `local_agent`, `local_llm_router` | -- | not harnesses (in-process allowlist / no commands) | unconfirmed |

`present` requires **both** measurements: the recorded live resolution, and a
behavioural self-test. The self-test passes the runner's wiring through the
adapter's own `build_env()`, resolves the shell the way the harness was
measured to, and requires `echo` to come back byte-identical and `cat .env`
(run in an empty temp dir, so harmless if it ever ran) to be refused with 126.
`absolute` is a measured **absence**: no PATH or `$SHELL` shim can see a shell
exec'd by absolute path. `python tools/agents/capability_matrix.py --gate`
exits 0 on Linux and Windows, with 0 overclaimed. On Windows, claude/opencode
are `unverified` (declared, cannot be wired there).

### Windows: unsupported, explicitly

`wiring()` returns `supported: false` with the reason, and the runner logs
`guarded shell (L2) NOT wired` on **every** dispatch. CreateProcess finds
`cmd.exe` in System32 before `PATH`, and harnesses spawn
`bash`/`powershell` as `.exe` (libuv and Rust append only `.exe`). A script
shim cannot stand in for either. `cmd /c` and `powershell -Command` are
therefore not covered by L2 on Windows. It is reported, never a silent pass.
The live runner on the Windows host gains nothing from L2. claude and pi stay
guarded by L1 there.

### Behaviour

* **Fail open, logged**, like `harness_guard`: a bridge that raises allows the
  command, and the reason says so. A wiring failure in the runner logs
  `dispatching WITHOUT it` and does not stop the dispatch.
* **Kill switches** are the hook's own, applied by `decide`:
  `ICDEV_PRETOOLUSE_ENFORCE=0` makes a refusal advisory (the command runs and
  `ADVISORY` goes to stderr), and `ICDEV_<CHECK>_GUARD=0` skips one check.
  No new switch was added.
* `ICDEV_GUARD_SHELL_LOG=<file>` appends every shim decision as JSONL. That
  is how `--measure` sees which route a harness took.
* Each decision writes a `hook_events` audit row through `run_pre_tool_check`,
  the same as L1.

### Fire-rate survey (before arming)

`python tools/hooks/fire_rate_survey.py`'s own transcript reader
(`iter_transcripts(since_days=30)` + `iter_tool_calls`), run 2026-10-11 against
this branch: **4,292 transcripts, 20,703 Bash calls, 943 s**. Every call was
decided three ways: by the bridge as L1 sees it (`harness_guard.decide`), by
the shim on the raw text (`guard_shell.decide_text`), and by the shim on the
text **wrapped exactly as Claude Code sends it to `$SHELL`**
(`source <snapshot> ... && eval '<cmd>' < /dev/null && pwd -P >| <tmp>`).

| Decision | Refused | Rate | Sessions |
|---|---:|---:|---:|
| L1 bridge, raw command | 256 | 1.24% | 70 |
| L2 shim, raw command | 256 | 1.24% | 70 |
| L2 shim, Claude-wrapped command | 255 | 1.23% | 70 |

* **L2 adds no refusal L1 does not already make: 0 calls** were refused by the
  shim and allowed by the bridge, raw or wrapped. The checks are the same
  checks, already enforcing in every Claude Code session on this host, so L2
  is armed at its defaults.
* An earlier run of the same replay found **209 extra refusals** when the shim
  checked the quoted `eval '...'` segment whole: `dangerous_rm` read a quoted
  `docker` container removal as an rm with no target. The shim now checks an
  eval's argument *instead of* the quoted segment (only when `eval` is the
  command word), which brought the extra count to 0.
* **1 call** refused by L1 is allowed when wrapped: a `python - <<'PYEOF'`
  heredoc whose BODY `write_outside_worktree` read as a write target (a known
  L1 false positive, not a real write). The eval unwrap re-joins tokens, so the
  body no longer looks like a path. L2 is not stricter than L1 anywhere.
* The replay surveys Claude Code commands because that is the corpus that
  exists; opencode commands pass the same `decide_text`, and the codex/pi
  absolute-`/bin/bash` paths are not covered by L2 at all (see the matrix).

### Verification

* `tests/hooks/test_guard_shell.py`, gated in
  `args/ci_test_files/core.d/omx-guard-04.txt`, covers:
  * argv parsing for `-c`, `-lc`, `-ic`, `-euc`, `--login`, `-o`, `-eo`,
    `--rcfile`, `--`, a script and stdin;
  * **benign commands byte-identical** to the real shell (argv incl. `$0`,
    spaces and quotes, exit code, stdout, stderr, unicode, multi-line, script,
    stdin);
  * **known-bad refused** with 126, the real shell never running it (a `RAN`
    sentinel stays absent): via `-c`, `-lc`, `-ic`, an append-only `UPDATE`, a
    script, stdin (refused line dropped, later lines still run), a
    backslash-continuation split, and Claude's exact `eval` wrapper;
  * the kill switches, fail-open, the real shell never resolving to the shim;
  * wiring (POSIX env, explicit Windows refusal), the runner session carrying
    the wiring and logging when it is unsupported;
  * the `guard_shell` probe per adapter, and no adapter overclaiming.
* Linux (Omarchy, Python 3.14, throwaway clone): `tests/hooks/` +
  `test_agent_capability_matrix.py` gave 482 passed, 2 skipped (pre-existing).
  Windows: the new file gave 41 passed.
* End to end, Linux, **real Claude Code 2.1.296** in a hookless temp dir with a
  fixture `.env`, wired exactly as `_agent_session` wires it. Asked to run
  `cat .env` then `echo SECOND_OK`, the first was refused:
  `Exit code 126 / ICDEV guard: BLOCKED: Access to .env files is prohibited`,
  "The file was never read". `SECOND_OK` printed normally.

### Not changed, and residual gaps

* **`guard_parity` is unchanged.** It still requires `guard_wired` (L1) for
  autonomous dispatch. L2 sees shell commands only, not a harness's file
  edits (L3, omx-guard-05). Of the adapters guard_parity refuses, L2 measured
  codex as **not coverable** and could not measure copilot or goose, so
  accepting `guard_shell` as "guarded" would admit nothing today.
* The positional arguments after `-c <text>` (`$0`, `$1`, ...) are not part of
  the checked text.
* `source`/`.` files are not read: Claude sources its own snapshot on every
  call.
* A command split across lines **inside quotes** on a persistent stdin shell
  is checked line by line; only backslash continuations are joined.

## Default-branch push guard (omx-guard-08)

**Measured 2026-10-11** on the Omarchy laptop (omx-smoke-03: opencode_cli on
ollama-cloud/kimi-k3, external scratch repo). After committing on
`kanban/omx-smoke-03`, the agent ran `git push origin kanban/omx-smoke-03:main`
and then `git push origin main`. Its work landed on the default branch with no
PR. Nothing refused it. `check_git_danger` refuses a *force* push and
`check_branch_deletion` a *delete*, and a fast-forward push to main is neither.
GitHub branch protection would have stopped it, but a local or bare remote, a
Gitea, or an unprotected repo does not. Operator approved the guard the same day.

| Piece | Path | Role |
|---|---|---|
| Check | `tools/hooks/shared_checks.py::check_default_branch_push` (mirrored, and in the bootstrap payload) | refuses a `git push` whose destination is the repo's default branch. One copy, which every path runs |
| L1 Claude Code | `.claude/hooks/pre_tool_use.py` | runs it fourth, after `git_danger`, with the hook payload's `cwd` |
| L1 opencode / Pi | `harness_guard.decide(..., cwd=)` + both plugins | the plugins now send `cwd: directory`. The bridge runs from the ICDEV root, so without this it would have read ICDEV's branches instead of the run's |
| Headless / L2 | `hook_compat.HEADLESS_CHECKS` | `run_pre_tool_check` covers `guard_shell` too. The shim runs **in** the shell's directory, so git sees the right repo with nothing passed |
| Survey | `fire_rate_survey.py` check `default_branch_push` | replayed, anchored on each transcript's checkout |

### What is refused

The destination is parsed from the push's own argv. Refused: `<remote> main`,
`HEAD:main` / `@:main`, `<src>:main`, `<src>:refs/heads/main`, `+<src>:main`,
`:main` and `--delete main`, the matching spec `:`, a glob that covers the
default branch, and `--all` / `--branches` / `--mirror`. Every form is refused
with `git -C <dir>` too, after a `cd <dir> &&`, inside `( … )`, behind
`VAR=x` / `env` / `sudo` prefixes, and inside the L2 shim's `eval '…'` unwrap.

A **bare** `git push` / `git push <remote>` goes where git would send it:

* `remote.<remote>.push` set: those refspecs are judged.
* `push.default=simple` (git's default) or `current`: refused when the
  **current** branch is the default branch.
* `upstream` / `tracking`: refused when the branch **tracks** the default
  branch.
* `matching`: always refused. `nothing`: never refused.

The task text said "is (or tracks)". Under `simple`, "tracks" alone is not a
push to main. A `kanban/x` worktree created from `origin/main` records
`origin/main` as its upstream, and git **itself** refuses that bare push
because the names differ. Refusing it here would refuse nothing git lets
through. It would also be a false positive on every worktree made the way
CLAUDE.md prescribes. `tests/hooks/test_default_branch_push.py` pins all four
modes.

Allowed: any feature/kanban branch (`-u origin kanban/x`, `HEAD:kanban/x`,
`--force-with-lease` on it), deleting a non-default branch, tags (`v1.0`,
`refs/tags/…`, `--tags`), `--dry-run` / `-n`. Prose (`echo …`, a commit
message, a grep pattern) is also allowed: only a segment whose command word is
`git` with subcommand `push` is parsed.

### The default branch is resolved per repository, never assumed

In order:

1. `git symbolic-ref refs/remotes/<remote>/HEAD`, which clone records.
2. The remote's own `HEAD` when its URL is a local path. A bare scratch remote
   added with `git remote add` has no remote HEAD ref.
3. A remote-tracking, then a local, `main` / `master`.
4. When git cannot answer at all (a directory it cannot read), **both** `main`
   and `master`. An explicit `git push origin master` is still refused there.
   A bare push, which needs branch state, fails open.

The directory is, in order: the caller's `cwd` (the Claude hook payload), then
`tool_input['cwd']` / `['workdir']`, then whatever `cd` / `pushd` / `git -C`
the command applies, then the process's own directory (the L2 shim's). Nothing
reads `os.getcwd()`, and `test_no_check_reads_cwd` still holds. The check
fails open on any error.

### Fire-rate survey (before arming)

`python tools/hooks/fire_rate_survey.py --json --check default_branch_push`,
run 2026-10-11 against this branch: Claude Code transcripts, 30-day window,
**4,297 transcripts, 580 sessions, 26,199 tool calls (20,952 Bash), 636 of
them `push` calls**.

| Verdict | Fired | Rate | Sessions |
|---|---:|---:|---:|
| Survey (argv + today's branch state of each call's checkout) | 31 | 0.118% | 21 |
| argv alone (no branch state) | 1 | 0.004% | 1 |
| **True positives after classifying every fire** | **0** | **0.000%** | 0 |

All 31 fires were classified by hand from their recorded `cwd`. Every one is a
replay artifact, not a push to main. Each ran from a kanban worktree that has
since been removed: 30 under `C:\AI\ICDev\.tmp\worktrees\…`, 1 under
`icdev_ft`. The survey's anchor therefore walked up to the enclosing **main
checkout**, which is on `main` today, so the call's `git push -u origin HEAD`
(10) or bare `git push` (21) was judged as if it ran on `main`. When those
commands ran, they pushed the worktree's own kanban branch. The one argv-only
fire is the same artifact: its `cd` names a worktree husk, which git resolves
to the enclosing checkout. **No session in the window pushed to a default
branch**, so the guard is armed at its default (on). The survey row is
labelled an upper bound for this reason.

### Standing it down

* **One check, one harness session:** start the agent with
  `ICDEV_DEFAULT_BRANCH_PUSH_GUARD=0` in its environment. This is the
  `CHECK_KILL_SWITCHES` entry `default_branch_push`, honoured by the Claude
  hook, `harness_guard` (opencode/Pi) and `guard_shell` alike. For Claude
  Code, adding it to `.claude/settings.local.json` `env` takes effect on the
  next tool call. Remove it after the one push.
* **Human-approved hotfix, one command:** the human runs that one
  `git push origin main` in their own terminal. The guard sits in front of
  agents only, so nothing outside a harness is intercepted.
* **Not a switch:** writing `ICDEV_DEFAULT_BRANCH_PUSH_GUARD=0 git push origin
  main` *inside the command*. The agent writes the command, so a switch it can
  type is no switch. This is pinned by a test.
* `ICDEV_PRETOOLUSE_ENFORCE=0` still turns every refusal into an advisory, as
  for every other check.

### Verification

* `tests/hooks/test_default_branch_push.py` (69 tests, gated in
  `args/ci_test_files/core.d/omx-guard-08.txt`) builds real throwaway repos: a
  bare remote plus a clone, one with default `main` and one with default
  `master`. It covers:
  * every refspec form refused in **both** repos;
  * `git -C` and `cd` resolved against the named directory;
  * 16 allowed forms;
  * `master` resolved from the repo, so `git push origin main` is allowed
    there;
  * a `git remote add` remote with no HEAD ref asked directly;
  * the unreadable-directory fallback;
  * bare push on the default branch refused, and on a tracking feature branch
    under all four `push.default` modes;
  * configured `remote.origin.push`;
  * the kill switch, and the inline assignment that is **not** one;
  * end to end through the Claude hook subprocess (exit 2, using the payload
    `cwd`), `run_pre_tool_check`, `harness_guard.decide` for opencode and Pi,
    and `guard_shell.decide_text`, including the `eval '…'` wrapper.
* Red-first: `red_first_gate.py --gate` reports it **discriminating**. It
  fails to collect against the merge base and passes here. `isolation_run.py`
  passes it alone.
* `tests/hooks/` plus the bootstrap-payload, AGOV, branch-deletion and
  capability-matrix suites: 624 passed.

### Residual gaps

* Codex and Pi's RPC shell exec `/bin/bash` by absolute path, so L2 does not
  see them (see the L2 matrix above). Pi's L1 extension does see its `bash`
  tool. Codex has neither layer.
* A push spelled through a git alias (`git p`) or a script file the agent
  writes and runs is not parsed. `git_danger` has the same limit.
* `push.default=matching` refuses even when the remote has no default branch
  yet. That is conservative, and the setting is rare.

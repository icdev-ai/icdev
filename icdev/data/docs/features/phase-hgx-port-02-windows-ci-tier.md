# Phase HGX — Windows CI Tier for the Portability-Sensitive Suite

**Task:** `hgx-port-02`. **Card:** HGX — Harness Agent Parity and Graph Runtime
(`args/projects.yaml`, MANUAL-ONLY, gated on `hgx-gate-00`).
**Governing rule:** extend the existing surface — one workflow, one new job, no
second pipeline.

## The problem

All nine jobs in `.github/workflows/icdev-ci.yml` ran on `ubuntu-latest`. ICDEV
is **developed** on Windows and was **tested** only on Linux, so every
OS-portability defect was structurally invisible to the pipeline: it could not
fail on the only OS the pipeline ran.

That is not hypothetical. It is exactly how the `rubric_build_tools` newline bug
survived review (`hgx-exec-01`): on Windows the owned build agent rewrote every
file it touched from LF to CRLF, turning a one-line edit into a whole-file `git
diff` and making the pipeline grader's changed-file signal meaningless. Every
Linux assertion stayed green throughout, because on Linux the bug does not
exist.

## What changed

### 1. `test-windows` job (`.github/workflows/icdev-ci.yml`)

A `windows-latest` job, `needs: lint`, 30-minute timeout, running the
portability-**sensitive** subset rather than the suite — the modules that touch
the filesystem, spawn processes, or thread wall-clock timing, which is where
Windows and POSIX actually diverge:

| Area | Files |
|---|---|
| Build toolset | `tests/genesis/test_rubric_build_tools.py`, `tests/test_toolset_portability.py` |
| Agent loop | `tests/test_agent_loop.py`, `…_semantic_loop.py`, `…_wall_clock.py`, `…_wiring.py`, `tests/llm/test_agent_loop_shim.py`, `tests/llm/test_rubric_loop.py` |
| Studio / DAG runtime | `tests/studio/` |
| Executors & adapters | `tests/test_local_agent_adapter.py`, `tests/test_agent_surface_dispatch.py`, `tests/test_kanban_executor_resolution.py`, `tests/kanban/test_build_mode_and_model.py` |

Deliberate choices, each of which has a comment in the workflow:

- **`shell: bash`**, not the `windows-latest` default `pwsh`, so a step body
  means one thing across the whole file.
- **Path isolation preserved** — job-level `defaults.run.working-directory:
  ${{ github.workspace }}` and absolute `PYTHONPATH: ${{ github.workspace }}`,
  matching the workflow-level `env` the rest of the file already sets.
- **`core.autocrlf false` / `core.eol lf` set BEFORE checkout.** The hosted
  Windows image ships `autocrlf=true`, which would rewrite the checked-out tree
  to CRLF; this job would then be reading different *bytes* than the Linux jobs
  and any difference in outcome would be unattributable. The line-ending
  behaviour under test is what the build toolset writes into its own temp files,
  not what git checked out — so pinning the checkout to LF costs no coverage.
- **No `-x`.** The Linux `test` job stops at the first failure because it is a
  merge gate; this job exists to *characterise* Windows behaviour, so it reports
  every failure in one run.
- **Explicit file list, not a glob.** A glob that matches nothing silently
  shrinks a gate — the exact failure mode this job was created to stop.
- **Non-required, and still able to go red.** `Test (Windows)` is deliberately
  absent from the branch-protection required checks (Lint, Test, Security Scan,
  Helm Lint), so a Windows-only flake cannot block a merge while the job's own
  stability is characterised. `continue-on-error` is **not** used: a job that
  cannot go red is not a gate, it is decoration. Promote by adding the check to
  branch protection; nothing in the workflow needs to change.

### 2. `tests/test_toolset_portability.py` (new, 69 tests)

Pins two invariants for `tools/genesis/rubric_build_tools.py`, each checked
**twice** — statically against the source (so an unreachable branch cannot hide)
and at runtime against every handler (so a helper added later is covered without
anyone remembering to update a list):

1. **`shell=False`, list argv.** `shell=True` hands the command line to
   `cmd.exe` on Windows and `/bin/sh` on POSIX; the two disagree about quoting,
   `&&`, globbing, and paths with spaces.
2. **No POSIX-only binary.** `grep`/`sed`/`sh` do not exist on a stock Windows
   host, and `find.exe`/`sort.exe` exist there as unrelated programs.

Notable tests:

- `test_source_names_no_posix_only_executable` resolves one level of
  indirection (`cmd = ["git", "diff"]` passed by name) and checks **argv[0]
  only** — `"diff"` in position 1 is a git subcommand, and a rule that flagged
  it is a rule people learn to work around.
- `test_shell_metacharacters_are_inert_not_interpreted` runs
  `python tools/../probe.py && python tools/../evil.py`, asserts the *first*
  half really executed and the second left no trace — so the absent trace means
  "not interpreted", not "nothing ran".
- `test_every_tool_has_a_portability_input` is a forcing function: adding a tool
  to the toolset without adding it to `_TOOL_INPUTS` fails.
- Both the `tools/` and `icdev/tools/` copies are scanned. A gate that reads one
  passes while the shipped copy drifts.

### 3. Linux runs the same two files

`tests/genesis/test_rubric_build_tools.py` and
`tests/test_toolset_portability.py` were added to the Linux `test` job
allowlist. The pairing is the point: a portability claim proven on one OS is not
proven, and it is what makes the Windows job *interpretable* — when the newline
fix regresses, Linux stays green on the very same files while Windows goes red,
and that contrast is the evidence the defect is Windows-only rather than a
broken test. ~2s combined, pure Python, no DB/LLM/network.

### 4. A pre-existing Linux-only defect, fixed

`test_run_command_runs_in_the_worktree` ran `python tools/../marker.py` without
creating `tools/`. Windows collapses `tools/..` **lexically**; POSIX **walks**
it, so a missing directory is `ENOENT`. The test passed on Windows and failed on
Linux — the mirror image of the bug this file was written for. It never showed
up because the Linux `test` job did not run this file. Fixed by creating the
directory, and pinned by
`test_dotdot_in_a_command_path_needs_a_real_directory`, which asserts the
divergence explicitly per-OS.

### 5. What the job caught on its first CI run

`tests/studio/test_workflow_parallel.py::test_human_gate_parks_only_its_own_branch`
failed on `windows-latest` while all nine Linux jobs went green:

```
assert recorder.spans[sibling][0] > gate_start or recorder.overlaps(sibling, "gate")
AssertionError: assert (9775.625 > 9775.625 or False)
```

Two steps that started milliseconds apart carried the **identical** timestamp.
`_Recorder` timed spans with `time.monotonic()`, which on Windows is
`GetTickCount64` — a ~15.6 ms tick (note the `.625`, i.e. a multiple of
1/64 s). `time.perf_counter()` is `QueryPerformanceCounter`, sub-microsecond,
and is monotonic on both platforms. On Linux `monotonic` has ns resolution, so
the flake is invisible there — which is why it sat in a file the Linux `test`
job already runs.

Fixed by switching `_Recorder` to `perf_counter`. This is a root-cause fix, not
a relaxed assertion: `start_b > start_a` still has to hold. Verified with 20
consecutive runs of the failing test on Windows (20/20 pass), the full
`tests/studio/` suite on Windows (190 passed) and `test_workflow_parallel.py`
on Linux (81 passed).

## Verification

Windows = this host. Linux = `python:3.11-slim` in Docker over the same
worktree.

| Check | Windows | Linux |
|---|---|---|
| `test_toolset_portability.py` + `test_rubric_build_tools.py` | 95 passed | 95 passed |
| Full `test-windows` job subset (501 tests) | 501 passed, 124s | — |
| Coherence fast tier (changed files) | exit 0 | — |
| `ruff check tests/ --select E,F,W` | clean | — |

**Acceptance criterion 3 — the asymmetry, demonstrated.** `_write_text` in both
copies of `rubric_build_tools.py` was reverted to `path.write_text(text,
encoding="utf-8")` (i.e. the `hgx-exec-01` fix removed), and the identical tree
was run on both OSes:

```
Windows : 6 failed, 89 passed
          test_write_read_roundtrip
          test_write_file_does_not_translate_newlines
          test_patch_preserves_lf_endings
          test_patch_preserves_crlf_endings
          test_patch_produces_a_one_line_git_diff[lf]
          test_patch_produces_a_one_line_git_diff[crlf]
              -> "one edit produced 40 insertions / 40 deletions"
Linux   : 95 passed
```

The fix was then restored (`git checkout --`) and both copies re-verified.

**Mutation sensitivity of the new gate.** Three separate portability violations
injected into `tools/genesis/rubric_build_tools.py`, each run against
`test_toolset_portability.py` alone:

| Injected | Result |
|---|---|
| `shell=True` on the `git diff` call | 2 failed |
| `cmd = "git diff"` (string argv) | 4 failed |
| `cmd = ["grep", "diff"]` | 2 failed |

Source restored and re-verified clean after each.

## Files

- `.github/workflows/icdev-ci.yml` — new `test-windows` job; two files added to
  the Linux `test` allowlist.
- `tests/test_toolset_portability.py` — new.
- `tests/genesis/test_rubric_build_tools.py` — `tools/` mkdir fix.
- `tests/studio/test_workflow_parallel.py` — `monotonic` → `perf_counter` in
  `_Recorder` (the Windows-only failure the new job caught).
- `docs/features/phase-hgx-port-02-windows-ci-tier.md` — this document.

No `tools/` module changed, so no `icdev/tools/` mirror update is required.

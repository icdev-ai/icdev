# CUI // SP-CTI

# Executor parity: the owned agent loop measured against `claude_cli`

**Task:** hgx-exec-04 · **Card:** HGX — Harness Agent Parity and Graph Runtime
**Status:** measured. **Nothing was flipped.**

## Why this exists

ICDEV ships two executors that can build a kanban task.

`claude_cli` is the primary one and has run essentially every autonomous build
this repo has ever produced. The other — `_dispatch_via_rubric_loop`, reached
through the `local_agent` AgentAdapter — is the *owned* path: the rubric-gated
agent loop with a worktree toolset, graded by the real delivery pipeline. It
shipped behind `KANBAN_RUBRIC_LOOP` (default OFF), is not a supported value in
the executor `fallback_chain`, and was documented nowhere outside two test
reports. It had 13 unit tests proving it is *wired* and zero evidence that it
can *build* anything.

That is a bad position to be in. The owned executor is the answer to "what
happens when the vendor CLI is absent" — an air-gapped install, a host without
the CLI, a provider change — and nobody knew whether the answer worked. This
document is the measurement.

**It is not a recommendation to switch.** `claude_cli` stays primary,
`KANBAN_RUBRIC_LOOP` stays off, and `args/strategos_config.yaml`'s
`fallback_chain` is byte-unchanged. Two tests in
`tests/genesis/test_executor_parity.py` enforce exactly that, because an
acceptance criterion that only exists in prose is a criterion that erodes.

## How the benchmark works

`tools/workflow/executor_parity.py`, corpus in `args/executor_parity_corpus.yaml`.

Ten already-merged kanban tasks with self-contained diffs (2–4 files, ≤180
inserted lines, no migration, no new page). For each (task, executor) pair:

1. `git worktree add --detach` a disposable tree at the task's `base_commit` —
   the repo exactly as it stood immediately *before* the human fix landed.
   Detached deliberately: with no branch and no upstream there is nothing for a
   stray `git push` to push to, which is a stronger guarantee than the prompt's
   prohibition on pushing.
2. Invoke the executor through the `tools/agents` **AgentAdapter** seam with an
   identical `AgentSession` — same prompt, same system prompt, same
   `max_turns`, same timeout. Neither executor gets a bespoke harness; they are
   two implementations of one interface.
3. Grade the resulting tree with
   `tools/workflow/pipeline_grader.make_pipeline_grader` — ruff/bandit,
   coherence, and pytest on the changed files. **This harness computes the
   grade for both executors**, so the verdict never depends on what an executor
   says about itself.
4. Record verdict, wall-clock, cost, tokens and the changed-file set. Destroy
   the worktree.

The prompts are authored from each commit's own message — the problem it
states, never the patch it applied. A test asserts no prompt contains the
reference SHA or diff markers; a prompt that names the patch measures reading
comprehension rather than building.

Two rates are reported per executor and they are deliberately not the same
number:

| | |
|---|---|
| `gate_pass_rate` | this harness's independent verdict on the tree |
| `self_report_rate` | what the executor claimed (`AgentResult.completed`) |

The gap between them is the interesting result. An executor that reports
success on a tree the gates reject is worse than one that fails honestly,
because the kanban runner acts on the claim.

### Two harness decisions worth knowing about

**Changed files are computed against the replay base, including uncommitted and
untracked work.** Neither executor commits, so the commit-range form
(`base...HEAD`) reports an empty set — and every delivery gate is *scoped to the
changed-file set*, so an empty set makes every gate vacuously green. The
harness diffs the working tree against `base_commit` and adds untracked files.
A run that changed nothing is additionally forced to `no_op` rather than being
allowed to inherit a vacuous pass. See
[the finding below](#finding-the-owned-executors-own-grader-can-see-an-empty-diff).

**Conformance review is off.** `make_pipeline_grader` can also ask an LLM "was
this built to the acceptance criteria?". Leaving it on would make the published
gate-pass rate depend on a model's judgement and on a reachable kanban DB. The
mechanical gates are the reproducible part, so they are what is published;
`--conformance` turns the review back on for anyone who wants it.

## Reproducing it

```bash
# what is in the corpus
python -m tools.workflow.executor_parity --list

# resolve corpus, adapters and base commits without building anything
python -m tools.workflow.executor_parity --dry-run

# the full run that produced the numbers below
python -m tools.workflow.executor_parity --run \
  --timeout 480 --max-turns 25 \
  --out .tmp/parity.json --report .tmp/parity.md
```

Runs on Windows and Linux: `pathlib` throughout, repo root resolved from
`__file__` (never `os.getcwd()` — the harness runs from disposable worktrees),
fixed-argv `subprocess` with `shell=False`, `shutil.which` for git,
`encoding="utf-8"` + `newline=""` on every file read and write, and a two-sided
worktree-removal path because Windows holds file locks that POSIX does not.
Threads, not asyncio (D36). No model id appears anywhere in the harness — the
executor is chosen by *adapter name*, and every model call happens inside an
adapter that routes by `llm_function` through `LLMRouter`.

<!-- RESULTS:BEGIN -->
<!-- RESULTS:END -->

## Reading the numbers

<!-- ANALYSIS:BEGIN -->
<!-- ANALYSIS:END -->

## Finding: the owned executor's own grader can see an empty diff

Independent of the corpus results, building the harness surfaced a defect in
the owned path.

`local_agent` (and `_dispatch_via_rubric_loop`, which shares the shape) builds
its rubric's changed-file list with:

```python
_git_changed_files("origin/main", False, Path(work_dir))
```

which is `git diff --name-only origin/main...HEAD` — **committed** changes only.
The agent loop edits the working tree and does not commit; the kanban runner
commits *after* the task. So during grading the list is empty, every gate is
scoped to nothing, and `make_pipeline_grader` returns `satisfied` on a tree it
never actually looked at. The executor then reports `completed=True`.

This is the same defect class as the `[source: …]` provenance gate that recorded
`warn` for a programming error: a check that cannot fail is indistinguishable
from a check that passed.

It is **not fixed here**, on purpose. Changing the executor mid-benchmark would
invalidate the numbers this task exists to produce, and this task's mandate is
measurement. The harness works around it for its own scoring (working-tree diff
plus untracked files, and an explicit `no_op` verdict), so the published
gate-pass rate is unaffected. The fix belongs to a follow-up card against
`tools/agents/adapters/local_agent.py::_changed_files_thunk` and
`tools/genesis/reflexes/kanban.py::_dispatch_via_rubric_loop._changed`.

## What was changed in the tree

| File | Why |
|---|---|
| `args/executor_parity_corpus.yaml` | the frozen 10-task corpus |
| `tools/workflow/executor_parity.py` (+ `icdev/` mirror) | the harness |
| `tools/agents/adapters/claude_cli.py` (+ `icdev/` mirror) | `--output-format json` so the CLI's cost, tokens and turn count reach `AgentResult.structured`; without it the cost column for the primary executor is permanently empty. `output` is still the assistant's text and an unparseable envelope degrades to plain text, so existing callers see no change. |
| `tests/genesis/test_executor_parity.py` | harness tests + the two no-flip guards |
| `tools/manifest/agent-adapters.md`, `docs/reference/commands.md` | registration |

Not changed, and tested to stay that way: `args/strategos_config.yaml`,
`KANBAN_RUBRIC_LOOP` (which is on the harness's `.env` import denylist so the
benchmark cannot set it even by accident), and the kanban dispatch path — the
benchmark talks to the adapters directly and never goes through the runner.

## The decision this does not make

hgx-exec-04's mandate ends at the data. Flipping the chain is a separate human
decision, and it is additionally blocked on **hgx-guard-02**: promoting the
owned executor before headless guardrail parity would move autonomous builds
onto the weaker guardrail set. Re-run the benchmark after that lands, against
the same corpus, and compare.

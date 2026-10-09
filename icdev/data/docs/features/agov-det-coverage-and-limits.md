# AGOV / DET — Coverage and Limits

**CUI // SP-CTI**

> **A finding is a RULE MATCH AND NOT PROOF OF EXECUTION.**
>
> A finding records that the platform observed a pattern in the agent event
> stream. It does not establish that a command ran, that it ran successfully,
> that it had the effect the rule title names, or that the agent intended it.
> Nothing in AGOV/DET performs post-hoc verification, and nothing in the event
> stream carries a result the detector reads. Treat every finding as a lead for
> a human, never as an adjudicated fact.

This document is the counterweight to a documented failure mode in this repo:
artifacts that overstate completion. It exists to say what AGOV/DET does *not*
see, in enough detail that an operator can tell the difference between "clean"
and "blind". Everything below was measured against the live PostgreSQL backend
on 2026-08-09, not inferred from the code.

Related: [`args/agent_rules/README.md`](../../args/agent_rules/README.md) (rule
schema), [`args/agent_rules_enforce/README.md`](../../args/agent_rules_enforce/README.md)
(enforcement opt-in), [`docs/security/sandbox-coverage.md`](../security/sandbox-coverage.md)
(Gap 34, the gate's ingress analysis).

---

## 1. What AGOV/DET is

A declarative rule engine over agent activity ICDEV **already** records. It adds
no event table and issues no write outside the append-only `agent_findings`
table. A rule is one YAML file with structured matchers — not an expression
language — and is **monitor-only** unless an operator copies it into
`args/agent_rules_enforce/` and sets `enforce: true`. The shipped pack sets
`enforce: false` on all 14 rules.

There are **two evaluation paths**, and they have very different fidelity. The
distinction below is the single most important thing in this document.

| Path | Where | Input | Sees operands? |
|------|-------|-------|----------------|
| **Live gate** | `tools/agent_detect/gate.py`, reached from `.claude/hooks/pre_tool_use.py` and `tools/airgap/hook_compat.py` | the tool call's own `tool_input`, in memory, before the call runs | **Yes** — `command`, `file_path`, `url` are present |
| **Historical scan** | `tools/agent_detect/cli.py --scan --session <id>` | rows already persisted in the five source tables | **Today, no** — see §3 |

---

## 2. Sources → event types

`tools/agent_detect/events.py` projects five existing tables into one closed
event vocabulary: `command.exec`, `file.read`, `file.write`, `file.delete`,
`network.indicator`, `tool.call`. `tool.call` is the honest fallback — it
asserts only that a tool was invoked.

Confidence is a **named level, not a score**:

| Level | Definition |
|-------|-----------|
| `direct` | The tool is known by name and the operand came from that tool's own documented input field (`Bash.command`, `Read.file_path`, `WebFetch.url`) |
| `derived` | The tool was recognised through the shared `command_tools` generic-executor list in `args/agent_approval_policy.yaml`, not an exact entry |
| `declared` | The row names a tool, agent or audited action and nothing more — no operand, so no promotion beyond `tool.call` |

### Per-source fidelity

Measured with `fetch_events(sources=[s], limit=300)` against the live database
on 2026-08-09. "Observed" is what the projection actually produced; "ceiling" is
the best that source could produce if its writers recorded operands.

| Source table | Writer | Ceiling (what the schema could support) | **Observed 2026-08-09** | Why the gap |
|---|---|---|---|---|
| `hook_events` | `.claude/hooks/*.py`, `tools/airgap/hook_compat.py` | `direct` — `tool_name` plus a payload that could carry `command`/`file_path`/`url` | 300/300 `tool.call` / `declared` | The persisted `payload` for `post_tool_use` and `stop` rows is a re-serialisation of the row envelope (`id`, `session_id`, `hook_type`, `tool_name`, `payload`, `classification`, `created_at`) whose inner `payload` is `null`. Operand keys are absent. Only `pre_tool_use` **block** rows carry content, and it is `{blocked, reason, command_snippet, rule}` — `command_snippet` is deliberately *not* in `OPERAND_KEYS`, so it is not read as an operand (see §5, invariant (a)) |
| `agent_executions` | `tools/agent_runtime/` | `declared` — an execution record, not a tool call | 5/5 `tool.call` / `declared` | By design. This table records that an agent ran, not what it touched |
| `ai_telemetry` | LLM router / gateway | `declared` — model, tokens, latency | 300/300 `tool.call` / `declared` | By design. Telemetry is about the model call, not the filesystem |
| `audit_trail` | platform-wide, hash-chained from migration 149 | `declared` — actor and action name | 300/300 `tool.call` / `declared` | By design. The narrative columns are free text and are refused as operands |
| `ace_audit_log` | ACE co-worker engine | `declared` — coworker action | 300/300 `tool.call` / `declared` | By design |

**Read that table honestly: on this database, zero of the 1,205 sampled stored
events carried an operand.** Every rule in the shipped pack requires one
(`event_type: command.exec`, `file_path_glob`, `command_name`, `command_matches`,
`url_matches`, `argv_contains`). Therefore:

> **`--scan` over the stored history currently cannot fire any shipped rule.**
> A clean `--scan` today means "the stored rows carry nothing to match", not
> "this session was clean". Detection that actually fires happens on the **live
> gate path**, where the tool payload is in hand.

This is not a defect introduced by AGOV; it is the pre-existing shape of
`hook_events`, and AGOV is the first consumer to make it visible. Closing it
means changing what the hooks persist, which is a schema and privacy decision
outside this epic's scope. Until then, `--scan` is a working evaluator pointed
at an under-populated stream, and this document is the place that says so.

---

## 3. What is known-missing

Ordered by how much it would mislead an operator who did not know.

1. **No post-hoc proof that anything executed.** The gate runs *before* the tool
   call. Nothing re-checks afterwards whether the command ran, exited zero, or
   did what the rule name says. A `command.exec` event means a shell command was
   *requested*.
2. **The stored stream carries no operands (§2).** `--scan` is structurally
   sound and currently blind. Do not report a clean scan as evidence.
3. **No coverage of activity outside the hook path.** Anything that does not
   pass through `pre_tool_use` / `hook_compat` is invisible: a subprocess spawned
   *by* an already-approved command, a background daemon, a cron entry, another
   session's shell, direct database access from outside the platform, and any
   action taken by a human at a terminal. The gate sees tool calls, not syscalls.
4. **A command that does not parse does not fire a parsed-view rule.**
   `command_name` and `argv_contains` read `tools/agent_detect/shell_parse.py`
   and deliberately do **not** fall back to substring matching — that fallback is
   the documented fail-open at `args/agent_approval_policy.yaml`:107-126. Command
   substitution, parameter expansion, `&&`/`;`/`&`, subshells, heredocs,
   `eval`/`source`/`exec`, PowerShell and `cmd` all yield `parsed=False`. **A
   parse refusal is not a safety verdict** — it means the parser declined. This
   is a deliberate false-negative trade: a rule that cannot be satisfied by a
   word inside an unrelated argument is worth the commands it skips.
5. **Destination negations are inert when the URL is not known.** Rules like
   `exfil.http_upload_external` carry `not_url_matches` for loopback. A
   `command.exec` event usually has no `url` field, so the negation passes
   vacuously and the rule fires without having seen the destination. That is why
   those rules are `medium`, not `high`.
6. **Chains are bounded and non-overlapping.** A `sequence` rule matches 2–8
   steps within one partition (`session_id`, `agent`, `project_id`, `source`) and
   defaults to `max_matches: 1`. A chain spanning two sessions, two agents or two
   sources is not detected. Steps must appear in order; the same event is not
   reused across two matches.
7. **The live gate reads a bounded session trail, not the database.** For
   latency, `gate.py` keeps the last 256 events per session in a file trail
   (`TRAIL_MAX_EVENTS`). A chain whose first step fell off the end of that trail
   will not complete. Sessions longer than the trail have partial chain coverage.
8. **The gate fails OPEN.** Every internal error in the detection path allows
   the tool call. `ICDEV_AGENT_DETECT=0` removes it entirely. This is the correct
   trade for a monitor-only system on a latency-critical hook, and it means an
   absent finding is never evidence of absence.
9. **The rule pack is a starting point, not a taxonomy.** 14 rules across
   `secrets`, `exfil`, `persistence`, `tamper` and `chains`. There is no coverage
   of privilege escalation, lateral movement, container escape, model-level
   attacks (prompt injection, tool poisoning), or supply-chain tampering —
   artifact scanning is SIPA's job (`tools/integrity/`), not this engine's.
10. **Findings have no lifecycle.** `agent_findings` is append-only; there is no
    triage state, no suppression list and no false-positive marking. Re-running
    `--scan --record` over the same events does not duplicate (the `finding_id`
    is deterministic over rule, version, session and ordered event ids), but
    nothing dismisses a finding either.
11. **Fixture tests prove the matcher, not the coverage.** `--test` evaluates
    synthetic events with declared expectations. Green means the rules behave as
    their authors declared. It says nothing about whether the pack would catch a
    real attacker, and nothing about the stored stream.

---

## 4. What it does establish

Stated plainly, so the limits above are not read as "this is worthless":

- On the **live gate path**, a match means the platform observed a tool call
  whose *own structured input* satisfied a named, versioned, reviewable rule —
  and it recorded that observation in an append-only table before the call ran.
- Chains express something ICDEV could not express at all before: every prior
  check was single-action, so "read a credential, then send data out" was
  invisible because each half is individually unremarkable.
- `command_name` / `argv_contains` match a *parsed* command, which cannot be
  satisfied by a word sitting inside an unrelated argument — the cause-level fix
  for the fail-open at `args/agent_approval_policy.yaml`:107-126.
- Enforcement authority is a **directory**, not a field: flipping `enforce: true`
  on a file in the shipped pack is inert. `tests/test_agov_gate.py` pins this.

---

## 5. Honesty invariants enforced in code

Not conventions — each raises or rejects.

| Invariant | Enforced by | Prevents |
|---|---|---|
| (a) classification never reads free text | `events._structured()` **raises** on any key in `FREE_TEXT_KEYS` (`output_summary`, `message`, `details`, `content`, `stdout`, `stderr`, …) | A command appearing in tool *output*, an attacker-supplied file body, or audit narrative being read as evidence that an action happened |
| (b) a promoted event carries the operand that justified it | `AgentEvent.__post_init__` rejects `command.exec` without a `command`, `file.*` without a `file_path`, `network.indicator` without a `url` | "Promote first, hope the operand shows up" — an ambiguous row stays `tool.call` |
| (c) a broken rule is inert, never match-all | `rules.compile_rule` skips the WHOLE rule into `RuleSet.errors`; `rules.match_event` and `sequence.builtin_match_event` fail closed on an unknown key | A dropped clause silently loosening the surviving AND into a match-everything rule |
| (d) a scan records observations, never denials | `cli._record_one` pins `decision="observed"`, `enforced=False` | An after-the-fact match being written to an append-only table as though something was blocked |
| (e) an empty fixture run is a failure | `cli.cmd_test` exits non-zero on zero cases | A green `--test` that evaluated nothing reading as a pass |

---

## 6. Operator commands

```bash
python tools/agent_detect/cli.py --list --json                        # catalog loaded rules
python tools/agent_detect/cli.py --check --json                       # validate the shipped pack (exit 1 on any invalid rule)
python tools/agent_detect/cli.py --check --rules-dir args/agent_rules_enforce --json
python tools/agent_detect/cli.py --test --json                        # fixtures (exit 1 on mismatch)
python tools/agent_detect/cli.py --scan --session <session_id> --json  # read-only
python tools/agent_detect/cli.py --scan --session <session_id> --record --json
python tools/agent_detect/events.py --session <session_id> --summary --json  # what the stream actually holds
```

Run `--check` before copying any rule into `args/agent_rules_enforce/`. An
invalid rule is inert, so the exit code is the only signal that an enforcement
directory is not doing what its author thinks it is.

**Exit codes:** `0` completed and every check passed · `1` a check failed
(invalid rule, fixture mismatch) · `2` usage error or the verb could not run.
